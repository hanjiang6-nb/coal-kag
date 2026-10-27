import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from scripts.cluster_minimal_ontologies import infer, build, validate


def write_rows(root, filename, rows):
    (root / filename).write_text(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def fixture(root, species=None, cosmetic_only=False, composition_only=False, overlap=False):
    """Four species and repeated mentions, rather than four specimen identities."""
    root.mkdir()
    specifications = {
        'calcite': [('formula', 'CaCO3'), ('crystal_family', 'hexagonal'),
                    ('cleavage_quality', 'perfect'), ('mohs_hardness', [3, 3])],
        'dolomite': [('formula', 'CaMg(CO3)2'), ('crystal_family', 'hexagonal'),
                     ('cleavage_quality', 'perfect'), ('mohs_hardness', [3.5, 4])],
        'pyrite': [('formula', 'FeS2'), ('crystal_family', 'cubic'),
                   ('mohs_hardness', [6, 6.5])],
        'marcasite': [('formula', 'FeS2'), ('crystal_family', 'orthorhombic'),
                      ('mohs_hardness', [6, 6.5])],
    }
    if overlap:
        specifications['aragonite'] = [('formula', 'CaCO3'), ('crystal_family', 'orthorhombic'),
                                       ('mohs_hardness', [3, 3])]
    selected = list(species or specifications)
    nodes, edges, values = [], [], {}

    def property_value(owner, feature, value):
        feature_id = 'feature:' + feature
        if not any(node['id'] == feature_id for node in nodes):
            nodes.append(dict(id=feature_id, kind='semantic_feature', key=feature))
        token = (feature, json.dumps(value, sort_keys=True))
        if token not in values:
            values[token] = 'value:' + str(len(values))
            nodes.append(dict(id=values[token], kind='feature_value', key=feature,
                              feature=feature_id, value=value))
        nodes.append(dict(id=owner + ':' + feature, kind='property_assertion', subject=owner,
                          feature=feature_id, value_node=values[token], layer='external',
                          conditions={}, cluster_eligible=True))

    for name in selected:
        reference = 'ref:' + name
        nodes.append(dict(id=reference, kind='reference_concept', label=name))
        properties = specifications[name]
        if cosmetic_only:
            properties = [('habit', 'massive'), ('luster', 'metallic'), ('fracture', 'uneven')]
        elif composition_only:
            properties = [row for row in properties if row[0] == 'formula']
        for feature, value in properties:
            property_value(reference, feature, value)
        for index in range(2 if name == 'calcite' else 1):
            mention = name + ':' + str(index)
            nodes.append(dict(id=mention, kind='entity_mention', layer='binary',
                              annotation=dict(text=name if index == 0 else name.title(), type='mineral')))
            edges.append(dict(id='reference:' + mention, source=mention, target=reference,
                              type='HAS_SPECIES_REFERENCE'))
    nodes.append(dict(id='water', kind='entity_mention', layer='binary',
                      annotation=dict(text='water', type='fluid')))
    if set(specifications) - {'aragonite'} <= set(selected):
        for key, head, tail, predicate, eligible in [
            ('r1', 'calcite:0', 'pyrite:0', 'coexists with', True),
            ('r2', 'calcite:1', 'marcasite:0', 'coexists with', True),
            ('r3', 'pyrite:0', 'calcite:0', 'coexists with', True),
            ('r4', 'calcite:0', 'pyrite:0', 'occurs in', True),
            ('r5', 'calcite:0', 'water', 'occurs in', True),
            ('r6', 'dolomite:0', 'pyrite:0', 'coexists with', False),
        ]:
            nodes.append(dict(id=key, kind='relations', predicate=predicate,
                              default_fact_eligible=eligible))
            edges.extend([dict(id=key + ':head', source=key, target=head, type='HEAD'),
                          dict(id=key + ':tail', source=key, target=tail, type='TAIL')])
    write_rows(root, 'nodes.jsonl', nodes)
    write_rows(root, 'edges.jsonl', edges)


def source_hashes(source):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}


def kind_for(data, mention):
    return next(kind['id'] for kind in data['kind_profiles'] if mention in kind['source_members'])


class MinimalOntologyTests(unittest.TestCase):
    def test_repeated_mentions_of_one_species_do_not_make_an_ontology(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            fixture(source, species=['calcite'])
            data = infer(source)
            kind = kind_for(data, 'calcite:0')
            self.assertEqual(kind_for(data, 'calcite:1'), kind)
            self.assertEqual(data['ontologies'], [])
            self.assertEqual(data['relations'], [])

    def test_distinct_species_classes_and_directional_typed_relation_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            fixture(source)
            before = source_hashes(source)
            data = infer(source)
            self.assertEqual(source_hashes(source), before)
            carbonate_members = {kind_for(data, name + ':0') for name in ['calcite', 'dolomite']}
            sulfide_members = {kind_for(data, name + ':0') for name in ['pyrite', 'marcasite']}
            self.assertEqual({frozenset(row['kind_members']) for row in data['ontologies']},
                             {frozenset(carbonate_members), frozenset(sulfide_members)})
            carbonate = next(row for row in data['ontologies'] if set(row['kind_members']) == carbonate_members)
            sulfide = next(row for row in data['ontologies'] if set(row['kind_members']) == sulfide_members)
            self.assertIn('calcite:0', carbonate['mention_members'])
            self.assertIn('calcite:1', carbonate['mention_members'])
            self.assertNotIn('water', [key for row in data['ontologies'] for key in row['mention_members']])
            relation_map = {(row['source'], row['target'], row['predicate']): row for row in data['relations']}
            expected = {
                (carbonate['id'], sulfide['id'], 'coexists with'): {'r1', 'r2'},
                (sulfide['id'], carbonate['id'], 'coexists with'): {'r3'},
                (carbonate['id'], sulfide['id'], 'occurs in'): {'r4'},
            }
            self.assertEqual(set(relation_map), set(expected))
            for key, ids in expected.items():
                self.assertEqual(set(relation_map[key]['connection_ids']), ids)
                self.assertEqual(relation_map[key]['weight'], len(ids))
            self.assertNotIn('r5', [key for row in data['relations'] for key in row['connection_ids']])
            self.assertNotIn('r6', [key for row in data['relations'] for key in row['connection_ids']])

    def test_composition_does_not_count_twice_and_cosmetic_features_do_not_define_classes(self):
        for options in [dict(species=['pyrite', 'marcasite'], composition_only=True),
                        dict(species=['pyrite', 'marcasite'], cosmetic_only=True)]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / 'source'
                fixture(source, **options)
                self.assertEqual(infer(source)['ontologies'], [])

    def test_multi_class_membership_does_not_duplicate_evidence_within_a_relation(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            fixture(source, overlap=True)
            data = infer(source)
            calcite = kind_for(data, 'calcite:0')
            owners = [row['id'] for row in data['ontologies'] if calcite in row['kind_members']]
            self.assertGreaterEqual(len(owners), 2)
            forward = [row for row in data['relations'] if row['source'] in owners
                       and row['predicate'] == 'coexists with' and 'r1' in row['connection_ids']]
            self.assertEqual(len(forward), len(owners))
            for row in data['relations']:
                self.assertEqual(row['weight'], len(set(row['connection_ids'])))

    def test_build_validates_preserves_inputs_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / 'source', root / 'out'
            fixture(source)
            before = source_hashes(source)
            summary = build(source, output)
            self.assertIsInstance(summary, dict)
            self.assertEqual(source_hashes(source), before)
            result = validate(source, output)
            self.assertTrue(result['passed'])
            path = output / 'ontology_relations.jsonl'
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            rows[0]['weight'] += 1
            write_rows(output, path.name, rows)
            with self.assertRaises(ValueError):
                validate(source, output)
            self.assertEqual(source_hashes(source), before)
            with self.assertRaises(ValueError):
                build(source, output)

    def test_validator_rejects_graph_only_node_and_edge_tampering(self):
        for bad_case in ['missing_subkind_edge', 'relation_edge_weight', 'kind_node_label']:
            with self.subTest(bad_case=bad_case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, output = root / 'source', root / 'out'
                fixture(source)
                build(source, output)
                self.assertTrue(validate(source, output)['passed'])
                catalogs = {name: (output / name).read_bytes() for name in
                            ['minimal_ontologies.jsonl', 'ontology_relations.jsonl']}
                filename = 'nodes.jsonl' if bad_case == 'kind_node_label' else 'edges.jsonl'
                path = output / filename
                rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                if bad_case == 'missing_subkind_edge':
                    victim = next(row for row in rows if row.get('type') == 'SUBKIND_OF')
                    rows.remove(victim)
                elif bad_case == 'relation_edge_weight':
                    victim = next(row for row in rows if row.get('type') == 'ONTOLOGY_RELATION')
                    victim['weight'] += 1
                else:
                    victim = next(row for row in rows if row.get('kind') == 'entity_kind'
                                  and 'water' in row.get('source_members', []))
                    victim['label'] = 'tampered label'
                write_rows(output, filename, rows)
                self.assertEqual(catalogs, {name: (output / name).read_bytes() for name in catalogs})
                with self.assertRaises(ValueError):
                    validate(source, output)

    def test_html_safely_embeds_unassigned_original_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / 'source', root / 'out'
            fixture(source)
            original = '</script><script>alert(1)</script>'
            nodes = [json.loads(line) for line in
                     (source / 'nodes.jsonl').read_text(encoding='utf-8').splitlines()]
            next(node for node in nodes if node['id'] == 'water')['annotation']['text'] = original
            write_rows(source, 'nodes.jsonl', nodes)
            build(source, output)
            html = (output / '属性本体与本体间关系.html').read_text(encoding='utf-8')
            self.assertNotIn(original, html)
            embedded = re.search(
                r'<script id="minimal-data" type="application/json">(.*?)</script>', html, re.S)
            self.assertIsNotNone(embedded)
            payload = json.loads(embedded[1])
            kind = next(row for row in payload['kind_profiles'] if 'water' in row['source_members'])
            self.assertIn(original, kind['original_names'])
