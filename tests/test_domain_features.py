import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.build_domain_features import features, canonical_value
from scripts.build_knowledge_graph import identifier, SOURCES
from scripts.prepare_entity_research import prepare
from scripts.validate_domain_features import validate


class SemanticFeatureTests(unittest.TestCase):
    def test_shared_values_preserve_subjects_conditions_and_missingness(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'data';source.mkdir();graph=root/'graph';graph.mkdir()
            nodes=[]
            for filename,layer in SOURCES:
                scope=identifier(layer,'record')
                record=dict(task=dict(id='record'),input=dict(PASSAGE='Coal has smooth surfaces.'),
                    annotation=dict(paper_id='paper',entities=[dict(id='e',text='Coal',type='rock')],
                    observations=[dict(object='e',property_text='surface texture',value_text='smooth',evidence_text='Coal has smooth surfaces.',conditions=['polished specimen']),
                                  dict(object='not_an_entity',value_text='rough',evidence_text='Coal has smooth surfaces.')]))
                (source/filename).write_text(json.dumps([record]),encoding='utf-8')
                nodes.extend([dict(id=scope,kind='scope',document_id='paper',passage=record['input']['PASSAGE']),
                    dict(id=identifier(scope,'entity','e'),kind='entity_mention',annotation=record['annotation']['entities'][0],layer=layer)])
            (graph/'nodes.jsonl').write_text('\n'.join(json.dumps(n) for n in nodes),encoding='utf-8')
            (graph/'edges.jsonl').write_text('',encoding='utf-8')
            (source/'checksums.json').write_text('{}',encoding='utf-8')
            prepare(source,root/'research')
            catalog=dict(minerals=[dict(name=n,label=n,properties=[
                dict(feature='habit',value=value,url='https://example.org/reference',section='crystals'),
                dict(feature='surface_texture',value='smooth',url='https://example.org/reference',section='AFM',conditions=dict(surface='basal',sample='sample X'))])
                for n,value in [('Mineral A','platy'),('Mineral B','片状')]])
            path=root/'references.json';path.write_text(json.dumps(catalog),encoding='utf-8')
            result=features(source,graph,root/'research',path,root/'out')
            self.assertEqual(result['entity_mentions'],3)
            self.assertEqual(result['review_queue'],3)
            self.assertEqual(result['reusable_values'],2)
            self.assertEqual(result['candidate_clusters'],1)
            db=sqlite3.connect(str(root/'out/knowledge_graph.sqlite'))
            values=[json.loads(x[0]) for x in db.execute("SELECT payload FROM nodes WHERE kind='feature_value'")]
            self.assertEqual({v['value'] for v in values},{'smooth','platy'})
            claims=[json.loads(x[0]) for x in db.execute("SELECT payload FROM nodes WHERE kind='property_assertion'")]
            self.assertEqual(len(claims),7)
            conditionals=[c for c in claims if c['layer']=='external' and c['conditions']]
            self.assertTrue(all(c['conditions']['sample']=='sample X' for c in conditionals))
            cluster=json.loads(db.execute("SELECT payload FROM nodes WHERE kind='candidate_cluster'").fetchone()[0])
            self.assertEqual(len(cluster['members']),2)
            self.assertEqual(len(cluster['shared_values']),1)
            self.assertEqual(next(v for v in values if v['id']==cluster['shared_values'][0])['key'],'habit')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall());db.close()
            self.assertTrue(validate(root/'out')['passed'])
            claims_path=root/'out/property_assertions.jsonl'
            saved=claims_path.read_text(encoding='utf-8')
            corrupted=[json.loads(line) for line in saved.splitlines()]
            corrupted[0]['raw_value']='fabricated'
            claims_path.write_text('\n'.join(json.dumps(c) for c in corrupted),encoding='utf-8')
            with self.assertRaises(ValueError):validate(root/'out')
            claims_path.write_text(saved,encoding='utf-8')
            with self.assertRaises(ValueError):features(source,graph,root/'research',path,root/'out')

    def test_controlled_synonyms_do_not_equate_unrelated_features(self):
        self.assertEqual(canonical_value('surface_texture','光滑'),'smooth')
        self.assertEqual(canonical_value('habit','片状'),'platy')
        self.assertNotEqual(canonical_value('habit','tabular'),canonical_value('habit','platy'))
        self.assertEqual(canonical_value('chemical_description','光滑'),'光滑')
        self.assertEqual(canonical_value('formula',' Al2Si2O5(OH)4 '),'Al2Si2O5(OH)4')
        self.assertNotEqual(canonical_value('formula','Co'),canonical_value('formula','CO'))
