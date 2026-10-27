import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from scripts.learn_ontology_candidates import infer, build, validate, parse_formula, attribute_id


_spec = importlib.util.spec_from_file_location(
    '_ontology_learning_fixtures', Path(__file__).with_name('test_minimal_ontologies.py'))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
fixture = _fixtures.fixture
write_rows = _fixtures.write_rows


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def hashes(source):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}


def add_property(source, owner, feature, value):
    nodes = rows(source/'nodes.jsonl')
    feature_id = 'feature:' + feature
    if not any(n['id'] == feature_id for n in nodes):
        nodes.append(dict(id=feature_id, kind='semantic_feature', key=feature))
    matching = next((n for n in nodes if n['kind'] == 'feature_value'
                     and n['key'] == feature and n['value'] == value), None)
    value_id = matching['id'] if matching else 'extra-value:' + owner + ':' + feature
    if matching is None:
        nodes.append(dict(id=value_id, kind='feature_value', key=feature,
                          feature=feature_id, value=value))
    nodes.append(dict(id='extra-property:' + owner + ':' + feature, kind='property_assertion',
                      subject=owner, feature=feature_id, value_node=value_id, layer='external',
                      conditions={}, cluster_eligible=True))
    write_rows(source, 'nodes.jsonl', nodes)


def add_species(source, name, formula, hardness):
    nodes = rows(source/'nodes.jsonl')
    reference = 'ref:' + name
    mention = name + ':0'
    nodes.extend([dict(id=reference, kind='reference_concept', label=name),
                  dict(id=mention, kind='entity_mention', layer='binary',
                       annotation=dict(text=name, type='mineral'))])
    write_rows(source, 'nodes.jsonl', nodes)
    edges = rows(source/'edges.jsonl')
    edges.append(dict(id='reference:' + mention, source=mention, target=reference,
                      type='HAS_SPECIES_REFERENCE'))
    write_rows(source, 'edges.jsonl', edges)
    for feature, value in [('formula', formula), ('crystal_family', 'hexagonal'),
                           ('cleavage_quality', 'perfect'), ('mohs_hardness', hardness)]:
        add_property(source, reference, feature, value)


def extended_fixture(source, smooth=False):
    fixture(source)
    add_species(source, 'siderite', 'FeCO3', [3.75, 4.25])
    if smooth:
        for name in ['calcite', 'dolomite']:
            add_property(source, 'ref:' + name, 'surface_texture', 'smooth')


def attribute(data, key, value):
    matches = [a for a in data['attributes'] if a['key'] == key and a['value'] == value]
    if len(matches) != 1:
        raise AssertionError('Expected one normalized attribute: ' + repr((key, value)))
    return matches[0]


def facet(data, key, value):
    aid = attribute(data, key, value)['id']
    matches = [c for c in data['concepts'] if c['definition']['conditions'] == [aid]]
    if len(matches) != 1:
        raise AssertionError('Expected a reusable single-condition candidate for ' + repr((key, value)))
    return matches[0]


def kind_for(data, mention):
    return next(k for k in data['kind_profiles'] if mention in k['source_members'])


def reachable(edges, start, target):
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge['child'], set()).add(edge['parent'])
    visited, pending = set(), list(outgoing.get(start, []))
    while pending:
        item = pending.pop()
        if item == target:
            return True
        if item not in visited:
            visited.add(item)
            pending.extend(outgoing.get(item, []))
    return False


class OntologyLearningTests(unittest.TestCase):
    def test_formula_parser_and_semantic_attribute_id_preserve_chemical_meaning(self):
        self.assertEqual(parse_formula('Co'), {'Co': 1})
        self.assertEqual(parse_formula('CO'), {'C': 1, 'O': 1})
        self.assertNotEqual(parse_formula('Co'), parse_formula('CO'))
        self.assertEqual(parse_formula('CaMg(CO3)2'), {'Ca': 1, 'Mg': 1, 'C': 2, 'O': 6})
        self.assertEqual(parse_formula('Al₂Si₂O₅(OH)₄'), {'Al': 2, 'Si': 2, 'O': 9, 'H': 4})
        for formula in ['Fe0', 'Fe1.5S2', 'Fe2+', '()', 'CaMg(CO3',
                        'Al2Si2O5(OH)4)', 'Xx2O3', 'CuSO4·5H2O', 'CuSO4.5H2O']:
            with self.subTest(formula=formula):
                self.assertIsNone(parse_formula(formula))
        self.assertNotEqual(attribute_id('formula', 'Co'), attribute_id('formula', 'CO'))
        self.assertNotEqual(attribute_id('formula', 'FeS2'), attribute_id('formula', 'fes2'))
        self.assertNotEqual(attribute_id('grain_size', 1, 'mm'), attribute_id('grain_size', 1, 'cm'))
        self.assertNotEqual(attribute_id('grain_size', 1), attribute_id('grain_size', '1'))
        self.assertNotEqual(attribute_id('observed', False), attribute_id('observed', 0))
        self.assertNotEqual(attribute_id('formula', 'FeS2'), attribute_id('observed', 'FeS2'))

    def test_overlapping_members_and_useful_parent_candidates_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            extended_fixture(source)
            data = infer(source)
            iron = facet(data, 'main_composition_element', 'Fe')
            disulfide = facet(data, 'formula', 'FeS2')
            carbonate = facet(data, 'composition_family', 'carbonate')
            iron_members = {kind_for(data, name + ':0')['id'] for name in ['pyrite', 'marcasite', 'siderite']}
            self.assertEqual(set(iron['members']), iron_members)
            self.assertEqual(set(disulfide['members']), {kind_for(data, name + ':0')['id']
                                                       for name in ['pyrite', 'marcasite']})
            self.assertIn(kind_for(data, 'siderite:0')['id'], carbonate['members'])
            self.assertTrue(reachable(data['hierarchy'], disulfide['id'], iron['id']))
            for kind in ['pyrite', 'siderite']:
                kid = kind_for(data, kind + ':0')['id']
                self.assertGreaterEqual(sum(kid in c['members'] for c in data['concepts']), 2)
            self.assertTrue(all(re.fullmatch(r'C\d+', c['label']) for c in data['concepts']))
            self.assertTrue(all(c['status'] == 'candidate' for c in data['concepts']))

    def test_single_decisive_attribute_is_not_rejected_by_the_old_two_dimension_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            fixture(source, species=['pyrite', 'marcasite'], composition_only=True)
            data = infer(source)
            self.assertEqual(len(facet(data, 'formula', 'FeS2')['members']), 2)
            self.assertEqual(len(facet(data, 'main_composition_element', 'Fe')['members']), 2)

    def test_equal_observed_members_do_not_make_different_definitions_equivalent(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            extended_fixture(source)
            data = infer(source)
            formula = facet(data, 'formula', 'FeS2')
            sulfur = facet(data, 'main_composition_element', 'S')
            self.assertEqual(set(formula['members']), set(sulfur['members']))
            self.assertNotEqual(formula['id'], sulfur['id'])
            self.assertNotEqual(formula['definition'], sulfur['definition'])
            self.assertTrue(reachable(data['hierarchy'], formula['id'], sulfur['id']))
            self.assertFalse(reachable(data['hierarchy'], sulfur['id'], formula['id']))

    def test_observed_inclusion_is_not_a_definition_rule_and_unknown_is_not_false(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            extended_fixture(source, smooth=True)
            data = infer(source)
            smooth = facet(data, 'surface_texture', 'smooth')
            hexagonal = facet(data, 'crystal_family', 'hexagonal')
            self.assertLess(set(smooth['members']), set(hexagonal['members']))
            self.assertFalse(reachable(data['hierarchy'], smooth['id'], hexagonal['id']))
            self.assertTrue(any(e['child'] == smooth['id'] and e['parent'] == hexagonal['id']
                                for e in data['observed_inclusions']))
            self.assertTrue(all(e['status'] == 'needs_review' for e in data['observed_inclusions']))
            water = kind_for(data, 'water')
            self.assertEqual(water['attributes'], [])
            self.assertFalse(any(water['id'] in c['members'] for c in data['concepts']))
            self.assertTrue(any(u['kind'] == water['id'] for u in data['uncovered']))

    def test_registry_keeps_ids_and_labels_when_new_members_arrive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before_source, after_source = root/'before', root/'after'
            extended_fixture(before_source)
            old = infer(before_source)
            registry = root/'registry.json'
            registry.write_text(json.dumps(old['registry'], ensure_ascii=False), encoding='utf-8')
            shutil.copytree(before_source, after_source)
            add_species(after_source, 'magnesite', 'MgCO3', [4, 4.5])
            new = infer(after_source, registry=registry)
            original = facet(old, 'composition_family', 'carbonate')
            updated = facet(new, 'composition_family', 'carbonate')
            self.assertEqual((original['id'], original['label'], original['definition']),
                             (updated['id'], updated['label'], updated['definition']))
            self.assertLess(set(original['members']), set(updated['members']))
            self.assertIn(kind_for(new, 'magnesite:0')['id'], updated['members'])
            previous = {c['id']: c for c in old['registry']['concepts']}
            current = {c['id']: c for c in new['registry']['concepts']}
            self.assertTrue(set(previous) <= set(current))
            for key in previous:
                self.assertEqual(previous[key], current[key])

    def test_projected_relations_preserve_direction_predicate_and_evidence_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            extended_fixture(source)
            data = infer(source)
            owner = {}
            for concept in data['concepts']:
                for member in concept['source_members']:
                    owner.setdefault(member, set()).add(concept['id'])
            raw = {'r1': ('calcite:0', 'pyrite:0', 'coexists with'),
                   'r2': ('calcite:1', 'marcasite:0', 'coexists with'),
                   'r3': ('pyrite:0', 'calcite:0', 'coexists with'),
                   'r4': ('calcite:0', 'pyrite:0', 'occurs in')}
            used = set()
            for relation in data['domain_relations']:
                ids = relation['connection_ids']
                self.assertEqual(relation['weight'], len(set(ids)))
                for key in ids:
                    self.assertIn(key, raw)
                    head, tail, predicate = raw[key]
                    self.assertIn(relation['source'], owner[head])
                    self.assertIn(relation['target'], owner[tail])
                    self.assertEqual(relation['predicate'], predicate)
                    used.add(key)
            self.assertEqual(used, set(raw))
            self.assertGreater(sum(r['weight'] for r in data['domain_relations']), len(used))

    def test_build_checks_all_outputs_without_changing_input_or_executing_original_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root/'source', root/'output'
            extended_fixture(source)
            dangerous = '</script><script>alert(1)</script>'
            nodes = rows(source/'nodes.jsonl')
            next(n for n in nodes if n['id'] == 'water')['annotation']['text'] = dangerous
            write_rows(source, 'nodes.jsonl', nodes)
            snapshot = hashes(source)
            build(source, output)
            self.assertTrue(validate(source, output)['passed'])
            self.assertEqual(hashes(source), snapshot)
            page = next(output.glob('*.html')).read_text(encoding='utf-8')
            self.assertNotIn(dangerous, page)
            payload = json.loads(re.search(r'type="application/json">(.*?)</script>', page, re.S)[1])
            self.assertTrue(any(dangerous in k['original_names'] for k in payload['kind_profiles']))
            edge_file = output/'edges.jsonl'
            edges = rows(edge_file)
            victim = next(e for e in edges if e.get('type') not in {'HEAD', 'TAIL', 'HAS_SPECIES_REFERENCE'})
            edges.remove(victim)
            write_rows(output, edge_file.name, edges)
            with self.assertRaises(ValueError):
                validate(source, output)
            with self.assertRaises(ValueError):
                build(source, output)
            self.assertEqual(hashes(source), snapshot)

    def test_registry_definition_cannot_be_changed_while_reusing_its_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source'
            extended_fixture(source)
            data = infer(source)
            registry = data['registry']
            registry['concepts'][0]['condition_ids'] = ['unregistered-condition']
            path = root/'bad_registry.json'
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
            with self.assertRaises(ValueError):
                infer(source, registry=path)
