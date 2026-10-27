# -*- coding: utf-8 -*-
"""Intrinsic attributes -> frozen ontologies -> relation-weight ontologies.

Both stages produce candidate ontologies, never entity-identity merges. Existing
ontologies and their members cannot re-enter either clustering pool.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_knowledge_graph import identifier
from cluster_ontology_candidates import read_rows, feature_views, object_info

VERSION='two-stage-ontology-v2'
INTRINSIC={'formula','crystal_family','habit','cleavage_quality','mohs_hardness',
           'luster','fracture','surface_texture','grain_size','roundness','morphology'}
MATERIAL_TYPES={'mineral','rock','chemical element','element group','organic component','fluid','sample or material'}


def existing_ontologies(nodes):
    result=[]
    for node in nodes.values():
        if node['kind'] in {'ontology','ontology_class','frozen_ontology'} or node.get('ontology_frozen') is True:
            members=node.get('members')
            if not isinstance(members,list) or not members or len(set(members))!=len(members):
                raise ValueError('Existing ontology needs a nonempty unique member list')
            result.append(dict(id=node['id'],members=list(members),stage='existing',
                label=node.get('label',node['id']),ontology_frozen=True,source_payload=node))
    membership={}
    for ontology in result:
        for member in ontology['members']:
            if member not in nodes or member==ontology['id'] or member in membership:
                raise ValueError('Invalid or overlapping existing ontology membership')
            membership[member]=ontology['id']
    if set(membership)&{o['id'] for o in result}:raise ValueError('An ontology cannot be a member of another clustering')
    return result,membership


def attribute_profiles(nodes,edges):
    objects,properties,_,evidence=feature_views(nodes,edges)
    allowed={k for k,n in nodes.items() if n['kind']=='feature_value' and n['key'] in INTRINSIC}
    vectors={k:set(values)&allowed for k,values in properties.items()}
    borrowed=defaultdict(list)
    # General mineral-kind references may inform class clustering. This does not
    # create measured specimen properties or copy conditional surface findings.
    for edge in edges:
        a,b=edge['source'],edge['target']
        if edge['type']=='HAS_SPECIES_REFERENCE' and a in objects and b in objects and objects[b]['kind']=='reference_concept':
            if objects[a]['kind']=='entity_mention' and objects[a]['annotation']['type']=='mineral' and objects[a].get('layer')!='pending':
                vectors[a]|=vectors[b]
                borrowed[a].append(dict(reference=b,feature_values=sorted(vectors[b]),
                    interpretation='kind_reference_for_clustering_not_specimen_measurement'))
    return objects,vectors,evidence,borrowed


def complete_link(keys,similarity,threshold):
    groups=[]
    for key in sorted(keys):
        for group in groups:
            if all(similarity(key,other)>=threshold for other in group):
                group.append(key);break
        else:groups.append([key])
    return groups


def weighted_jaccard(a,b):
    keys=set(a)|set(b)
    return sum(min(a.get(k,0),b.get(k,0)) for k in keys)/max(1,sum(max(a.get(k,0),b.get(k,0)) for k in keys))


def semantic_connections(nodes,edges,objects):
    roles=defaultdict(lambda:defaultdict(set))
    for edge in edges:
        if edge['type'] in {'HEAD','TAIL'}:roles[edge['source']][edge['type']].add(edge['target'])
    result=[]
    for key,node in sorted(nodes.items()):
        if node['kind']!='relations' or node.get('default_fact_eligible') is not True:continue
        head=roles[key]['HEAD'];tail=roles[key]['TAIL']
        if len(head)!=1 or len(tail)!=1:raise ValueError('Semantic connection has ambiguous endpoints')
        a,b=next(iter(head)),next(iter(tail))
        if a in objects and b in objects:
            result.append(dict(id=key,source=a,target=b,predicate=node['predicate'],weight=1))
    return result


def count_boundary(connections,membership,remaining):
    matrix=defaultdict(Counter);evidence=defaultdict(lambda:defaultdict(list))
    for edge in connections:
        for member,other in [(edge['source'],edge['target']),(edge['target'],edge['source'])]:
            if member in membership and other in remaining:
                ontology=membership[member];matrix[other][ontology]+=edge['weight']
                evidence[other][ontology].append(edge['id'])
    rows=[]
    for entity in sorted(matrix):
        for ontology,weight in sorted(matrix[entity].items()):
            rows.append(dict(entity=entity,ontology=ontology,weight=weight,connection_ids=evidence[entity][ontology]))
    return rows,{k:dict(v) for k,v in matrix.items()}


def build(source,output,attribute_threshold=.45,relation_threshold=.5):
    if output.exists():raise ValueError('Use a new output directory')
    if not 0<attribute_threshold<=1 or not 0<relation_threshold<=1:raise ValueError('Thresholds must lie in (0,1]')
    raw_nodes=read_rows(source/'nodes.jsonl');nodes={n['id']:n for n in raw_nodes}
    if len(nodes)!=len(raw_nodes):raise ValueError('Duplicate graph node IDs')
    edges=read_rows(source/'edges.jsonl')
    for edge in edges:
        if edge['source'] not in nodes or edge['target'] not in nodes:raise ValueError('Dangling graph edge')
    frozen,assigned=existing_ontologies(nodes)
    objects,vectors,evidence,borrowed=attribute_profiles(nodes,edges)
    frozen_ids={o['id'] for o in frozen}
    if not set(assigned)<=set(objects):raise ValueError('Ontology members must be original objects, not other ontologies or technical nodes')
    if frozen_ids&set(objects):raise ValueError('Frozen ontologies must use an explicit ontology node kind')
    eligible=[];profiles=[]
    for key,node in objects.items():
        name,type_,level=object_info(node)
        usable=(key not in assigned and key not in frozen_ids and type_ in MATERIAL_TYPES
                and node.get('layer')!='pending' and len(vectors[key])>=2)
        profiles.append(dict(entity=key,name=name,type=type_,identity_level=level,
            intrinsic_attributes=sorted(vectors[key]),attribute_eligible=usable,
            property_evidence=evidence[key]['attributes'],reference_derivations=borrowed[key]))
        if usable:eligible.append(key)
    by_id={p['entity']:p for p in profiles}
    def similarity(a,b):
        if (by_id[a]['type'],by_id[a]['identity_level'])!=(by_id[b]['type'],by_id[b]['identity_level']):return 0
        return len(vectors[a]&vectors[b])/len(vectors[a]|vectors[b])
    stage1=[]
    for members in complete_link(eligible,similarity,attribute_threshold):
        if len(members)<2:continue
        oid=identifier(VERSION,'attributes',attribute_threshold,*members)
        ontology=dict(id=oid,members=members,stage='attributes',ontology_frozen=True,
            label='属性本体：'+' / '.join(sorted({by_id[k]['name'] for k in members})),
            member_type=by_id[members[0]]['type'],member_identity_level=by_id[members[0]]['identity_level'],
            shared_attributes=sorted(set.intersection(*(vectors[k] for k in members))),
            minimum_similarity=min(similarity(a,b) for i,a in enumerate(members) for b in members[i+1:]),
            formal_ontology_axiom=False)
        stage1.append(ontology)
        for member in members:assigned[member]=oid
    # Each round uses a fixed snapshot. New ontologies become frozen anchors
    # only in the next round; their members never re-enter the candidate pool.
    anchors=frozen+stage1
    initial_anchor_ids=[o['id'] for o in anchors]
    connections=semantic_connections(nodes,edges,set(objects))
    final_assigned=dict(assigned)
    boundary=[];rounds=[];stage2=[];round_number=0
    while True:
        round_number+=1
        remaining=set(objects)-set(final_assigned)-frozen_ids
        current_boundary,matrix=count_boundary(connections,final_assigned,remaining)
        boundary.extend(dict(row,round=round_number) for row in current_boundary)
        snapshot=dict(round=round_number,anchor_ids=[o['id'] for o in anchors],
            remaining_ids=sorted(remaining),boundary_pairs=len(current_boundary),
            boundary_weight_total=sum(r['weight'] for r in current_boundary),new_ontology_ids=[])
        def relation_similarity(a,b):
            if (by_id[a]['type'],by_id[a]['identity_level'])!=(by_id[b]['type'],by_id[b]['identity_level']):return 0
            return weighted_jaccard(matrix[a],matrix[b])
        new_ontologies=[]
        for members in complete_link(matrix,relation_similarity,relation_threshold):
            if len(members)<2:continue
            oid=identifier(VERSION,'relations',relation_threshold,*members)
            ontology=dict(id=oid,members=members,stage='relations',round=round_number,ontology_frozen=True,
                label='关系本体：'+' / '.join(sorted({by_id[k]['name'] for k in members})),
                member_type=by_id[members[0]]['type'],member_identity_level=by_id[members[0]]['identity_level'],
                boundary_profiles={k:matrix[k] for k in members},
                minimum_similarity=min(relation_similarity(a,b) for i,a in enumerate(members) for b in members[i+1:]),
                formal_ontology_axiom=False)
            new_ontologies.append(ontology)
        snapshot['new_ontology_ids']=[o['id'] for o in new_ontologies];rounds.append(snapshot)
        if not new_ontologies:break
        for ontology in new_ontologies:
            for key in ontology['members']:
                if key in final_assigned or key in frozen_ids:raise ValueError('Ontology/member assigned again')
                final_assigned[key]=ontology['id']
        stage2.extend(new_ontologies);anchors.extend(new_ontologies)
    unassigned=[dict(entity=k,reason='no_connection_to_frozen_ontologies' if k not in matrix else 'no_supported_relation_peer',
                     intrinsic_feature_count=len(vectors[k]),boundary_weights=matrix.get(k,{}))
                for k in sorted(set(objects)-set(final_assigned)-frozen_ids)]
    output.mkdir(parents=True)
    artifacts=[('entity_profiles.jsonl',profiles),('existing_ontologies.jsonl',frozen),
        ('attribute_ontologies.jsonl',stage1),('relation_ontologies.jsonl',stage2),
        ('semantic_connections.jsonl',connections),('boundary_weights.jsonl',boundary),('relation_rounds.jsonl',rounds),
        ('membership.jsonl',[dict(entity=k,ontology=v) for k,v in sorted(final_assigned.items())]),
        ('unassigned.jsonl',unassigned)]
    for name,rows in artifacts:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    # Preserve frozen payloads byte-for-byte at the JSON-object level.
    graph_nodes={k:n for k,n in nodes.items() if n['kind']!='candidate_cluster' or k in frozen_ids}
    graph_edges=[e for e in edges if e['source'] in graph_nodes and e['target'] in graph_nodes and e['type']!='BOUNDARY_WEIGHT']
    for ontology in stage1+stage2:
        graph_nodes[ontology['id']]=dict(ontology,kind='ontology')
        for member in ontology['members']:
            graph_edges.append(dict(id=identifier(member,ontology['id'],'MEMBER_OF_ONTOLOGY'),source=member,target=ontology['id'],type='MEMBER_OF_ONTOLOGY'))
    for row in boundary:
        graph_edges.append(dict(id=identifier(row['round'],row['entity'],row['ontology'],'BOUNDARY_WEIGHT'),
            source=row['entity'],target=row['ontology'],type='BOUNDARY_WEIGHT',round=row['round'],
            active=row['round']==round_number,weight=row['weight'],connection_ids=row['connection_ids']))
    unique_edges={}
    for edge in graph_edges:
        if edge['id'] in unique_edges and unique_edges[edge['id']]!=edge:
            raise ValueError('Conflicting graph edges share an identifier')
        unique_edges[edge['id']]=edge
    graph_edges=list(unique_edges.values())
    for name,rows in [('nodes.jsonl',graph_nodes.values()),('edges.jsonl',graph_edges)]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'));db.execute('PRAGMA foreign_keys=ON')
    db.executescript('CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,payload TEXT);CREATE TABLE edges(id TEXT PRIMARY KEY,source TEXT REFERENCES nodes(id),target TEXT REFERENCES nodes(id),type TEXT,payload TEXT);')
    db.executemany('INSERT INTO nodes VALUES(?,?,?)',[(n['id'],n['kind'],json.dumps(n,ensure_ascii=False)) for n in graph_nodes.values()])
    db.executemany('INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)',[(e['id'],e['source'],e['target'],e['type'],json.dumps(e,ensure_ascii=False)) for e in graph_edges])
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Database integrity failure')
    db.commit();db.close()
    summary=dict(version=VERSION,objects=len(objects),existing_frozen_ontologies=len(frozen),
        new_attribute_ontologies=len(stage1),attribute_members=sum(len(o['members']) for o in stage1),
        new_relation_ontologies=len(stage2),relation_members=sum(len(o['members']) for o in stage2),
        unassigned=len(unassigned),attribute_threshold=attribute_threshold,relation_threshold=relation_threshold,
        boundary_pairs=rounds[-1]['boundary_pairs'],boundary_weight_total=rounds[-1]['boundary_weight_total'],
        first_round_boundary_weight_total=rounds[0]['boundary_weight_total'],
        relation_rounds=len(rounds),boundary_history_rows=len(boundary),
        original_semantic_connections=len(connections),second_stage_anchor_ids=initial_anchor_ids,
        intrinsic_feature_keys=sorted(INTRINSIC),formal_ontology_complete=False,
        input_sha256={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['nodes.jsonl','edges.jsonl']})
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    used_connections={key for row in boundary for key in row['connection_ids']}
    details={key:dict(id=key,predicate=nodes[key]['predicate'],annotation=nodes[key].get('annotation',{})) for key in used_connections}
    report(profiles,frozen+stage1,stage2,boundary,unassigned,summary,output/'两阶段本体聚类.html',details,{k:n for k,n in nodes.items() if n['kind']=='feature_value'})
    return summary


def report(profiles,anchors,stage2,boundary,unassigned,summary,path,details=None,feature_details=None):
    from ontology_report_zh import display_metadata
    contents=dict(profiles=profiles,anchors=anchors,stage2=stage2,boundary=boundary,
        unassigned=unassigned,summary=summary,connections=details or {},
        original_names={p['entity']:p['name'] for p in profiles})
    contents.update(display_metadata(profiles,anchors+stage2,feature_details))
    payload=json.dumps(contents,ensure_ascii=False).replace('<','\\u003c')
    template=Path(__file__).with_name('two_stage_report.html').read_text(encoding='utf-8')
    path.write_text(template.replace('PAYLOAD',payload),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--attribute-threshold',type=float,default=.45);p.add_argument('--relation-threshold',type=float,default=.5)
    a=p.parse_args();print(json.dumps(build(a.source,a.output,a.attribute_threshold,a.relation_threshold)))
