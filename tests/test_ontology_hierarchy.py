import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from scripts.visualize_ontology_hierarchy import build_hierarchy, render


def write_rows(root, filename, rows):
    (root / filename).write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def fixture(root):
    root.mkdir()
    profiles = []
    for key, name, kind in [
        ('a', 'Kaolinite', 'mineral'), ('b', 'kaolinite', 'mineral'),
        ('c', 'Fe', 'chemical element'), ('d', 'Co', 'chemical element'),
        ('e', 'China', 'geographic location'), ('f', 'India', 'geographic location'),
        ('x', 'Quartz', 'mineral'), ('y', 'quartz', 'mineral'),
        ('u', '</script><script>original', 'sample or material'),
    ]:
        profiles.append(dict(entity=key, name=name, type=kind, identity_level='source_mention',
                             intrinsic_attributes=[], reference_derivations=[]))

    def ontology(key, members, stage, round_number=None):
        member_type = next(profile['type'] for profile in profiles
                           if profile['entity'] == members[0])
        result = dict(id=key, label='Original ' + key, members=members, stage=stage,
                      ontology_frozen=True, member_type=member_type,
                      member_identity_level='source_mention', minimum_similarity=0.5,
                      shared_attributes=[], formal_ontology_axiom=False)
        if round_number is not None:
            result['round'] = round_number
        return result

    write_rows(root, 'entity_profiles.jsonl', profiles)
    write_rows(root, 'existing_ontologies.jsonl', [ontology('E', ['x', 'y'], 'existing')])
    write_rows(root, 'attribute_ontologies.jsonl', [ontology('A', ['a', 'b'], 'attributes')])
    write_rows(root, 'relation_ontologies.jsonl', [ontology('B', ['c', 'd'], 'relations', 1),
                                                ontology('C', ['e', 'f'], 'relations', 2)])
    # r7 supports e in rounds 1 and 2, but C is only born in round 2.
    # r4 supports a never-clustered object across all rounds, including the stop.
    boundary = []
    for round_number, entity, anchor, ids in [
        (1, 'c', 'A', ['r1', 'r2']), (1, 'd', 'A', ['r3']),
        (1, 'e', 'A', ['r7']), (1, 'u', 'A', ['r4']),
        (2, 'e', 'A', ['r7']), (2, 'e', 'B', ['r5']),
        (2, 'f', 'B', ['r6']), (2, 'u', 'A', ['r4']),
        (3, 'u', 'A', ['r4']),
    ]:
        boundary.append(dict(round=round_number, entity=entity, ontology=anchor,
                             weight=len(ids), connection_ids=ids))
    write_rows(root, 'boundary_weights.jsonl', boundary)
    write_rows(root, 'unassigned.jsonl', [dict(entity='u', reason='no_supported_relation_peer')])
    write_rows(root, 'relation_rounds.jsonl', [dict(round=1, new_ontology_ids=['B']),
                                             dict(round=2, new_ontology_ids=['C']),
                                             dict(round=3, new_ontology_ids=[])])
    return boundary


def descendants(node):
    yield node
    for child in node.get('children', []):
        yield from descendants(child)


class HierarchyTests(unittest.TestCase):
    def test_membership_hierarchy_and_birth_round_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            fixture(source)
            result = build_hierarchy(source)
            self.assertEqual({k: result['summary'][k] for k in
                              ['objects', 'ontologies', 'clustered_members', 'unassigned']},
                             dict(objects=9, ontologies=4, clustered_members=8, unassigned=1))
            nodes = list(descendants(result['root']))
            self.assertEqual(len(nodes), len({node['id'] for node in nodes}))
            entities = {node['id']: node for node in nodes if node['kind'] == 'entity'}
            self.assertEqual(set(entities), set('abcdefxyu'))
            self.assertEqual(entities['a']['label'], '高岭石')
            self.assertEqual(entities['d']['label'], 'Co')
            ontology_nodes = {node['id']: node for node in nodes if node['kind'] == 'ontology'}
            self.assertEqual(ontology_nodes['E']['members'], ['x', 'y'])
            self.assertEqual(ontology_nodes['A']['members'], ['a', 'b'])
            for ontology in ontology_nodes.values():
                self.assertEqual([node['id'] for node in ontology['children']], ontology['members'])
                self.assertTrue(all(node['kind'] == 'entity' for node in ontology['children']))
            stop_round = [node for node in nodes if node['kind'] == 'round' and node['round'] == 3]
            self.assertEqual(len(stop_round), 1)
            self.assertEqual(stop_round[0]['children'], [])
            edges = {(row['source'], row['target']): row for row in result['dependency_edges']}
            self.assertEqual(set(edges), {('A', 'B'), ('A', 'C'), ('B', 'C')})
            self.assertEqual(edges['A', 'B']['weight'], 3)
            self.assertEqual(edges['A', 'C']['connection_ids'], ['r7'])
            self.assertEqual(edges['A', 'C']['weight'], 1)
            self.assertEqual(edges['B', 'C']['weight'], 2)
            self.assertEqual(sum(row['weight'] for row in edges.values()), 6)
            self.assertNotIn('r4', [key for row in edges.values() for key in row['connection_ids']])

    def test_rejects_duplicate_members_and_missing_object_coverage(self):
        for bad_case in ['duplicate', 'missing']:
            with self.subTest(bad_case=bad_case), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / 'source'
                fixture(source)
                if bad_case == 'duplicate':
                    path = source / 'relation_ontologies.jsonl'
                    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                    rows[0]['members'].append('a')
                    write_rows(source, path.name, rows)
                else:
                    write_rows(source, 'unassigned.jsonl', [])
                with self.assertRaises(ValueError):
                    build_hierarchy(source)

    def test_rejects_inaccurate_weights_and_future_anchors(self):
        for bad_case in ['weight', 'future_anchor', 'repeated_evidence']:
            with self.subTest(bad_case=bad_case), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / 'source'
                boundary = fixture(source)
                if bad_case == 'weight':
                    boundary[0]['weight'] += 1
                elif bad_case == 'future_anchor':
                    boundary[0]['ontology'] = 'B'
                else:
                    boundary[1]['connection_ids'] = ['r1']
                write_rows(source, 'boundary_weights.jsonl', boundary)
                with self.assertRaises(ValueError):
                    build_hierarchy(source)

    def test_html_safely_embeds_original_names_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            fixture(source)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            output = root / 'view' / 'hierarchy.html'
            summary = render(source, output)
            self.assertEqual(summary['ontologies'], 4)
            html = output.read_text(encoding='utf-8')
            self.assertNotIn('</script><script>original', html)
            payloads = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S)
            self.assertEqual(len(payloads), 1)
            payload = json.loads(payloads[0])
            entity = next(node for node in descendants(payload['root']) if node['id'] == 'u')
            self.assertEqual(entity['original_name'], '</script><script>original')
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in source.iterdir()})
            with self.assertRaises(ValueError):
                render(source, source / 'entity_profiles.jsonl')
