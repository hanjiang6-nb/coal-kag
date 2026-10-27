"""Audit both ontology stages, frozen membership, and exact boundary edge sums."""
import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cluster_ontology_candidates import read_rows
from cluster_two_stage_ontologies import INTRINSIC, MATERIAL_TYPES


def validate(source,output):
    def rows(name):return read_rows(output/name)
    raw=read_rows(source/'nodes.jsonl');source_nodes={n['id']:n for n in raw}
    if len(raw)!=len(source_nodes):raise ValueError('Duplicate source nodes')
    source_edges=read_rows(source/'edges.jsonl')
    objects={k:n for k,n in source_nodes.items() if n['kind'] in {'entity_mention','reference_concept'}}
    profiles=rows('entity_profiles.jsonl');profile={p['entity']:p for p in profiles}
    if set(profile)!=set(objects) or len(profile)!=len(profiles):raise ValueError('Object profiles incomplete')
    frozen=rows('existing_ontologies.jsonl');stage1=rows('attribute_ontologies.jsonl');stage2=rows('relation_ontologies.jsonl')
    source_frozen={k for k,n in source_nodes.items() if n['kind'] in {'ontology','ontology_class','frozen_ontology'} or n.get('ontology_frozen') is True}
    if {o['id'] for o in frozen}!=source_frozen:raise ValueError('Existing ontology dropped')
    for o in frozen:
        if o['source_payload']!=source_nodes[o['id']] or o['members']!=source_nodes[o['id']]['members']:
            raise ValueError('Existing ontology changed')
    all_ontologies=frozen+stage1+stage2
    ids={o['id'] for o in all_ontologies}
    if len(ids)!=len(all_ontologies):raise ValueError('Ontology ID reused')
    owners={}
    for o in all_ontologies:
        if o['ontology_frozen'] is not True or not o['members']:raise ValueError('Unfrozen or empty ontology')
        for k in o['members']:
            if k not in objects or k in ids or k in owners:raise ValueError('Ontology reclustered or member reassigned')
            owners[k]=o['id']
    membership=rows('membership.jsonl')
    if {m['entity']:m['ontology'] for m in membership}!=owners or len(membership)!=len(owners):raise ValueError('Membership mismatch')
    unassigned=rows('unassigned.jsonl')
    if {u['entity'] for u in unassigned}!=set(objects)-set(owners) or len(unassigned)!=len(objects)-len(owners):raise ValueError('Unassigned coverage incorrect')
    # Reconstruct admissible physical properties independently from input claims.
    vectors={k:set() for k in objects}
    for claim in source_nodes.values():
        if claim['kind']!='property_assertion' or claim['subject'] not in objects:continue
        key=source_nodes[claim['feature']]['key'];conditions=claim.get('conditions') or {}
        if key not in INTRINSIC or claim.get('layer')=='pending':continue
        if claim['layer']=='external':allowed=not conditions and claim.get('cluster_eligible') is True
        else:
            allowed=not any(conditions.get(k) for k in ['conditions','uncertainty_text','method_text','basis_text','analyte'])
            owner=objects[claim['subject']];scope=source_nodes.get(owner.get('scope'),{})
            allowed=allowed and scope.get('provenance',{}).get('source_verification',{}).get('verified') is True
        if allowed:vectors[claim['subject']].add(claim['value_node'])
    for edge in source_edges:
        a,b=edge['source'],edge['target']
        if edge['type']=='HAS_SPECIES_REFERENCE' and a in objects and b in objects:
            if objects[b]['kind']=='reference_concept' and objects[a]['kind']=='entity_mention' and objects[a]['annotation']['type']=='mineral' and objects[a].get('layer')!='pending':vectors[a]|=vectors[b]
    for k,p in profile.items():
        if set(p['intrinsic_attributes'])!=vectors[k]:raise ValueError('Intrinsic feature profile mismatch')
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    for o in stage1:
        if len(o['members'])<2 or o['stage']!='attributes':raise ValueError('Invalid first stage')
        for k in o['members']:
            if len(vectors[k])<2 or profile[k]['type'] not in MATERIAL_TYPES or objects[k].get('layer')=='pending':raise ValueError('Unsupported physical-property clustering')
        for a,b in itertools.combinations(o['members'],2):
            if (profile[a]['type'],profile[a]['identity_level'])!=(profile[b]['type'],profile[b]['identity_level']):raise ValueError('Incompatible physical categories')
            if len(vectors[a]&vectors[b])/len(vectors[a]|vectors[b])<summary['attribute_threshold']:raise ValueError('Attribute threshold not met')
        if o['shared_attributes']!=sorted(set.intersection(*(vectors[k] for k in o['members']))):raise ValueError('Shared feature mismatch')
    anchors={o['id'] for o in frozen+stage1}
    anchor_members={k:o['id'] for o in frozen+stage1 for k in o['members']}
    if set(summary['second_stage_anchor_ids'])!=anchors:raise ValueError('Second-stage outputs leaked into anchor snapshot')
    roles=defaultdict(lambda:defaultdict(set))
    for e in source_edges:
        if e['type'] in {'HEAD','TAIL'}:roles[e['source']][e['type']].add(e['target'])
    expected_connections={}
    for key,n in source_nodes.items():
        if n['kind']!='relations' or n.get('default_fact_eligible') is not True:continue
        head=roles[key]['HEAD'];tail=roles[key]['TAIL']
        if len(head)!=1 or len(tail)!=1:raise ValueError('Ambiguous semantic edge')
        a,b=next(iter(head)),next(iter(tail))
        if a not in objects or b not in objects:continue
        expected_connections[key]=dict(id=key,source=a,target=b,predicate=n['predicate'],weight=1)
    connections=rows('semantic_connections.jsonl')
    if {e['id']:e for e in connections}!=expected_connections or len(connections)!=len(expected_connections):raise ValueError('Semantic connection export mismatch')
    boundary=rows('boundary_weights.jsonl');rounds=rows('relation_rounds.jsonl')
    if [r['round'] for r in rounds]!=list(range(1,len(rounds)+1)):raise ValueError('Relation rounds are missing or reordered')
    if {r['round'] for r in boundary}-set(range(1,len(rounds)+1)):raise ValueError('Boundary refers to unknown round')
    checked_stage2=set()
    for snapshot in rounds:
        number=snapshot['round'];remaining=set(objects)-set(anchor_members)
        if set(snapshot['anchor_ids'])!=anchors or set(snapshot['remaining_ids'])!=remaining:raise ValueError('Frozen snapshot or remaining pool incorrect')
        expected=defaultdict(list)
        for edge in connections:
            for member,other in [(edge['source'],edge['target']),(edge['target'],edge['source'])]:
                if member in anchor_members and other in remaining:expected[(other,anchor_members[member])].append(edge['id'])
        actual={};matrix=defaultdict(dict)
        for r in [r for r in boundary if r['round']==number]:
            pair=(r['entity'],r['ontology'])
            if pair in actual:raise ValueError('Duplicate boundary pair within round')
            actual[pair]=r
            if pair not in expected or r['weight']!=len(expected[pair]) or Counter(r['connection_ids'])!=Counter(expected[pair]):raise ValueError('Boundary sum differs from original edges')
            matrix[r['entity']][r['ontology']]=r['weight']
        if set(actual)!=set(expected):raise ValueError('Boundary pair missing')
        if snapshot['boundary_weight_total']!=sum(r['weight'] for r in actual.values()):raise ValueError('Round weight total mismatch')
        new=[o for o in stage2 if o['round']==number]
        if {o['id'] for o in new}!=set(snapshot['new_ontology_ids']):raise ValueError('Round output mismatch')
        if number<len(rounds) and not new:raise ValueError('Continued after convergence')
        for o in new:
            if o['stage']!='relations' or len(o['members'])<2:raise ValueError('Invalid relation ontology')
            if any(k not in remaining for k in o['members']):raise ValueError('Previously clustered object reused')
            if o['boundary_profiles']!={k:matrix[k] for k in o['members']}:raise ValueError('Relation profile mismatch')
            for a,b in itertools.combinations(o['members'],2):
                if (profile[a]['type'],profile[a]['identity_level'])!=(profile[b]['type'],profile[b]['identity_level']):raise ValueError('Incompatible relation categories')
                columns=set(matrix[a])|set(matrix[b]);denom=sum(max(matrix[a].get(c,0),matrix[b].get(c,0)) for c in columns)
                score=sum(min(matrix[a].get(c,0),matrix[b].get(c,0)) for c in columns)/max(1,denom)
                if score<summary['relation_threshold']:raise ValueError('Weighted relation threshold not met')
            checked_stage2.add(o['id'])
        for o in new:
            anchors.add(o['id'])
            for k in o['members']:anchor_members[k]=o['id']
    if checked_stage2!={o['id'] for o in stage2}:raise ValueError('Relation ontology omitted from round history')
    if rounds[-1]['new_ontology_ids']:raise ValueError('No convergence snapshot')
    graph_nodes={n['id']:n for n in rows('nodes.jsonl')}
    graph_edges=rows('edges.jsonl')
    if len({e['id'] for e in graph_edges})!=len(graph_edges):raise ValueError('Duplicate output edges')
    for o in frozen:
        if graph_nodes[o['id']]!=source_nodes[o['id']]:raise ValueError('Frozen graph payload changed')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'))
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Database integrity failure')
    if db.execute('SELECT count(*) FROM nodes').fetchone()[0]!=len(graph_nodes):raise ValueError('Database/export count mismatch')
    if db.execute('SELECT count(*) FROM edges').fetchone()[0]!=len(graph_edges):raise ValueError('Database/export edge mismatch')
    db.close()
    for name,digest in summary['input_sha256'].items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source changed')
    if summary['boundary_weight_total']!=rounds[-1]['boundary_weight_total']:raise ValueError('Final boundary total mismatch')
    result=dict(passed=True,objects_checked=len(objects),existing_ontologies_preserved=len(frozen),
        attribute_ontologies_checked=len(stage1),relation_ontologies_checked=len(stage2),
        original_connections_checked=len(connections),boundary_pairs_checked=len(boundary),
        membership_nonoverlapping=True,ontology_reclustering_detected=False,
        formal_ontology_semantics_verified=False)
    (output/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(validate(a.source,a.output)))
