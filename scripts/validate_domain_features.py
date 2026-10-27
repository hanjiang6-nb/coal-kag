"""Check coverage, reusable identifiers, evidence scoping and graph integrity."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_knowledge_graph import identifier
from build_domain_features import canonical_value


def validate(output):
    def records(name):
        return [json.loads(s) for s in (output/name).read_text(encoding='utf-8').splitlines()]
    all_nodes=records('nodes.jsonl');nodes={n['id']:n for n in all_nodes};edges=records('edges.jsonl')
    if len(nodes)!=len(all_nodes):raise ValueError('Duplicate node identifiers')
    for edge in edges:
        if edge['source'] not in nodes or edge['target'] not in nodes:raise ValueError('Dangling edge')
    claims=records('property_assertions.jsonl');coverage=records('entity_coverage.jsonl')
    mentions={n['id'] for n in all_nodes if n['kind']=='entity_mention'}
    if {row['entity'] for row in coverage}!=mentions or len(coverage)!=len(mentions):
        raise ValueError('Entity coverage incomplete or duplicated')
    for claim in claims:
        value=nodes[claim['value_node']];feature=nodes[claim['feature']]
        expected=identifier('feature_value',feature['key'],canonical_value(feature['key'],claim['raw_value']),value['unit'])
        if expected!=value['id']:raise ValueError('Non-reusable value identifier')
        if claim['layer']=='external':
            if not claim['origin'].get('url','').startswith('https://'):raise ValueError('External source missing')
        elif not claim['evidence']:raise ValueError('Original evidence missing')
    allowed={c['value_node'] for c in claims if c['layer']=='external' and not c['conditions'] and c['cluster_eligible']}
    for cluster in records('candidate_clusters.jsonl'):
        if not set(cluster['shared_values'])<=allowed:raise ValueError('Contextual/unknown values leaked into clustering')
        for member in cluster['members']:
            if nodes[member]['kind']!='reference_concept':raise ValueError('Sample inferred to be species')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'))
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database integrity failure')
    if db.execute('SELECT count(*) FROM nodes').fetchone()[0]!=len(nodes):raise ValueError('Export/database mismatch')
    db.close()
    result=dict(passed=True,entity_mentions=len(mentions),property_assertions=len(claims),
        shared_values=sum(n['kind']=='feature_value' for n in all_nodes),
        property_coverage=dict(Counter(v['status'] for v in coverage)),
        all_properties_semantically_verified=False,formal_ontology_complete=False)
    (output/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(validate(a.output)))
