import os
import sys
import json
import copy

before = sys.argv[1]
after = sys.argv[2]

with open(before, 'r', encoding='utf-8') as fin:
    before_data = json.load(fin)

with open(after, 'r', encoding='utf-8') as fin:
    after_data = json.load(fin)



diff_map = {
    'missing': {
        'topics': [],
        "nodes": [],
        "services": [],
        'param': {},
        'connections': [],
    },
    'add': {
        'topics': [],
        "nodes": [],
        "services": [],
        'param': {},
        'connections': [],
    },
    'change': {
        'topics': [],
        "nodes": [],
        "services": [],
        'param': {},
        'connections': [],
    },
}

keys = [
    'nodes',
    'topics',
    'services',
    'connections',
]

for key in keys:
    before = before_data[key]
    after = after_data[key]

    if key in ['nodes', 'topics', 'services']:
        before_names = before.keys()
        after_names = after.keys()
    else:
        before_names = [
            f"{item['source_id']} -> {item['target_id']}({item['type']}:{item['direction']})"
            for item in before
        ]
        after_names = [
            f"{item['source_id']} -> {item['target_id']}({item['type']}:{item['direction']})"
            for item in after
        ]

    for idx_before, b_n in enumerate(before_names):

        if not b_n in after_names:
            diff_map['missing'][key].append(b_n)
            if key == 'nodes':
                params = before[b_n]['parameters']
                for p in params:
                    # diff_map['missing']['param'].append(f"[{b_n}]: {p['name']}")
                    if not b_n in diff_map['missing']['param']:
                        diff_map['missing']['param'][b_n] = []
                    diff_map['missing']['param'][b_n].append(p)
        else:
            if key == 'nodes':
                before_params = before[b_n]['parameters']
                before_param_names = [b_p['name'] for b_p in before_params]
                after_params = after[b_n]['parameters']
                after_param_names = [b_p['name'] for b_p in after_params]
                for b_p_name in before_param_names:
                    b_p = list(
                        filter(lambda x: x['name'] == b_p_name,
                               before_params))[0]
                    if not b_p_name in after_param_names:
                        # diff_map['missing']['param'].append(f"[{b_n}]: {b_p_name}")
                        if not b_p_name in diff_map['missing']['param']:
                            diff_map['missing']['param'][b_n] = []
                        diff_map['missing']['param'][b_n].append(b_p)
                    else:
                        a_p = list(
                            filter(lambda x: x['name'] == b_p_name,
                                   after_params))[0]
                        if b_p['value'] != a_p['value']:
                            # diff_map['change']['param'].append(f"[{b_n}: {b_p_name}] {b_p['value']} -> {a_p['value']}")
                            if not b_n in diff_map['change']['param']:
                                diff_map['change']['param'][b_n] = []
                            diff_map['change']['param'][b_n].append({"name": b_p_name, "before": b_p['value'], "after": a_p['value'], "type": b_p['type']})
                for a_p_name in after_param_names:
                    if not a_p_name in before_param_names:
                        # diff_map['add']['param'].append(f"[{b_n}]: {a_p_name}")
                        if not a_p_name in diff_map['add']['param']:
                            diff_map['add']['param'][b_n] = []
                        diff_map['add']['param'][b_n].append(b_p)
    for idx_after, a_n in enumerate(after_names):

        if not a_n in before_names:
            diff_map['add'][key].append(a_n)
            if key == 'nodes':
                params = after[a_n]['parameters']
                for p in params:
                    # diff_map['add']['param'].append(f"[{a_n}]: {p['name']}")
                    if not a_n in diff_map['add']['param']:
                        diff_map['add']['param'][a_n] = []
                    diff_map['add']['param'][a_n].append(p)


with open("./diff_result.json", 'w', encoding='utf-8') as fout:
    json.dump(diff_map, fout, indent=2)
