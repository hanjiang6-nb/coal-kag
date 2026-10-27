# -*- coding: utf-8 -*-
"""Reusable semantic features, evidence-scoped assertions, and candidate clusters.

Search completion never implies identity verification or property verification.
Reference facts are supplied in a local reviewed catalog, not scraped from snippets.
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
from build_knowledge_graph import identifier, load, SOURCES
from visualize_knowledge_graph import render

FEATURES = {
    'formula': ('化学式','composition'),
    'crystal_family': ('晶族（保留来源分类口径）','crystallography'),
    'habit': ('晶形与集合体形态','morphology'),
    'cleavage_quality': ('解理发育程度','physical'),
    'mohs_hardness': ('莫氏硬度','physical'),
    'luster': ('光泽','optical'),
    'fracture': ('断口形态','morphology'),
    'surface_texture': ('表面纹理','surface'),
    'grain_size': ('粒径','size'),
    'roundness': ('圆度','morphology'),
    'genesis': ('成因','genesis'),
    'morphology': ('形貌','morphology'),
    'unclassified_observation': ('属性维度尚未明确的观测','unclassified'),
}
VALUE_ALIASES = {
    'surface_texture': {'平滑':'smooth','光滑':'smooth','smooth':'smooth',
                        '台阶状':'stepped','stepped':'stepped','不规则':'irregular'},
    'habit': {'片状':'platy','plate-like':'platy','platy':'platy',
              '柱状':'prismatic','prismatic':'prismatic'},
    'luster': {'金属光泽':'metallic','metallic':'metallic',
               '玻璃光泽':'vitreous','vitreous':'vitreous','珍珠光泽':'pearly'},
}


def normalized(value):
    return re.sub(r'\s+',' ',str(value).strip()).casefold()


def canonical_value(feature,value):
    if not isinstance(value,str):return value
    if feature=='formula':return re.sub(r'\s+','',value)
    value=normalized(value)
    return VALUE_ALIASES.get(feature,{}).get(value,value)


def observation_feature(item):
    name=normalized(item.get('property_text') or '')
    value=normalized(item.get('value_text') or '')
    if re.search(r'grain size|particle size|^sizes?$',name):
        return 'grain_size'
    if name=='roundness': return 'roundness'
    if name in {'origin','genesis'} or value in {'authigenic','epigenetic','detrital terrigenous origin'}:
        return 'genesis'
    if name in {'forms','morphology'} or value in {'lamellar','sharp edges','multi-loop layers'}:
        return 'morphology'
    if name in {'hardness','mohs hardness'}: return 'mohs_hardness'
    if name in {'surface roughness','surface texture','surface smoothness'}: return 'surface_texture'
    if not name: return 'unclassified_observation'
    return 'observed_' + re.sub(r'[^\w]+','_',name).strip('_')


def features(source, graph, research, reference, output):
    if output.exists(): raise ValueError('Use a new output directory')
    nodes={n['id']:n for n in (json.loads(s) for s in (graph/'nodes.jsonl').read_text(encoding='utf-8').splitlines())}
    edges=[json.loads(s) for s in (graph/'edges.jsonl').read_text(encoding='utf-8').splitlines()]
    assertions=[]; definitions={}; values={}; issues=[]; profiles={}; counts=Counter()
    def put(kind,parts,**payload):
        key=identifier(kind,*parts)
        nodes[key]=dict(id=key,kind=kind,**payload)
        return key
    def link(a,b,kind):
        edges.append(dict(id=identifier(a,b,kind),source=a,target=b,type=kind))
    def add(subject,key,value,unit,layer,evidence,origin,conditions=None,cluster_eligible=False):
        feature=identifier('semantic_feature',key)
        label,dimension=FEATURES.get(key,(key,'unclassified'))
        definitions[feature]=dict(id=feature,kind='semantic_feature',key=key,
            label=label,dimension=dimension,
            definition_status='controlled' if key in FEATURES else 'raw_observation_dimension_pending_normalization')
        canonical=canonical_value(key,value)
        vid=identifier('feature_value',key,canonical,unit)
        values[vid]=dict(id=vid,kind='feature_value',feature=feature,key=key,value=canonical,unit=unit)
        aid=put('property_assertion',[subject,origin,key,value,unit,conditions],subject=subject,
            feature=feature,value_node=vid,layer=layer,raw_value=value,evidence=evidence,
            origin=origin,conditions=conditions or {},cluster_eligible=cluster_eligible,
            human_verified=False)
        link(subject,aid,'HAS_PROPERTY_ASSERTION');link(aid,vid,'HAS_VALUE')
        link(aid,feature,'USES_FEATURE'); link(vid,feature,'VALUE_OF')
        assertions.append(nodes[aid]);counts[layer+'_properties']+=1
        if cluster_eligible and not conditions: profiles.setdefault(subject,set()).add(vid)
    for filename,layer in SOURCES:
        for record in load(source/filename):
            scope=identifier(layer,record['task']['id'])
            entities={e['id']:e for e in record['annotation']['entities']}
            for index,item in enumerate(record['annotation'].get('observations',[])):
                obj=item.get('object')
                if obj not in entities:
                    issues.append(dict(code='OBSERVATION_OBJECT_UNRESOLVED',scope=scope,observation=item));continue
                if item.get('value_text') is None:
                    issues.append(dict(code='OBSERVATION_VALUE_MISSING',scope=scope,observation=item));continue
                evidence=item.get('evidence_text') or ''
                if not evidence or evidence not in record['input']['PASSAGE']:
                    issues.append(dict(code='OBSERVATION_EVIDENCE_INVALID',scope=scope,observation=item));continue
                owner=identifier(scope,'entity',obj)
                add(owner,observation_feature(item),item['value_text'],item.get('unit_text'),layer,
                    evidence,dict(file=filename,scope=scope,observation_index=index),
                    conditions={k:v for k,v in item.items() if k not in {'value_text','evidence_text'}},
                    cluster_eligible=False)
    catalog=load(reference)
    aliases={}
    for mineral in catalog['minerals']:
        concept=put('reference_concept',[mineral['name']],name=mineral['name'],label=mineral['label'],
                    identity_status='reference_species_not_specific_sample')
        for alias in [mineral['name']]+mineral.get('aliases',[]): aliases[normalized(alias)]=concept
        for index,prop in enumerate(mineral['properties']):
            if not prop.get('url','').startswith('https://') or not prop.get('section'):
                raise ValueError('External property must have a verified source and locator')
            add(concept,prop['feature'],prop['value'],prop.get('unit'),'external',None,
                dict(url=prop['url'],section=prop['section'],accessed='2026-10-27',catalog_index=index),
                conditions=prop.get('conditions',{}),cluster_eligible=not prop.get('conditions'))
    mention_coverage=[]
    inventory=load(research/'inventory.json')
    batch_by_entity={}
    for path in sorted(research.glob('search_[0-9][0-9][0-9].json')):
        batch=load(path)
        for query in batch['queries']:
            batch_by_entity[query['entity_id']]=dict(batch=path.name,status=batch['status'],candidate_sources=batch['candidate_sources'])
    own_properties=Counter(a['subject'] for a in assertions if a['layer']!='external')
    for item in inventory:
        reference_concept=aliases.get(normalized(item['name'])) if item['type']=='mineral' else None
        for mention in item['mentions']:
            owner=identifier(identifier(mention['layer'],mention['scope_id']),'entity',mention['entity_id'])
            if reference_concept: link(owner,reference_concept,'HAS_SPECIES_REFERENCE')
            mention_coverage.append(dict(entity=owner,research_unit=item['id'],name=item['name'],type=item['type'],
                specific_observations=own_properties[owner],reference_concept=reference_concept,
                search=batch_by_entity.get(item['id'],dict(status='not_searched')),
                status='specific_observation_available' if own_properties[owner] else ('species_reference_only' if reference_concept else 'property_verification_pending')))
    # Complete-link clustering prevents chains of weak similarities. Only general
    # verified reference features enter this pilot; contextual observations do not.
    clusters=[]
    for subject in sorted(profiles):
        current=profiles[subject]
        match=None
        for cluster in clusters:
            if all(len(current & profiles[other])/max(1,len(current | profiles[other]))>=0.35 for other in cluster):
                match=cluster;break
        if match is None: clusters.append([subject])
        else: match.append(subject)
    cluster_records=[]
    for group in clusters:
        shared=set.intersection(*(profiles[s] for s in group))
        cid=put('candidate_cluster',group,members=group,shared_values=sorted(shared),
                status='candidate_only_not_ontology_axiom',method='complete-link Jaccard >= 0.35',
                label=' / '.join(nodes[s]['label'] for s in group))
        cluster_records.append(nodes[cid])
        for subject in group: link(subject,cid,'CANDIDATE_MEMBER_OF')
    nodes.update(definitions);nodes.update(values)
    output.mkdir(parents=True)
    for name,rows in [('nodes.jsonl',nodes.values()),('edges.jsonl',edges),('property_assertions.jsonl',assertions),
                      ('entity_coverage.jsonl',mention_coverage),('review_queue.jsonl',issues),('candidate_clusters.jsonl',cluster_records)]:
        with (output/name).open('w',encoding='utf-8') as stream:
            for row in rows: stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    db=sqlite3.connect(str(output/'knowledge_graph.sqlite'));db.execute('PRAGMA foreign_keys=ON')
    db.executescript('CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,payload TEXT);CREATE TABLE edges(id TEXT PRIMARY KEY,source TEXT REFERENCES nodes(id),target TEXT REFERENCES nodes(id),type TEXT);')
    db.executemany('INSERT INTO nodes VALUES(?,?,?)',[(n['id'],n['kind'],json.dumps(n,ensure_ascii=False)) for n in nodes.values()])
    db.executemany('INSERT OR IGNORE INTO edges VALUES(:id,:source,:target,:type)',edges)
    check=db.execute('PRAGMA integrity_check').fetchone()[0];foreign=db.execute('PRAGMA foreign_key_check').fetchall();db.commit();db.close()
    summary=dict(entity_mentions=len(mention_coverage),research_units=len(inventory),
        search_statuses=dict(Counter(v.get('search',{}).get('status') for v in mention_coverage)),
        property_coverage=dict(Counter(v['status'] for v in mention_coverage)),
        feature_definitions=len(definitions),reusable_values=len(values),assertions=len(assertions),
        source_property_counts=dict(counts),verified_reference_species=len(catalog['minerals']),
        candidate_clusters=len(clusters),review_queue=len(issues),
        database_integrity=check,foreign_key_errors=len(foreign),
        all_entities_have_verified_properties=all(v['status']!='property_verification_pending' for v in mention_coverage),
        formal_ontology_complete=False)
    manifest_paths=[reference,source/'checksums.json',graph/'nodes.jsonl',graph/'edges.jsonl',research/'inventory.json']
    manifest_paths+=list(research.glob('search_[0-9][0-9][0-9].json'))
    summary['input_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in manifest_paths}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    visualization(nodes,assertions,cluster_records,output)
    research_review(inventory,batch_by_entity,mention_coverage,output)
    return summary


def research_review(inventory,batches,coverage,output):
    """Expose every research unit, including those with no verified properties."""
    records=[]
    by_unit=defaultdict(list)
    for row in coverage:by_unit[row['research_unit']].append(row)
    for item in inventory:
        records.append(dict(item,coverage=by_unit[item['id']],research=batches.get(item['id'],{})))
    payload=json.dumps(records,ensure_ascii=False).replace('<','\\u003c')
    template='''<!doctype html><meta charset="utf-8"><title>全实体查询与属性覆盖</title><style>body{font:15px system-ui;margin:28px;background:#101827;color:#dce8f4}input,select,button{padding:10px;margin:8px;background:#203149;color:#fff;border:1px solid #456;border-radius:5px}main{display:grid;grid-template-columns:350px 1fr;gap:24px}#list{max-height:75vh;overflow:auto}button{display:block;width:95%;text-align:left}pre{white-space:pre-wrap;word-break:break-word;line-height:1.7}a{color:#6ee7c7}small{color:#a8b7cb}</style><h1>全实体查询与属性覆盖</h1><p>已查询不代表已核实。候选来源为同批检索结果，须逐项确认相关性；来源与条件不用于实体相似度。</p><input id="query" placeholder="检索实体名称"><span id="count"></span><main><section id="list"></section><section><h2 id="title">选择实体</h2><pre id="detail"></pre><div id="sources"></div></section></main><script id="records" type="application/json">PAYLOAD</script><script>
const rows=JSON.parse(document.getElementById('records').textContent),$=s=>document.getElementById(s);
function show(r){$('title').textContent=r.name+' / '+r.type;$('detail').textContent=JSON.stringify({query:r.query,search:r.research.status,coverage:r.coverage.map(c=>({status:c.status,specific_observations:c.specific_observations,reference_concept:c.reference_concept})),mentions:r.mentions},null,2);$('sources').replaceChildren();for(const s of r.research.candidate_sources||[]){if(!/^https?:\\/\\//.test(s.url))continue;const p=document.createElement('p'),a=document.createElement('a');a.textContent=s.title;a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';p.appendChild(a);$('sources').appendChild(p)}}
function filter(){const q=$('query').value.trim().toLowerCase(),matches=rows.filter(r=>(r.name+' '+r.type).toLowerCase().includes(q));$('list').replaceChildren();$('count').textContent=matches.length+' / '+rows.length+' 项';for(const r of matches){const b=document.createElement('button');b.textContent=r.name+' · '+r.type;b.onclick=()=>show(r);$('list').appendChild(b)}}$('query').oninput=filter;filter();
</script>'''
    (output/'全部实体检索清单.html').write_text(template.replace('PAYLOAD',payload),encoding='utf-8')


def visualization(nodes,assertions,clusters,output):
    """Project concepts and shared feature values into the offline explorer."""
    view=output/'view';view.mkdir()
    exported={};relations=[];links=[]
    def export(key):
        if key in exported:return
        n=nodes[key];kind=n['kind']
        if kind=='entity_mention': label=n['annotation']['text'];type_=n['annotation']['type']
        elif kind=='reference_concept': label=n['label'];type_='矿物参考概念'
        elif kind=='feature_value':label=n['key']+' = '+str(n['value'])+(' '+n['unit'] if n['unit'] else '');type_='可复用领域特征值'
        else:label='候选簇：'+n['label'];type_='候选聚类'
        layer={'reference_concept':'concept','feature_value':'feature','candidate_cluster':'cluster'}.get(kind,n.get('layer','binary'))
        if kind=='feature_value':
            label=FEATURES.get(n['key'],(n['key'],''))[0]+' = '+str(n['value'])+(' '+n['unit'] if n['unit'] else '')
        exported[key]=dict(id=key,kind='entity_mention',annotation=dict(text=label,type=type_),layer=layer)
    for a in assertions:
        export(a['subject']);export(a['value_node']);scope='scope-'+a['id']
        exported[scope]=dict(id=scope,kind='scope',document_id=a['origin'].get('url',a['origin'].get('file','')),passage=a['evidence'] or '外部文献概括；完整来源、定位和条件见本条标注。')
        predicate=FEATURES.get(nodes[a['feature']]['key'],(nodes[a['feature']]['key'],''))[0]
        if a['conditions']:predicate+='（有条件）'
        relations.append(dict(id=a['id'],kind='relations',scope=scope,layer='binary' if a['layer']=='external' else a['layer'],predicate=predicate,default_fact_eligible=a['layer']=='external',annotation=a))
        links.extend([dict(source=a['id'],target=a['subject'],type='HEAD'),dict(source=a['id'],target=a['value_node'],type='TAIL')])
    for cluster in clusters:
        export(cluster['id'])
        for member in cluster['members']:
            export(member);rid=identifier(member,cluster['id']);scope='scope-'+rid
            exported[scope]=dict(id=scope,kind='scope',document_id='candidate clustering',passage='属性相似性候选，不是正式本体分类。')
            relations.append(dict(id=rid,kind='relations',scope=scope,layer='binary',predicate='候选聚类',default_fact_eligible=True,annotation=cluster))
            links.extend([dict(source=rid,target=member,type='HEAD'),dict(source=rid,target=cluster['id'],type='TAIL')])
    for name,rows in [('nodes',list(exported.values())+relations),('edges',links)]:
        (view/(name+'.jsonl')).write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows),encoding='utf-8')
    target=output/'领域特征图谱.html';render(view,target)
    html=target.read_text(encoding='utf-8').replace('默认银标事实','外部参考与候选聚类').replace('普通样本（含转述）','外部参考及原文普通层').replace('煤地质','领域特征')
    html=html.replace('基于标注证据的关系视图 · 本地离线 · 银标数据，未经人工金标验收','共享特征值 · 外部参考与样品观测分开 · 聚类仅为候选')
    html=html.replace("const colors={binary:","const colors={concept:'#46d5ba',feature:'#f5b85a',cluster:'#a78bfa',binary:")
    html=html.replace('placeholder="例如 coal、Formation、煤"','placeholder="输入实体名称；清空可查看全图" value="高岭石"')
    html=html.replace('每个节点是原文中的实体提及。同名节点不会自动合并。观测值与统计记录保留在 SQLite 中，本图显示有明确首尾实体的普通及限定关系。',
        '绿：矿物参考概念；黄：共享领域特征值；紫：候选聚类。默认聚焦高岭石，清空搜索可看全图。点击属性连线查看条件和外部来源。')
    html=html.replace('普通样本<br>','矿物参考概念<br>').replace('扩展 / 限定<br>','共享特征值<br>').replace('待核查</div>','待核查观测</div>')
    html=html.replace('个实体提及','个概念 / 特征节点')
    focus_script='''
function focusLayout(){const q=$('search').value.trim(),centers=visible.filter(n=>n.type==='矿物参考概念'&&n.label.includes(q));if(!q||centers.length!==1)return false;const center=centers[0],leaves=visible.filter(n=>n!==center);center.x=-300;center.y=0;leaves.forEach((n,i)=>{n.x=0;n.y=(i-(leaves.length-1)/2)*58;n.vx=0;n.vy=0});zoom=Math.min(.95,(H-140)/Math.max(200,leaves.length*58));pan={x:-20,y:0};frame=1000;draw();return true}
for(const id of ['search','layer','type','predicate'])$(id).addEventListener(id==='search'?'input':'change',focusLayout);$('reset').onclick=()=>{if(!focusLayout())fit()};focusLayout();
'''
    html=html.replace('</script></html>',focus_script+'</script></html>')
    target.write_text(html,encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('data'));p.add_argument('--graph',type=Path,required=True);p.add_argument('--research',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(features(a.source,a.graph,a.research,a.reference,a.output)))
