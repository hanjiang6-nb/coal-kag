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
    report(profiles,frozen+stage1,stage2,boundary,unassigned,summary,output/'两阶段本体聚类.html',details)
    return summary


def report(profiles,anchors,stage2,boundary,unassigned,summary,path,details=None):
    payload=json.dumps(dict(profiles=profiles,anchors=anchors,stage2=stage2,boundary=boundary,unassigned=unassigned,summary=summary,connections=details or {}),ensure_ascii=False).replace('<','\\u003c')
    html='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>两阶段本体聚类</title><style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#deebf5;font:14px system-ui,"Microsoft YaHei",sans-serif}header{padding:28px 36px;border-bottom:1px solid #29405a}h1{font-size:26px;margin:0 0 14px}p{line-height:1.8;color:#9eb3ce}main{padding:25px 36px}.cards{display:flex;gap:15px;flex-wrap:wrap}.card{background:#14243a;border:1px solid #344c65;border-radius:10px;padding:17px;min-width:180px}.card strong{display:block;font-size:28px;color:#5ee1bf}select,input,button{background:#192c45;color:#e3eef6;border:1px solid #35516d;padding:11px;border-radius:6px;margin:9px 9px 9px 0}button{cursor:pointer}.layout{display:grid;grid-template-columns:1fr 1.15fr;gap:24px;margin-top:15px}#list{max-height:62vh;overflow:auto}#list button{display:block;width:97%;text-align:left;margin:5px 0;line-height:1.7}pre{background:#142238;padding:20px;border-radius:8px;white-space:pre-wrap;word-break:break-word;line-height:1.7}@media(max-width:900px){.layout{grid-template-columns:1fr}}</style><header><h1>天然属性 → 本体 · 关系权重 → 新本体</h1><p>第一阶段仅比较客观物性，不使用来源、用途或成因标签。形成的本体立即冻结。第二阶段只比较剩余节点到冻结本体的原始关系连线总数，产生新的本体；已有本体和成员不再合并或重新分配。</p></header><main><div id="stats" class="cards"></div><select id="mode"><option value="attributes">属性阶段本体 / 已有冻结本体</option><option value="relations">关系阶段新本体</option><option value="weights">连线权重与原始证据</option><option value="unassigned">未聚类节点</option></select><input id="search" placeholder="搜索名称或 ID"><span id="count"></span><div class="layout"><section id="list"></section><section><h2 id="title">选择记录</h2><pre id="detail">连线权重等于跨本体边界的原始已断言语义关系数，每条关系计一次。成员边、属性边、来源边和未核实关系不计入。两阶段结果均为候选本体，不代表已完成领域专家验收。</pre></section></div></main><script id="data" type="application/json">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),names=new Map(data.profiles.map(p=>[p.entity,p.name]));for(const o of [...data.anchors,...data.stage2])names.set(o.id,o.label);
for(const [label,value] of [['已有冻结本体',data.summary.existing_frozen_ontologies],['属性阶段新本体',data.summary.new_attribute_ontologies],['关系阶段新本体',data.summary.new_relation_ontologies],['边界连线总权重',data.summary.boundary_weight_total],['未聚类节点',data.summary.unassigned]]){const c=document.createElement('div');c.className='card';const s=document.createElement('strong');s.textContent=value;c.appendChild(s);c.appendChild(document.createTextNode(label));$('stats').appendChild(c)}
function filter(){const mode=$('mode').value,q=$('search').value.trim().toLowerCase();let rows=mode==='attributes'?data.anchors:mode==='relations'?data.stage2:mode==='weights'?data.boundary:data.unassigned;rows=rows.filter(r=>((r.label||names.get(r.entity)||'')+' '+(r.id||r.entity||'')+' '+(r.ontology||'')).toLowerCase().includes(q));$('list').replaceChildren();$('count').textContent=rows.length+' 项';for(const r of rows){const b=document.createElement('button');b.textContent=r.label||names.get(r.entity)||r.entity;if(mode==='weights')b.textContent+=' → '+names.get(r.ontology)+' · 权重 '+r.weight;b.onclick=()=>{$('title').textContent=b.textContent;$('detail').textContent=JSON.stringify(r,null,2)};$('list').appendChild(b)}}$('mode').onchange=filter;$('search').oninput=filter;filter();
</script>'''
    html=html.replace('边界连线总权重','末轮边界连线总权重')
    html=html.replace("['末轮边界连线总权重',data.summary.boundary_weight_total]", "['首轮边界连线总权重',data.summary.first_round_boundary_weight_total],['末轮边界连线总权重',data.summary.boundary_weight_total]")
    html=html.replace("if(mode==='weights')b.textContent+=", "if(mode==='weights')b.textContent='第 '+r.round+' 轮 · '+b.textContent; if(mode==='weights')b.textContent+=")
    html=html.replace("JSON.stringify(r,null,2)","JSON.stringify(mode==='weights'?{...r,connections:r.connection_ids.map(k=>data.connections[k]||{id:k})}:r,null,2)")
    html=html.replace('<select id="mode">','<h2>逐轮关系权重图</h2><p>左侧是本轮固定的本体，右侧是本轮产生的新本体。灰色节点仅汇总展示尚未聚类的连通节点，不代表新本体。线上数字是原始连线数量之和；点击可追溯原文。</p><select id="graphRound"></select><div id="network" style="max-height:620px;overflow:auto;border:1px solid #29405a;border-radius:10px;margin-bottom:25px"></div><select id="mode">')
    graph_script='''
const NS='http://www.w3.org/2000/svg';function svgNode(tag,attrs){const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n}
for(let r=1;r<=data.summary.relation_rounds;r++){const o=document.createElement('option');o.value=r;o.textContent='第 '+r+' 轮';$('graphRound').appendChild(o)}
function drawNetwork(){const round=Number($('graphRound').value),left=[...data.anchors,...data.stage2.filter(o=>o.round<round)],fresh=data.stage2.filter(o=>o.round===round),rows=data.boundary.filter(r=>r.round===round),owner=new Map();for(const o of fresh)for(const member of o.members)owner.set(member,o.id);const unresolved=[...new Set(rows.filter(r=>!owner.has(r.entity)).map(r=>r.entity))];const right=[...fresh];if(unresolved.length)right.push({id:'display-only',label:'未聚类节点展示汇总（'+unresolved.length+'）',members:unresolved,display_only:true});const H=Math.max(360,left.length*62+60,right.length*62+60),svg=svgNode('svg',{viewBox:'0 0 1380 '+H,width:'100%',style:'min-width:1000px;background:#0e192a'}),pos=new Map();left.forEach((o,i)=>pos.set(o.id,{x:240,y:40+i*62}));right.forEach((o,i)=>pos.set(o.id,{x:1030,y:40+i*Math.max(62,(H-80)/Math.max(1,right.length-1))}));const totals=new Map();for(const r of rows){const target=owner.get(r.entity)||'display-only',key=r.ontology+'|'+target;if(!totals.has(key))totals.set(key,{source:r.ontology,target,weight:0,records:[]});const group=totals.get(key);group.weight+=r.weight;group.records.push(r)}for(const edge of totals.values()){const a=pos.get(edge.source),b=pos.get(edge.target);if(!a||!b)continue;const line=svgNode('path',{d:'M '+a.x+' '+a.y+' C '+(a.x+260)+' '+a.y+' '+(b.x-260)+' '+b.y+' '+b.x+' '+b.y,fill:'none',stroke:'#547695','stroke-width':Math.max(1,Math.sqrt(edge.weight)*1.4),opacity:0.7,style:'cursor:pointer'});line.onclick=()=>{$('title').textContent='累计关系权重 '+edge.weight;$('detail').textContent=JSON.stringify({...edge,records:edge.records.map(r=>({...r,connections:r.connection_ids.map(k=>data.connections[k]||{id:k})}))},null,2)};svg.appendChild(line);const label=svgNode('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2-6,fill:'#b7cbe2','font-size':13});label.textContent=edge.weight;svg.appendChild(label)}for(const [items,color] of [[left,'#4adbb9'],[right,'#b298ff']])for(const o of items){const p=pos.get(o.id),circle=svgNode('circle',{cx:p.x,cy:p.y,r:7,fill:o.display_only?'#8292a9':color,style:'cursor:pointer'});circle.onclick=()=>{$('title').textContent=o.label;$('detail').textContent=JSON.stringify(o,null,2)};svg.appendChild(circle);const text=svgNode('text',{x:p.x+(items===left?-14:14),y:p.y+4,'text-anchor':items===left?'end':'start',fill:'#d8e6f5','font-size':12});text.textContent=o.label.length>29?o.label.slice(0,28)+'…':o.label;const title=svgNode('title',{});title.textContent=o.label;text.appendChild(title);svg.appendChild(text)}$('network').replaceChildren(svg)}$('graphRound').onchange=drawNetwork;drawNetwork();
'''
    html=html.replace('filter();\n</script>', 'filter();\n'+graph_script+'\n</script>')
    path.write_text(html.replace('PAYLOAD',payload),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--attribute-threshold',type=float,default=.45);p.add_argument('--relation-threshold',type=float,default=.5)
    a=p.parse_args();print(json.dumps(build(a.source,a.output,a.attribute_threshold,a.relation_threshold)))
