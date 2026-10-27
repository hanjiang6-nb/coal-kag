"""Render a faithful stage/member hierarchy of frozen clustering results.

Dependency edges describe supporting connections, never class inheritance or
merges between ontologies. The builder reads existing results without modifying
their members, identifiers, weights, or review status.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster_ontology_candidates import read_rows
from graph_zh_glossary import translate_term
from ontology_report_zh import display_metadata, STATUS_LABELS


def build_hierarchy(source):
    source = Path(source)
    profiles = read_rows(source / 'entity_profiles.jsonl')
    attrs = read_rows(source / 'attribute_ontologies.jsonl')
    relations = read_rows(source / 'relation_ontologies.jsonl')
    existing = read_rows(source / 'existing_ontologies.jsonl')
    boundary = read_rows(source / 'boundary_weights.jsonl')
    unassigned = read_rows(source / 'unassigned.jsonl')
    rounds_path = source / 'relation_rounds.jsonl'
    rounds = read_rows(rounds_path) if rounds_path.exists() else []
    ontologies = existing + attrs + relations
    by_id = {p['entity']: p for p in profiles}
    ontology_by_id = {o['id']: o for o in ontologies}
    if len(by_id) != len(profiles) or len(ontology_by_id) != len(ontologies):
        raise ValueError('Duplicate object or ontology identifiers')
    if set(by_id) & set(ontology_by_id):
        raise ValueError('An ontology cannot also be a candidate object')
    values_path = source / 'nodes.jsonl'
    feature_details = {r['id']: r for r in read_rows(values_path)
                       if r['kind'] == 'feature_value'} if values_path.exists() else {}
    names = display_metadata(profiles, ontologies, feature_details)['display_names']
    assigned = {}
    for ontology in ontologies:
        members = ontology['members']
        if not members or len(members) != len(set(members)):
            raise ValueError('An ontology needs nonempty unique members')
        for member in members:
            if member not in by_id or member in assigned:
                raise ValueError('Unknown or multiply assigned ontology member')
            assigned[member] = ontology['id']
    pending = {r['entity']: r for r in unassigned}
    if len(pending) != len(unassigned) or set(pending) & set(assigned):
        raise ValueError('Unassigned objects overlap or repeat')
    if set(pending) | set(assigned) != set(by_id):
        raise ValueError('Hierarchy does not cover every object exactly once')

    def entity_node(key):
        profile = by_id[key]
        node = dict(id=key, label=names[key], kind='entity',
                    original_name=profile['name'], type=translate_term(profile['type']),
                    identity_level=STATUS_LABELS.get(profile['identity_level'], profile['identity_level']),
                    parent_ontology=assigned.get(key),
                    intrinsic_feature_count=len(profile.get('intrinsic_attributes', [])))
        if key in pending:
            node['reason'] = STATUS_LABELS.get(pending[key]['reason'], pending[key]['reason'])
        return node

    def ontology_node(ontology, stage):
        members = ontology['members']
        labels = {names[k] for k in members}
        if stage == 'attributes' and ontology.get('member_type') == 'mineral' and len(labels) == 1:
            assessment = dict(status='candidate', label='同类矿物候选',
                              note='成员名称与矿物类别一致；尚未完成本体语义验收。')
        elif stage == 'attributes':
            assessment = dict(status='conditional', label='共同属性类候选',
                              note='需明确共同概念、上位类与归属条件；共享属性不表示同一实体。')
        elif stage == 'relations':
            assessment = dict(status='pending', label='关系角色候选',
                              note='按连接权重产生，需审核关系类型、方向及概念层级。')
        else:
            assessment = dict(status='existing', label='已有冻结本体',
                              note='保留原编号和成员；本图不改变已有审核状态。')
        identity = ontology.get('member_identity_level', '')
        return dict(id=ontology['id'], kind='ontology', label=names[ontology['id']].split('：', 1)[-1],
                    original_name=ontology['label'], stage=stage, round=ontology.get('round'),
                    member_count=len(members), members=list(members), assessment=assessment,
                    minimum_similarity=ontology.get('minimum_similarity'),
                    identity_level=STATUS_LABELS.get(identity, identity),
                    shared_attributes=[names.get(k, k) for k in ontology.get('shared_attributes', [])],
                    children=[entity_node(k) for k in members])

    children = []
    if existing:
        children.append(dict(id='view:existing', kind='stage', stage='existing', label='已有冻结本体',
                             children=[ontology_node(o, 'existing') for o in existing]))
    children.append(dict(id='view:attributes', kind='stage', stage='attributes', label='第一阶段 · 天然属性聚类',
                         children=[ontology_node(o, 'attributes') for o in attrs]))
    relation_rounds = sorted({o['round'] for o in relations} | {r['round'] for r in rounds})
    children.append(dict(id='view:relations', kind='stage', stage='relations', label='第二阶段 · 关系权重聚类',
                         children=[dict(id='view:round:' + str(r), kind='round', round=r,
                                        label='第 ' + str(r) + ' 轮' + (
                                            ' · 无新本体，停止' if not any(o['round'] == r for o in relations) else ''),
                                        children=[ontology_node(o, 'relations') for o in relations if o['round'] == r])
                                   for r in relation_rounds]))
    pending_types = defaultdict(list)
    for key in pending:
        pending_types[by_id[key]['type']].append(key)
    children.append(dict(id='view:unassigned', kind='unassigned', label='未聚类对象 · ' + str(len(pending)),
                         children=[dict(id='view:unassigned:' + kind, kind='type_group',
                                        label=translate_term(kind) + ' · ' + str(len(keys)),
                                        children=[entity_node(k) for k in sorted(keys)])
                                   for kind, keys in sorted(pending_types.items())]))
    # Only the boundary snapshot in which a new ontology was born contributes
    # to its dependency edges. Later snapshots are historical repetitions.
    dependencies = {}
    seen_pairs = set()
    seen_connections = set()
    for row in boundary:
        pair = (row['round'], row['entity'], row['ontology'])
        ids = row['connection_ids']
        if pair in seen_pairs or row['weight'] != len(ids) or len(ids) != len(set(ids)):
            raise ValueError('Repeated boundary row or invalid connection count')
        seen_pairs.add(pair)
        if row['entity'] not in by_id or row['ontology'] not in ontology_by_id:
            raise ValueError('Unknown boundary endpoint')
        source_ontology = ontology_by_id[row['ontology']]
        if source_ontology.get('stage') == 'relations' and source_ontology['round'] >= row['round']:
            raise ValueError('Dependency source must already be frozen in this round')
        owner = assigned.get(row['entity'])
        target = ontology_by_id.get(owner)
        if not target or target.get('stage') != 'relations' or target['round'] != row['round']:
            continue
        key = (row['ontology'], owner, row['round'])
        group = dependencies.setdefault(key, dict(source=key[0], target=key[1], round=key[2],
                                                  weight=0, connection_ids=[]))
        for connection in ids:
            if (row['round'], connection) in seen_connections:
                raise ValueError('A connection was counted twice within one round')
            seen_connections.add((row['round'], connection))
        group['weight'] += row['weight']
        group['connection_ids'].extend(ids)
    summary = dict(objects=len(profiles), ontologies=len(ontologies), clustered_members=len(assigned),
                   unassigned=len(pending), attribute_ontologies=len(attrs), relation_ontologies=len(relations),
                   existing_ontologies=len(existing), generated_rounds=len({o['round'] for o in relations}),
                   dependency_edges=len(dependencies))
    root = dict(id='view:root', kind='root', label='两阶段聚类结果', children=children)
    return dict(root=root, dependency_edges=list(dependencies.values()), summary=summary)


def render(source, output):
    data = build_hierarchy(source)
    output = Path(output)
    if output.suffix.lower() != '.html':
        raise ValueError('Output must be an HTML file')
    # An output accidentally aimed at a source JSONL is rejected above.
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    template = Path(__file__).with_name('ontology_hierarchy.html').read_text(encoding='utf-8')
    if template.count('__HIERARCHY_DATA__') != 1:
        raise ValueError('Hierarchy template has an invalid payload marker')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace('__HIERARCHY_DATA__', payload), encoding='utf-8')
    return data['summary']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成现有两阶段聚类结果的中文层次图')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.source, args.output), ensure_ascii=False))
