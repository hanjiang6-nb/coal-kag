import json
from pathlib import Path
import tempfile
import unittest
import re
import subprocess

from scripts.cluster_two_stage_ontologies import build, count_boundary, weighted_jaccard, existing_ontologies
from scripts.validate_two_stage_ontologies import validate


def fixture(root):
    root.mkdir()
    nodes=[];edges=[]
    for key,kind in [('a','mineral'),('b','mineral'),('u','chemical element'),('v','chemical element')]:
        nodes.append(dict(id=key,kind='entity_mention',annotation=dict(text=key,type=kind),layer='binary'))
    for feature,value in [('habit','platy'),('formula','Al2Si2O5(OH)4')]:
        nodes.extend([dict(id='feature-'+feature,kind='semantic_feature',key=feature),
                      dict(id='value-'+feature,kind='feature_value',key=feature,value=value)])
        for subject in ['a','b']:
            nodes.append(dict(id=subject+'-'+feature,kind='property_assertion',subject=subject,
                feature='feature-'+feature,value_node='value-'+feature,layer='external',conditions={},cluster_eligible=True))
    for index,(head,tail) in enumerate([('a','u'),('b','u'),('a','u'),('a','v'),('b','v'),('a','b')]):
        key='r'+str(index)
        nodes.append(dict(id=key,kind='relations',predicate='occurs in',default_fact_eligible=True))
        edges.extend([dict(id=key+'H',source=key,target=head,type='HEAD'),dict(id=key+'T',source=key,target=tail,type='TAIL')])
    for name,rows in [('nodes',nodes),('edges',edges)]:
        (root/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    return nodes,edges


class TwoStageTests(unittest.TestCase):
    def test_attribute_ontology_then_weight_sum_relation_ontology_and_frozen_rerun(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';fixture(source)
            result=build(source,root/'out')
            self.assertEqual(result['new_attribute_ontologies'],1)
            self.assertEqual(result['attribute_members'],2)
            self.assertEqual(result['new_relation_ontologies'],1)
            self.assertEqual(result['relation_members'],2)
            self.assertEqual(result['first_round_boundary_weight_total'],5)
            self.assertEqual(result['boundary_weight_total'],0)
            self.assertEqual(result['relation_rounds'],2)
            weights=[json.loads(s) for s in (root/'out/boundary_weights.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({w['entity']:w['weight'] for w in weights},{'u':3,'v':2})
            self.assertTrue(all(len(w['connection_ids'])==w['weight'] for w in weights))
            # A later run must not change or merge either existing ontology.
            rerun=build(root/'out',root/'rerun')
            self.assertEqual(rerun['existing_frozen_ontologies'],2)
            self.assertEqual(rerun['new_attribute_ontologies'],0)
            self.assertEqual(rerun['new_relation_ontologies'],0)
            before={n['id']:n for n in map(json.loads,(root/'out/nodes.jsonl').read_text(encoding='utf-8').splitlines()) if n['kind']=='ontology'}
            after={n['id']:n for n in map(json.loads,(root/'rerun/nodes.jsonl').read_text(encoding='utf-8').splitlines()) if n['kind']=='ontology'}
            self.assertEqual(before,after)
            self.assertTrue(validate(source,root/'out')['passed'])
            self.assertEqual(validate(root/'out',root/'rerun')['existing_ontologies_preserved'],2)
            weights[0]['weight']+=1
            (root/'out/boundary_weights.jsonl').write_text(''.join(json.dumps(w)+'\n' for w in weights),encoding='utf-8')
            with self.assertRaises(ValueError):validate(source,root/'out')
            with self.assertRaises(ValueError):build(source,root/'out')

    def test_counts_and_similarity_use_weight_magnitude_not_boolean_edges(self):
        a={'one':3,'two':2};b={'one':1,'two':2}
        self.assertAlmostEqual(weighted_jaccard(a,b),3/5)
        self.assertEqual(weighted_jaccard({},{}),0)
        rows,matrix=count_boundary([dict(id='r',source='a',target='u',weight=1),
                                    dict(id='internal',source='a',target='b',weight=1)],{'a':'C','b':'C'},{'u'})
        self.assertEqual(matrix,{'u':{'C':1}})
        self.assertEqual(rows[0]['connection_ids'],['r'])

    def test_new_ontologies_become_next_round_anchors_without_merging_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';nodes,edges=fixture(source)
            nodes.extend([dict(id=k,kind='entity_mention',annotation=dict(text=k,type='geographic location'),layer='binary') for k in ['w','x']])
            for key,head,tail in [('later1','u','w'),('later2','v','x')]:
                nodes.append(dict(id=key,kind='relations',predicate='located in',default_fact_eligible=True))
                edges.extend([dict(id=key+'H',source=key,target=head,type='HEAD'),dict(id=key+'T',source=key,target=tail,type='TAIL')])
            for name,items in [('nodes',nodes),('edges',edges)]:
                (source/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in items),encoding='utf-8')
            result=build(source,root/'out')
            self.assertEqual(result['new_relation_ontologies'],2)
            self.assertEqual(result['relation_rounds'],3)
            groups=[json.loads(s) for s in (root/'out/relation_ontologies.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([(g['round'],set(g['members'])) for g in groups],[(1,{'u','v'}),(2,{'w','x'})])
            self.assertTrue(validate(source,root/'out')['passed'])

    def test_overlapping_frozen_ontologies_are_rejected(self):
        nodes={'a':dict(id='a',kind='entity_mention'),
               'C':dict(id='C',kind='ontology',members=['a']),
               'D':dict(id='D',kind='ontology',members=['a'])}
        with self.assertRaises(ValueError):existing_ontologies(nodes)

    def test_round_visualization_and_weight_evidence_interactions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';fixture(source);build(source,root/'out')
            html=(root/'out/两阶段本体聚类.html').read_text(encoding='utf-8')
            payload=re.search(r'type="application/json">(.*?)</script>',html,re.S)[1]
            script=re.findall(r'<script>(.*?)</script>',html,re.S)[0]
            code='''const vm=require('vm');const elements=new Map();function node(){return {value:'',textContent:'',children:[],appendChild(n){this.children.push(n)},replaceChildren(...n){this.children=n},setAttribute(){}}}function get(id){if(!elements.has(id))elements.set(id,node());return elements.get(id)}global.document={getElementById:get,createElement:node,createElementNS:node,createTextNode:text=>({textContent:text})};get('mode').value='attributes';get('graphRound').value='1';get('data').textContent=PAYLOAD;vm.runInThisContext(SCRIPT);if(get('network').children.length!==1)throw Error('graph missing');get('mode').value='weights';vm.runInThisContext('filter()');get('list').children[0].onclick();if(!get('detail').textContent.includes('connection_ids'))throw Error('evidence missing');get('graphRound').value='2';vm.runInThisContext('drawNetwork()');if(get('network').children.length!==1)throw Error('round switch failed');console.log('PASS two-stage browser interactions');'''
            code=code.replace('PAYLOAD',json.dumps(payload)).replace('SCRIPT',json.dumps(script))
            (root/'ui_test.js').write_text(code,encoding='utf-8')
            result=subprocess.run(['node',str(root/'ui_test.js')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_tags_and_genesis_are_not_intrinsic_attributes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';nodes,edges=fixture(source)
            for n in nodes:
                if n['kind'] in {'semantic_feature','feature_value'}:n['key']='genesis' if n['id'].endswith('habit') else 'source_path'
            (source/'nodes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in nodes),encoding='utf-8')
            result=build(source,root/'out')
            self.assertEqual(result['new_attribute_ontologies'],0)
            self.assertEqual(result['new_relation_ontologies'],0)
            self.assertEqual(result['boundary_weight_total'],0)
