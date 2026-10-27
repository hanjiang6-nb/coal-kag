"""Independently check routing coverage, feature evidence and pair thresholds."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cluster_ontology_candidates import read_rows


def validate(source,output):
    nodes={n['id']:n for n in read_rows(source/'nodes.jsonl')}
    profiles=read_rows(output/'entity_profiles.jsonl');by_id={p['entity']:p for p in profiles}
    expected={k for k,n in nodes.items() if n['kind'] in {'entity_mention','reference_concept'}}
    if set(by_id)!=expected or len(by_id)!=len(profiles):raise ValueError('Object coverage incorrect')
    clusters=read_rows(output/'candidate_clusters.jsonl');pending=read_rows(output/'pending_entities.jsonl')
    membership=[k for c in clusters for k in c['members']]+[p['entity'] for p in pending]
    if set(membership)!=expected or any(v!=1 for v in Counter(membership).values()):raise ValueError('Cluster/pending partition incorrect')
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    source_edges=read_rows(source/'edges.jsonl');roles={}
    for edge in source_edges:
        if edge['type'] in {'HEAD','TAIL'}:roles.setdefault(edge['source'],{})[edge['type']]=edge['target']
    for name,value in summary['input_sha256'].items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=value:raise ValueError('Input changed')
    primary={'attribute':['attributes'],'relation':['relations'],'structural_relation':['relations'],'hybrid':['attributes','relations'],'pending':[]}
    for p in profiles:
        if p['policy']['route'] not in primary:raise ValueError('Unknown route')
        if p['policy']['weights']!=summary['weights'][p['policy']['route']]:raise ValueError('Weight mismatch')
        if p['partition']!=[p['policy']['route'],p['type'],p['identity_level']]:raise ValueError('Unsafe partition')
        if p['policy']['classification_status']!='routing_hypothesis_not_verified_origin':raise ValueError('Origin improperly certified')
        for claim_id in p['evidence']['attributes']:
            claim=nodes[claim_id]
            if claim['subject']!=p['entity'] or claim.get('layer')=='pending':raise ValueError('Incorrect property evidence')
            if claim['layer']=='external' and claim['conditions']:raise ValueError('Conditional reference in clustering')
        for claim_id in p['evidence']['relations']:
            if not nodes[claim_id].get('default_fact_eligible'):raise ValueError('Unapproved relation in clustering')
        expected_relations=set()
        for claim_id in p['evidence']['relations']:
            endpoints=roles.get(claim_id,{})
            if p['entity'] not in endpoints.values():raise ValueError('Relation not incident to object')
            for own,opposite,direction in [('HEAD','TAIL','out'),('TAIL','HEAD','in')]:
                if endpoints.get(own)==p['entity']:
                    neighbor=nodes[endpoints[opposite]]
                    neighbor_type='mineral' if neighbor['kind']=='reference_concept' else neighbor['annotation']['type']
                    expected_relations.add(json.dumps([direction,nodes[claim_id]['predicate'],neighbor_type],ensure_ascii=False))
        if set(p['relations'])!=expected_relations:raise ValueError('Relation vector/evidence mismatch')
        if set(p['attributes'])!={nodes[k]['value_node'] for k in p['evidence']['attributes']}:
            raise ValueError('Property vector/evidence mismatch')
    for cluster in clusters:
        if cluster['formal_ontology_axiom'] is not False:raise ValueError('Candidate promoted to axiom')
        members=[by_id[k] for k in cluster['members']]
        for p in members:
            route=p['policy']['route']
            if not primary[route] or any(len(p[v])<2 for v in primary[route]):raise ValueError('Sparse object clustered')
            if p['partition']!=cluster['partition']:raise ValueError('Cross-category cluster')
        if sorted(set.intersection(*(set(p['attributes']) for p in members)))!=cluster['shared_attributes']:
            raise ValueError('Shared properties not supported by all members')
        if sorted(set.intersection(*(set(p['relations']) for p in members)))!=cluster['shared_relations']:
            raise ValueError('Shared relations not supported by all members')
        for a,b in itertools.combinations(members,2):
            numerator=denominator=0
            for view,weight in a['policy']['weights'].items():
                va,vb=set(a[view]),set(b[view])
                if va and vb and weight:
                    numerator+=weight*len(va&vb)/len(va|vb);denominator+=weight
            if not denominator or numerator/denominator<summary['threshold']:raise ValueError('Complete-link threshold violated')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'))
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Database invalid')
    db.close()
    if sum(len(c['members']) for c in clusters)+len(pending)!=summary['objects']:raise ValueError('Summary total mismatch')
    result=dict(passed=True,objects_checked=len(profiles),clusters_checked=len(clusters),pending_checked=len(pending),
                formation_semantics_verified=False,formal_ontology_complete=False)
    (output/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(validate(a.source,a.output)))
