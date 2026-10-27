import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from scripts.build_knowledge_graph import build, SOURCES


class GraphTests(unittest.TestCase):
    def test_provenance_layers_and_qualifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'data'; source.mkdir()
            schema=dict(entities=[dict(label='rock')],relations=[dict(label='part of')])
            (source/'schema_catalog.json').write_text(json.dumps(schema),encoding='utf-8')
            for name,layer in SOURCES:
                record=dict(task=dict(id='same'),input=dict(PASSAGE='Coal rock'),
                    annotation=dict(paper_id='paper',entities=[dict(id='a',text='Coal',type='rock'),dict(id='b',text='rock',type='rock')],
                    relations=[dict(head='a',tail='b',label='part of',modality='asserted',evidence_text='Coal rock')],
                    qualified_statements=[dict(head='a',tail='b',predicate='part of',conditions=['only here'],modality='possible')]),
                    binary_training_eligible=True,source_verification=dict(verified=True))
                (source/name).write_text(json.dumps([record]),encoding='utf-8')
            checks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            (source/'checksums.json').write_text(json.dumps(checks),encoding='utf-8')
            result=build(source,root/'graph')
            self.assertTrue(result['validation_passed'])
            self.assertEqual(result['counts']['default_fact_statements'],1)
            db=sqlite3.connect(str(root/'graph/knowledge_graph.sqlite'))
            self.assertEqual(db.execute('SELECT count(*) FROM default_facts').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM nodes WHERE kind='entity_mention'").fetchone()[0],6)
            db.close()
            with self.assertRaises(ValueError): build(source,root/'graph')
            (source/SOURCES[0][0]).write_text('[]',encoding='utf-8')
            with self.assertRaises(ValueError): build(source,root/'bad')
