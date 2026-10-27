# -*- coding: utf-8 -*-
"""Route ontology candidates by object kind and formation evidence.

Rules are explicit engineering hypotheses. No class axioms or specimen identity
claims are inferred from cluster membership. Unknown primary views abstain.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_knowledge_graph import identifier

VERSION='formation-routing-v1'
RELATIONAL_TYPES={
    'geographic location','basin','coalfield','mine','mineral deposit',
    'stratigraphic unit','coal seam','geological body','geological structure',
    'geological time','geological process','geological event','geological environment',
}
PHYSICAL_KIND_TYPES={'mineral','rock','chemical element','element group','organic component','fluid'}
CONTROLLED_PROPERTIES={
    'formula','crystal_family','habit','cleavage_quality','mohs_hardness',
    'luster','fracture','surface_texture','grain_size','roundness','genesis','morphology',
}
WEIGHTS={'attribute':(0.8,0.2),'relation':(0.2,0.8),'hybrid':(0.5,0.5),
         'structural_relation':(0.0,1.0),'pending':(0.0,0.0)}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def classify(name,entity_type,passage='',reference=False):
    """Separate routing policy from claims about a particular specimen's origin."""
    lower=name.casefold();context=passage.casefold()
    if reference:
        origin,route,reason='natural_kind_reference','attribute','reference mineral species, not specimen provenance'
    elif entity_type in RELATIONAL_TYPES:
        origin,route,reason='not_inferred','structural_relation','space/time/process/site object; use its relationships'
    elif re.search(r'\b(synthetic|synthesized|manufactured|engineered|artificial)\b|人造|合成',lower):
        origin,route,reason='artificial_candidate','relation','explicit fabrication cue in entity name'
    elif re.search(r'coal gangue|coal refuse|coal processing waste|coal tailing|non-magnetic residues|alkali-insoluble residues|clean coal|coarse reject|density products|thickener underflows|矸石|选煤|浮选产物',lower):
        origin,route,reason='processed_natural_candidate','hybrid','natural material with processing/waste cue in name'
    elif lower=='fly ash' and re.search(r'coal-fired|combustion|power plant|燃烧|电厂',context):
        origin,route,reason='artificial_byproduct_candidate','relation','fly ash named in explicit combustion/power-plant context'
    elif entity_type in PHYSICAL_KIND_TYPES:
        origin,route,reason='natural_kind_candidate','attribute','physical-kind annotation; no claim of specimen formation history'
    else:
        origin,route,reason='undetermined','pending','insufficient formation evidence; generic sample/material names do not establish origin'
    return dict(origin=origin,route=route,reason=reason,weights=dict(zip(('attributes','relations'),WEIGHTS[route])),
                classification_status='routing_hypothesis_not_verified_origin',policy_version=VERSION)


def object_info(node):
    if node['kind']=='reference_concept':return node['label'],'mineral','reference_kind'
    return node['annotation']['text'],node['annotation']['type'],'source_mention'


def feature_views(nodes,edges):
    """Keep relation direction/predicate/neighbor kind, excluding names and IDs."""
    objects={k:n for k,n in nodes.items() if n['kind'] in {'entity_mention','reference_concept'}}
    properties={key:set() for key in objects};relations={key:set() for key in objects}
    evidence={key:dict(attributes=[],relations=[]) for key in objects}
    roles=defaultdict(dict)
    for edge in edges:
        if edge['type'] in {'HEAD','TAIL'}:roles[edge['source']][edge['type']]=edge['target']
    for claim in nodes.values():
        if claim['kind']=='property_assertion' and claim['subject'] in objects:
            owner=claim['subject'];conditions=claim.get('conditions') or {}
            feature=nodes[claim['feature']]['key']
            if feature not in CONTROLLED_PROPERTIES or claim.get('layer')=='pending':continue
            if claim['layer']=='external':
                allowed=not conditions and claim.get('cluster_eligible') is True
            else:
                # Observation metadata is not a clustering condition. Actual
                # uncertainty, analyte, methods and comparisons require review.
                allowed=not any(conditions.get(k) for k in
                    ['conditions','uncertainty_text','method_text','basis_text','analyte'])
                scope=nodes.get(objects[owner].get('scope'),{})
                allowed=allowed and scope.get('provenance',{}).get('source_verification',{}).get('verified') is True
            if allowed:
                properties[owner].add(claim['value_node']);evidence[owner]['attributes'].append(claim['id'])
        elif claim['kind']=='relations' and claim.get('default_fact_eligible') is True:
            endpoints=roles.get(claim['id'],{});head=endpoints.get('HEAD');tail=endpoints.get('TAIL')
            if head not in objects or tail not in objects:continue
            predicate=claim['predicate']
            relations[head].add(json.dumps(['out',predicate,object_info(objects[tail])[1]],ensure_ascii=False))
            relations[tail].add(json.dumps(['in',predicate,object_info(objects[head])[1]],ensure_ascii=False))
            evidence[head]['relations'].append(claim['id']);evidence[tail]['relations'].append(claim['id'])
    return objects,properties,relations,evidence


def jaccard(a,b):
    return len(a & b)/len(a | b) if a and b else None


def similarity(a,b,profiles):
    """Require primary views; missing secondary views do not count as evidence."""
    left,right=profiles[a],profiles[b]
    if left['partition']!=right['partition']:return None
    route=left['policy']['route']
    if route=='pending':return None
    attributes=jaccard(set(left['attributes']),set(right['attributes']))
    relations=jaccard(set(left['relations']),set(right['relations']))
    if route=='attribute' and attributes is None:return None
    if route in {'relation','structural_relation'} and relations is None:return None
    if route=='hybrid' and (attributes is None or relations is None):return None
    weights=WEIGHTS[route];numerator=denominator=0.0
    for score,weight in zip((attributes,relations),weights):
        if score is not None and weight:
            numerator+=score*weight;denominator+=weight
    return numerator/denominator if denominator else None


def cluster_profiles(profiles,threshold=0.45):
    """Deterministic complete-link threshold grouping; abstain on sparse inputs."""
    groups=[];pending=[]
    for key,row in sorted(profiles.items()):
        route=row['policy']['route'];counts={'attributes':len(row['attributes']),'relations':len(row['relations'])}
        required={'attribute':['attributes'],'relation':['relations'],'structural_relation':['relations'],
                  'hybrid':['attributes','relations'],'pending':[]}[route]
        if not required or any(counts[view]<2 for view in required):
            pending.append(dict(entity=key,route=route,reason='formation_unknown' if route=='pending' else 'fewer_than_two_primary_features',feature_counts=counts));continue
        for group in groups:
            scores=[similarity(key,other,profiles) for other in group]
            if all(s is not None and s>=threshold for s in scores):
                group.append(key);break
        else:groups.append([key])
    clusters=[]
    for group in groups:
        row=profiles[group[0]]
        scores=[similarity(a,b,profiles) for i,a in enumerate(group) for b in group[i+1:]]
        clusters.append(dict(id=identifier(VERSION,threshold,*group),members=group,
            route=row['policy']['route'],partition=row['partition'],
            label=' / '.join(profiles[k]['name'] for k in group),
            shared_attributes=sorted(set.intersection(*(set(profiles[k]['attributes']) for k in group))),
            shared_relations=sorted(set.intersection(*(set(profiles[k]['relations']) for k in group))),
            minimum_similarity=min(scores) if scores else None,threshold=threshold,
            status='candidate_group' if len(group)>1 else 'singleton_not_evidence_of_a_class',
            formal_ontology_axiom=False))
    return clusters,pending


def build(source,output,threshold=0.45):
    if not 0<threshold<=1:raise ValueError('Threshold must be in (0, 1]')
    if output.exists():raise ValueError('Use a new output directory')
    all_nodes=read_rows(source/'nodes.jsonl');nodes={n['id']:n for n in all_nodes}
    if len(nodes)!=len(all_nodes):raise ValueError('Duplicate node identifiers')
    edges=read_rows(source/'edges.jsonl')
    objects,properties,relations,evidence=feature_views(nodes,edges)
    profiles={}
    for key,node in objects.items():
        name,type_,level=object_info(node)
        passage=nodes.get(node.get('scope'),{}).get('passage','')
        policy=classify(name,type_,passage,node['kind']=='reference_concept')
        profiles[key]=dict(entity=key,name=name,type=type_,identity_level=level,policy=policy,
            partition=[policy['route'],type_,level],attributes=sorted(properties[key]),
            relations=sorted(relations[key]),evidence=evidence[key])
    clusters,pending=cluster_profiles(profiles,threshold)
    output.mkdir(parents=True)
    for name,rows in [('entity_profiles.jsonl',profiles.values()),('candidate_clusters.jsonl',clusters),('pending_entities.jsonl',pending)]:
        (output/name).write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in rows)+'\n',encoding='utf-8')
    # Export a fresh graph; previous candidate clusters are superseded, not copied.
    graph_nodes={k:n for k,n in nodes.items() if n['kind']!='candidate_cluster'}
    graph_edges=[e for e in edges if e['source'] in graph_nodes and e['target'] in graph_nodes]
    for key,row in profiles.items():
        graph_nodes[key]=dict(graph_nodes[key],clustering_policy=row['policy'])
    for cluster in clusters:
        graph_nodes[cluster['id']]=dict(cluster,kind='candidate_cluster')
        for key in cluster['members']:
            graph_edges.append(dict(id=identifier(key,cluster['id'],'CANDIDATE_MEMBER_OF'),source=key,target=cluster['id'],type='CANDIDATE_MEMBER_OF'))
    for name,rows in [('nodes.jsonl',graph_nodes.values()),('edges.jsonl',graph_edges)]:
        (output/name).write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in rows)+'\n',encoding='utf-8')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'));db.execute('PRAGMA foreign_keys=ON')
    db.executescript('CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,payload TEXT);CREATE TABLE edges(id TEXT PRIMARY KEY,source TEXT REFERENCES nodes(id),target TEXT REFERENCES nodes(id),type TEXT);')
    db.executemany('INSERT INTO nodes VALUES(?,?,?)',[(n['id'],n['kind'],json.dumps(n,ensure_ascii=False)) for n in graph_nodes.values()])
    db.executemany('INSERT OR IGNORE INTO edges VALUES(:id,:source,:target,:type)',graph_edges)
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0];foreign=db.execute('PRAGMA foreign_key_check').fetchall();db.commit();db.close()
    if integrity!='ok' or foreign:raise ValueError('Graph integrity failed')
    summary=dict(policy_version=VERSION,objects=len(profiles),entity_mentions=sum(r['identity_level']=='source_mention' for r in profiles.values()),
        route_counts=dict(Counter(r['policy']['route'] for r in profiles.values())),
        origin_hypotheses=dict(Counter(r['policy']['origin'] for r in profiles.values())),
        clustered_objects=sum(len(c['members']) for c in clusters),
        candidate_groups=sum(len(c['members'])>1 for c in clusters),singletons=sum(len(c['members'])==1 for c in clusters),
        pending_objects=len(pending),pending_reasons=dict(Counter(p['reason'] for p in pending)),
        threshold=threshold,weights={k:dict(zip(('attributes','relations'),v)) for k,v in WEIGHTS.items()},
        input_sha256={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['nodes.jsonl','edges.jsonl']},
        origin_semantically_verified=False,formal_ontology_complete=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    report_html(profiles,clusters,pending,summary,output/'聚类策略与结果.html')
    from build_domain_features import visualization
    property_claims=[dict(n,clustering_policy=profiles[n['subject']]['policy'])
                     for n in graph_nodes.values() if n['kind']=='property_assertion' and n['subject'] in profiles]
    visualization(graph_nodes,property_claims,[c for c in clusters if len(c['members'])>1],output)
    (output/'领域特征图谱.html').rename(output/'属性关系聚类图谱.html')
    return summary


def report_html(profiles,clusters,pending,summary,output):
    payload=json.dumps(dict(profiles=list(profiles.values()),clusters=clusters,pending=pending,summary=summary),ensure_ascii=False).replace('<','\\u003c')
    html='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>属性与关系 · 本体候选聚类</title><style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e2e8f0;font:14px system-ui,"Microsoft YaHei",sans-serif}header{padding:28px 36px;border-bottom:1px solid #29405a}h1{margin:0 0 12px;font-size:25px}p{color:#9db2ca;line-height:1.8}main{padding:24px 36px}.cards{display:flex;gap:15px;flex-wrap:wrap}.card{background:#142237;padding:18px;border:1px solid #30435e;border-radius:10px;min-width:170px}.card strong{font-size:27px;color:#56dfbc;display:block}input,select,button{padding:11px;border-radius:6px;color:#deebf5;border:1px solid #36506c;background:#172b42;margin:10px 10px 10px 0}button{cursor:pointer}.layout{display:grid;grid-template-columns:minmax(400px,1fr) minmax(350px,1fr);gap:25px;margin-top:15px}#list{max-height:62vh;overflow:auto}#list button{display:block;width:97%;text-align:left;margin:5px 0;line-height:1.65}pre{white-space:pre-wrap;word-break:break-word;line-height:1.8;background:#122036;border-radius:8px;padding:20px;margin:0}small{color:#92abc8}@media(max-width:900px){.layout{grid-template-columns:1fr}}</style><header><h1>属性与关系 · 本体候选聚类</h1><p>自然物质以属性为主，人工对象以关系为主，加工天然材料采用混合特征；空间、时间、地层和事件使用关系。形成方式由显式规则提出候选，尚未人工验收。稀疏信息保留待补全。</p></header><main><div class="cards" id="stats"></div><input id="search" placeholder="搜索名称或实体类型"><select id="route"><option value="all">全部策略</option><option value="attribute">自然类别：属性为主</option><option value="relation">人工候选：关系为主</option><option value="hybrid">加工天然材料：混合</option><option value="structural_relation">空间 / 时间 / 过程：关系</option><option value="pending">形成方式待确认</option></select><select id="mode"><option value="entities">全部实体及策略</option><option value="clusters">聚类与单例</option><option value="pending">信息不足的实体</option></select><span id="count"></span><div class="layout"><div id="list"></div><section><h2 id="title">选择一个实体或候选簇</h2><pre id="detail">点击左侧条目查看路由依据、权重、可用特征和来源陈述编号。单例不证明独立本体类；未聚类不表示不同类。</pre></section></div></main><script id="data" type="application/json">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),byId=new Map(data.profiles.map(r=>[r.entity,r]));
const routeNames={attribute:'属性为主',relation:'关系为主',hybrid:'属性 + 关系',structural_relation:'结构关系',pending:'待确认'};
for(const [label,value] of [['实体及参考概念',data.summary.objects],['候选多成员簇',data.summary.candidate_groups],['信息不足 / 待确认',data.summary.pending_objects]]){const card=document.createElement('div');card.className='card';const strong=document.createElement('strong');strong.textContent=value;card.appendChild(strong);card.appendChild(document.createTextNode(label));$('stats').appendChild(card)}
function show(row){$('title').textContent=row.name||row.label||byId.get(row.entity)?.name||'候选结果';$('detail').textContent=JSON.stringify(row,null,2)}
function filter(){const q=$('search').value.trim().toLowerCase(),route=$('route').value,mode=$('mode').value;let rows=mode==='clusters'?data.clusters:mode==='pending'?data.pending.map(r=>({...byId.get(r.entity),pending_reason:r.reason,feature_counts:r.feature_counts})):data.profiles;rows=rows.filter(r=>(route==='all'||(r.route||r.policy.route)===route)&&((r.name||r.label||'')+' '+(r.type||'')).toLowerCase().includes(q));$('list').replaceChildren();$('count').textContent=rows.length+' 项';for(const row of rows){const b=document.createElement('button'),r=row.route||row.policy.route;b.textContent=(row.name||row.label)+' · '+routeNames[r]+(row.members?' · '+row.members.length+' 成员':' · '+row.type);b.onclick=()=>show(row);$('list').appendChild(b)}}$('search').oninput=filter;$('route').onchange=filter;$('mode').onchange=filter;filter();
</script>'''
    output.write_text(html.replace('PAYLOAD',payload),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--threshold',type=float,default=0.45)
    a=p.parse_args();print(json.dumps(build(a.source,a.output,a.threshold)))
