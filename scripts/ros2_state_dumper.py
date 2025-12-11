import json
import subprocess
import sys
import time
import os
import re
import yaml
from typing import Dict, List, Any, Set

IGNORE_SERVICE_NAMES = [
    'describe_parameters',
    'get_parameter_types',
    'get_parameters',
    'list_parameters',
    'set_parameters',
    'set_parameters_atomically',
]

IGNORE_TOPIC_NAMES = [
    'parameter_events',
]

# param dumpでタイムアウトする可能性のあるノード名パターン
IGNORE_NODE_PATTERNS = [
    r'.*_impl.*',  # 内部実装ノード（例: transform_listener_impl, transform_listener_impl_6315ad640800）
    r'/_ros2cli_.*',  # ROS 2 CLIの一時ノード
]


def run_ros2_command(cmd_list: List[str], timeout: int = 10) -> str | None:
    """
    ROS 2のCLIコマンドを実行し、標準出力を取得するヘルパー関数。
    """
    try:
        env = os.environ.copy()
        result = subprocess.run(
            cmd_list,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # interface showのパースエラーは簡潔に表示
        if "interface" in cmd_list and "show" in cmd_list:
            interface_type = cmd_list[-1] if len(cmd_list) > 0 else "unknown"
            print(f"Warning: Failed to get schema for {interface_type}", file=sys.stderr)
        elif "Node not found" not in e.stderr:
            print(f"Error running ROS 2 command: {' '.join(cmd_list)}", file=sys.stderr)
            print(f"Stderr: {e.stderr}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"ROS 2 command not found. Ensure ROS 2 environment is sourced.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"ROS 2 command timed out: {' '.join(cmd_list)}", file=sys.stderr)
        return None

def should_skip_node_param_dump(node_name: str) -> bool:
    """
    ノード名がparam dumpをスキップすべきパターンに一致するかチェック。
    """
    for pattern in IGNORE_NODE_PATTERNS:
        if re.match(pattern, node_name):
            return True
    return False


def check_node_param_service_available(node_name: str) -> bool:
    """
    ノードのパラメータサービスが応答可能かを短いタイムアウトでチェック。
    param listコマンドを5秒のタイムアウトで実行し、正常に応答するか確認する。
    """
    result = run_ros2_command(["ros2", "param", "list", node_name], timeout=5)
    if result is None:
        return False
    # "Exception while calling service"のようなエラーがstderrに出ている場合もあるが、
    # run_ros2_commandはstdoutのみを返すので、結果がNoneならエラーと判断
    return True


def get_python_type_name(value: Any) -> str:
    """
    Python値から型名を取得する。
    """
    if isinstance(value, bool):
        return "boolean"
    elif isinstance(value, int):
        return "integer"
    elif isinstance(value, float):
        return "double"
    elif isinstance(value, str):
        return "string"
    elif isinstance(value, list):
        return "array"
    else:
        return "unknown"


def flatten_dict(d: Dict[str, Any], parent_key: str = '') -> List[tuple[str, Any]]:
    """
    ネストされた辞書をドット記法でフラット化する。
    
    例: {'a': {'b': 1, 'c': 2}} -> [('a.b', 1), ('a.c', 2)]
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key))
        else:
            # Noneは空文字列に変換
            if v is None:
                v = ""
            items.append((new_key, v))
    return items


def parse_param_dump_output(output: str, node_name: str) -> List[Dict[str, Any]]:
    """
    'ros2 param dump' の出力 (YAML形式) をパースし、パラメータリストを返す。
    ネストされた辞書構造はドット記法で展開される。
    """
    parameters: List[Dict[str, Any]] = []

    try:
        data = yaml.safe_load(output)
        if data is None:
            return parameters

        # ros2 param dump の出力形式: {node_name: {ros__parameters: {param1: value1, ...}}}
        if node_name in data:
            node_params = data[node_name]
        else:
            # ノード名がキーにない場合、直接ros__parametersがある可能性
            node_params = data

        if isinstance(node_params, dict) and 'ros__parameters' in node_params:
            ros_params = node_params['ros__parameters']
            if isinstance(ros_params, dict):
                # パラメータをフラット化
                flattened = flatten_dict(ros_params)
                for param_name, param_value in flattened:
                    # Noneは空文字列として扱う
                    if param_value is None:
                        param_value = ""
                    param_type = get_python_type_name(param_value)
                    param_value_str = str(param_value)
                    parameters.append({
                        "name": param_name,
                        "value": param_value_str,
                        "type": param_type
                    })
    except yaml.YAMLError as e:
        print(f"Error parsing YAML for node {node_name}: {e}", file=sys.stderr)

    return parameters


def get_component_info() -> tuple[Set[str], Set[str]]:
    """
    'ros2 component list' を実行し、コンテナ名とコンポーネントノード名のセットを返す。

    Returns:
        tuple[Set[str], Set[str]]: (コンテナ名のセット, コンポーネントノード名のセット)
    """
    container_nodes: Set[str] = set()
    component_nodes: Set[str] = set()

    # コンポーネントコンテナのリストを取得
    component_list_raw = run_ros2_command(["ros2", "component", "list"])

    if component_list_raw:
        for line in component_list_raw.split('\n'):
            line = line.rstrip()
            if not line:
                continue

            # コンテナ名の行 (先頭がスペースでない)
            if not line.startswith(' '):
                container_name = line.strip()
                container_nodes.add(container_name)
                continue

            # コンポーネントの行 (先頭がスペース)
            # 形式: "  1  /namespace/node_name (package::ClassName)"
            match = re.match(r'\s+\d+\s+(/[\w/]+)', line)
            if match:
                node_name = match.group(1)
                component_nodes.add(node_name)

    return container_nodes, component_nodes

def parse_interface_schema(interface_type: str) -> List[str]:
    """
    'ros2 interface show' コマンドを実行し、出力を行ごとにパースしてリストで返す。
    """
    output = run_ros2_command(["ros2", "interface", "show", interface_type])
    schema: List[str] = []

    if output:
        for line in output.split('\n'):
            line = line.rstrip()
            trimmed_line_start = line.lstrip()

            # 空行、#コメント、//コメントをスキップ
            if not trimmed_line_start or trimmed_line_start.startswith('#') or trimmed_line_start.startswith('//'):
                continue

            schema.append(line)

    return schema


def collect_ros2_graph_data() -> Dict[str, Any]:
    """
    ROS 2のノード、トピック、サービス情報をCLI経由で収集し、JSON構造を構築する。
    """

    # 時間計測の開始
    start_time = time.time()

    graph_data: Dict[str, Any] = {
        "graph_metadata": {
            "created_at": int(time.time() * 1000),
            "description": "Captured ROS 2 network graph using subprocess"
        },
        "nodes": {},
        "topics": {},
        "services": {},
        "actions": {},
        "connections": []
    }

    node_name_to_id: Dict[str, str] = {}

    # 1. ノードリストの取得と総数カウント
    node_list_raw = run_ros2_command(["ros2", "node", "list", "-a"])
    node_list_all = [n.strip() for n in (node_list_raw.split('\n') if node_list_raw else []) if n.strip()]

    # コンポーネント情報の取得
    print("Getting component info...", file=sys.stderr)
    container_nodes, component_nodes = get_component_info()
    print(f"Found {len(container_nodes)} container nodes, {len(component_nodes)} component nodes", file=sys.stderr)

    node_list_filtered = []
    for n in node_list_all:
        if not n.startswith('/_ros2cli_') and n != '/ros2_state_yaml_dumper_node':
            node_list_filtered.append(n)
    
    # componentノードを追加（重複を避ける）
    for comp_node in component_nodes:
        if comp_node not in node_list_filtered:
            node_list_filtered.append(comp_node)

    total_nodes = len(node_list_filtered)

    # 2. トピックとサービスのリストを事前に取得 (接続情報構築のため)
    topic_list_raw = run_ros2_command(["ros2", "topic", "list", "-t"])
    service_list_raw = run_ros2_command(["ros2", "service", "list", "-t"])

    discovered_topics: Dict[str, str] = {} # name -> type
    discovered_services: Dict[str, str] = {} # name -> type

    if topic_list_raw:
        for line in topic_list_raw.split('\n'):
            match = re.match(r'(/[\w/]+)\s+\[([\w/]+)\]', line.strip())
            if match:
                name = match.group(1)
                last_topic_name = name.split('/')[-1]
                if not last_topic_name in IGNORE_TOPIC_NAMES:
                    type_name = match.group(2)
                    discovered_topics[name] = type_name

    if service_list_raw:
        for line in service_list_raw.split('\n'):
            match = re.match(r'(/[\w/]+)\s+\[([\w/]+)\]', line.strip())
            if match:
                name = match.group(1)
                last_service_name = name.split('/')[-1]
                if not last_service_name in IGNORE_SERVICE_NAMES:
                    type_name = match.group(2)
                    discovered_services[name] = type_name

    # 3. ノードごとの情報と接続の収集
    for i, node_name in enumerate(node_list_filtered):
        # 進捗情報の出力
        processed_nodes = i + 1
        progress = (processed_nodes / total_nodes) * 100
        print(f"Processing node {processed_nodes}/{total_nodes} ({progress:.0f}%): {node_name}", file=sys.stderr)

        node_id = f"node_{i}"
        node_name_to_id[node_name] = node_id

        # パス構築
        path_parts = [p for p in node_name.split('/') if p]

        # ノードタイプの判定
        if node_name in container_nodes:
            node_type = "container"
        elif node_name in component_nodes:
            node_type = "component"
        else:
            node_type = "node"

        # パラメータの取得 (ros2 param dump を使用)
        # コンテナノードや特定パターンのノードはparam dumpでタイムアウトするためスキップ
        parameters: List[Dict[str, Any]] = []
        if node_name in container_nodes:
            print(f"  Skipping param dump for container node: {node_name}", file=sys.stderr)
        elif should_skip_node_param_dump(node_name):
            print(f"  Skipping param dump for pattern-matched node: {node_name}", file=sys.stderr)
        elif not check_node_param_service_available(node_name):
            print(f"  Skipping param dump for node with unavailable param service: {node_name}", file=sys.stderr)
        else:
            param_dump_raw = run_ros2_command(["ros2", "param", "dump", node_name, "--include-hidden-nodes"])

            if param_dump_raw:
                parameters = parse_param_dump_output(param_dump_raw, node_name)

        # ノード情報の構築
        graph_data["nodes"][node_name] = {
            "id": node_id,
            "name": node_name,
            "path": path_parts,
            "type": node_type,
            "parameters": parameters
        }

        # 接続情報の収集
        node_info_raw = run_ros2_command(["ros2", "node", "info", node_name])
        if node_info_raw:
            current_section = None
            for line in node_info_raw.split('\n'):
                line = line.strip()
                if line.endswith(':'):
                    current_section = line[:-1]
                    continue
                if not current_section or not line:
                    continue

                name = line.split(':')[0].strip() # トピック/サービス名

                if current_section == 'Publishers' and name in discovered_topics:
                    graph_data["connections"].append({
                        "type": "topic",
                        "source_id": node_name,
                        "target_id": name,
                        "direction": "publish"
                    })
                elif current_section == 'Subscribers' and name in discovered_topics:
                    graph_data["connections"].append({
                        "type": "topic",
                        "source_id": name,
                        "target_id": node_name,
                        "direction": "subscribe"
                    })
                elif current_section == 'Service Servers' and name in discovered_services:
                    graph_data["connections"].append({
                        "type": "service",
                        "source_id": name,
                        "target_id": node_name,
                        "direction": "provide"
                    })
                elif current_section == 'Service Clients' and name in discovered_services:
                    graph_data["connections"].append({
                        "type": "service",
                        "source_id": node_name,
                        "target_id": name,
                        "direction": "call"
                    })

    # 5. トピック情報の構築 (メッセージスキーマの取得)
    for name, type_name in discovered_topics.items():
        graph_data["topics"][name] = {
            "id": name,
            "name": name,
            "type": type_name,
            "message_schema": parse_interface_schema(type_name)
        }

    # 6. サービス情報の構築 (メッセージスキーマの取得)
    for name, type_name in discovered_services.items():
        graph_data["services"][name] = {
            "id": name,
            "name": name,
            "type": type_name,
            "message_schema": parse_interface_schema(type_name)
        }

    # ★ 修正1: 処理終了時間の計測と実行時間の計算
    end_time = time.time()
    duration = end_time - start_time
    print(f"Total execution time (collect_ros2_graph_data): {duration:.3f} seconds", file=sys.stderr)

    return graph_data

def main():
    """
    メイン実行関数。グラフデータを収集し、JSONファイルに保存する。
    """
    if 'AMENT_PREFIX_PATH' not in os.environ:
        print("Warning: ROS 2 environment does not appear to be sourced. Command execution may fail.", file=sys.stderr)

    graph_data = collect_ros2_graph_data()

    # 出力をJSON形式に変更
    file_path = "ros2_graph_dump.json"
    try:
        with open(file_path, "w") as f:
            json.dump(graph_data, f, indent=2)
        print(f"Successfully dumped ROS 2 graph data to {file_path}")
    except IOError as e:
        print(f"Error saving JSON file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()