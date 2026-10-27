"""Learn reusable, overlapping attribute concepts from an evidence graph.

Candidate identity depends on its positive definition, not its observed members.
Definition implication, observed inclusion, and domain evidence are kept apart.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_knowledge_graph import identifier
from cluster_minimal_ontologies import prepare_kind_profiles, composition_family
from cluster_two_stage_ontologies import MATERIAL_TYPES, semantic_connections
from cluster_ontology_candidates import read_rows
from graph_zh_glossary import translate_term

VERSION = 'reusable-ontology-candidates-v1'
RULE_VERSION = 'positive-kind-conditions-v1'
ELEMENTS = set(('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn '
    'Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd '
    'Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac '
    'Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og').split())
FILES = [('concepts.jsonl', 'concepts'), ('attributes.jsonl', 'attributes'),
    ('kind_profiles.jsonl', 'kind_profiles'), ('membership.jsonl', 'membership'),
    ('definition_hierarchy.jsonl', 'hierarchy'), ('observed_inclusions.jsonl', 'observed_inclusions'),
    ('observed_coextensions.jsonl', 'observed_coextensions'), ('domain_relations.jsonl', 'domain_relations'),
    ('uncovered_kinds.jsonl', 'uncovered'), ('residual_relations.jsonl', 'residual_relations')]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def parse_formula(formula):
    """Parse complete, uncharged formulas; unsupported syntax stays unknown."""
    if not isinstance(formula, str):
        return None
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', formula))
    parts = re.findall(r'[A-Z][a-z]?|\d+|[()]', text)
    if not parts or ''.join(parts) != text:
        return None
    index = 0

    def group(nested=False):
        nonlocal index
        atoms = Counter()
        while index < len(parts) and parts[index] != ')':
            token = parts[index]
            index += 1
            if token == '(':
                term = group(True)
            elif token in ELEMENTS:
                term = Counter({token: 1})
            else:
                raise ValueError('Invalid chemical term')
            multiplier = 1
            if index < len(parts) and parts[index].isdigit():
                multiplier = int(parts[index]); index += 1
            if not multiplier:
                raise ValueError('Zero chemical coefficient')
            atoms.update({element: count * multiplier for element, count in term.items()})
        if not atoms:
            raise ValueError('Empty chemical group')
        if nested:
            if index >= len(parts) or parts[index] != ')':
                raise ValueError('Unclosed chemical group')
            index += 1
        return atoms

    try:
        atoms = group()
        return dict(atoms) if index == len(parts) else None
    except (ValueError, RecursionError):
        return None


def attribute_id(key, value, unit=None):
    return identifier(RULE_VERSION, 'attribute', key, canonical(value), canonical(unit), 'eq')


def condition_id(domain_type, conditions):
    return identifier(RULE_VERSION, 'concept', domain_type, sorted(conditions))


def load_registry(registry):
    if registry is None:
        return dict(schema_version=1, attributes=[], concepts=[])
    if isinstance(registry, dict):
        result = json.loads(canonical(registry))
    else:
        result = json.loads(Path(registry).read_text(encoding='utf-8'))
    if result.get('schema_version') != 1:
        raise ValueError('Unsupported concept registry version')
    attrs = {a['id']: a for a in result['attributes']}
    if len(attrs) != len(result['attributes']):
        raise ValueError('Duplicate registered attribute identity')
    for a in attrs.values():
        if a.get('operator') != 'eq' or a['id'] != attribute_id(a['key'], a['value'], a.get('unit')):
            raise ValueError('Registered attribute definition changed')
    identities, labels = set(), set()
    for c in result['concepts']:
        if (c['id'] in identities or c['label'] in labels or not re.fullmatch(r'C[1-9]\d*', c['label'])
                or c.get('definition_version') != 1 or not c['condition_ids']
                or not set(c['condition_ids']) <= set(attrs)
                or c['condition_ids'] != sorted(set(c['condition_ids']))
                or c['id'] != condition_id(c['domain_type'], c['condition_ids'])):
            raise ValueError('Registered concept identity, definition or display label conflicts')
        identities.add(c['id']); labels.add(c['label'])
    return result


def transitive_reduction(pairs):
    parents = defaultdict(set)
    for child, parent in pairs:
        parents[child].add(parent)
    return sorted((child, parent) for child, parent in pairs
                  if not any(parent in parents[middle] for middle in parents[child] if middle != parent))


def infer(source, registry=None, min_support=2):
    if min_support < 2:
        raise ValueError('Candidate discovery needs at least two distinct kinds')
    source = Path(source)
    prepared = prepare_kind_profiles(source)
    seed = load_registry(registry)
    catalog = {}
    provenance = defaultdict(set)

    def add(key, value, unit=None, label=None, value_label=None, dimension=None, source_ids=()):
        if key == 'composition_family' and value == 'iron_disulfide':
            key, value, label, value_label = 'formula', 'FeS2', '化学式', 'FeS2'
        if key == 'formula' and isinstance(value, str):
            value = re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
            value_label = value
        aid = attribute_id(key, value, unit)
        item = dict(id=aid, key=key, value=value, unit=unit, operator='eq', label=label or key,
                    value_label=value_label if value_label is not None else str(value),
                    dimension=dimension or ('composition' if key in {'formula', 'composition_family', 'main_composition_element'} else key))
        if aid not in catalog:
            catalog[aid] = item
        provenance[aid].update(source_ids)
        return aid

    for attr in seed['attributes']:
        catalog[attr['id']] = dict(attr)
    profiles = []
    for original in prepared['profiles']:
        profile = dict(original)
        conditions = set()
        derived = []
        for attr in original['attributes']:
            aid = add(attr['key'], attr['value'], attr.get('unit'), attr['label'], attr.get('value_label'),
                      source_ids=attr.get('source_value_ids', []))
            conditions.add(aid)
            if attr['key'] == 'formula':
                atoms = parse_formula(attr['value'])
                if atoms:
                    for element in sorted(atoms):
                        eid = add('main_composition_element', element, label='主化学组成含元素', value_label=element,
                                  source_ids=attr.get('source_value_ids', []))
                        conditions.add(eid)
                        derived.append(dict(attribute=eid, from_attribute=aid, rule='complete_formula_elements_v1'))
        profile['attributes'] = sorted(conditions)
        profile['derived_attributes'] = derived
        profile['evidence_scope'] = 'species_reference' if profile['reference_ids'] else 'common_supported_observations'
        profile['eligible'] = profile['raw_type'] in MATERIAL_TYPES and bool(conditions)
        profiles.append(profile)
    by_kind = {p['id']: p for p in profiles}

    # Rule conditions are included even if absent from the current sample. Their
    # existence is a definition, not evidence that an unknown object has them.
    for attr in list(catalog.values()):
        if attr['key'] == 'formula':
            atoms = parse_formula(attr['value'])
            if atoms:
                for element in atoms:
                    add('main_composition_element', element, label='主化学组成含元素', value_label=element)
            family = composition_family(attr['value'])
            if family == 'carbonate':
                add('composition_family', family, label='组成族', value_label='碳酸盐组成')
        if attr['key'] == 'composition_family' and attr['value'] == 'carbonate':
            for element in ['C', 'O']:
                add('main_composition_element', element, label='主化学组成含元素', value_label=element)
    direct = defaultdict(set)
    for aid, attr in list(catalog.items()):
        if attr['key'] == 'formula':
            atoms = parse_formula(attr['value'])
            if atoms:
                direct[aid].update(attribute_id('main_composition_element', element) for element in atoms)
            if composition_family(attr['value']) == 'carbonate':
                direct[aid].add(attribute_id('composition_family', 'carbonate'))
        elif attr['key'] == 'composition_family' and attr['value'] == 'carbonate':
            direct[aid].update(attribute_id('main_composition_element', element) for element in ['C', 'O'])

    def expand(conditions):
        result = set(conditions); todo = list(result)
        while todo:
            for attr in direct[todo.pop()]:
                if attr not in result:
                    result.add(attr); todo.append(attr)
        return result

    def normalize(conditions):
        result = set(conditions)
        for attr in sorted(result):
            if attr in expand(result - {attr}):
                result.remove(attr)
        return tuple(sorted(result))

    vectors = {p['id']: expand(p['attributes']) for p in profiles}
    definitions = {}
    origins = defaultdict(set)
    for c in seed['concepts']:
        normalized = normalize(c['condition_ids'])
        if tuple(c['condition_ids']) != normalized:
            raise ValueError('Registry contains a noncanonical concept definition')
        definitions[(c['domain_type'], normalized)] = dict(c)
        origins[c['id']].add('registry')
    partitions = defaultdict(list)
    for p in profiles:
        if p['eligible']:
            partitions[p['raw_type']].append(p['id'])
    discovered = defaultdict(set)
    for domain_type, keys in sorted(partitions.items()):
        intents = {frozenset(vectors[key]) for key in keys} - {frozenset()}
        todo = list(intents)
        while todo:
            current = todo.pop()
            for key in keys:
                common = current & vectors[key]
                if common and common not in intents:
                    intents.add(common); todo.append(common)
                    if len(intents) > 50000:
                        raise ValueError('Too many closed patterns; narrow attributes or raise support')
        for attr in set().union(*(vectors[key] for key in keys)):
            if sum(attr in vectors[key] for key in keys) >= min_support:
                discovered[(domain_type, normalize([attr]))].add('facet')
        for intent in intents:
            if sum(intent <= vectors[key] for key in keys) >= min_support:
                discovered[(domain_type, normalize(intent))].add('closed_intersection')
    next_number = max([int(c['label'][1:]) for c in seed['concepts']] or [0]) + 1
    for definition, found_by in sorted(discovered.items(), key=lambda row: (row[0][0], len(row[0][1]), row[0][1])):
        if definition not in definitions:
            domain_type, conditions = definition
            definitions[definition] = dict(id=condition_id(domain_type, conditions), label='C'+str(next_number),
                domain_type=domain_type, condition_ids=list(conditions), definition_version=1)
            next_number += 1
        origins[definitions[definition]['id']].update(found_by)
    if len(definitions) > 1000:
        raise ValueError('Too many candidate definitions; raise minimum support')
    concepts = []
    for (domain_type, conditions), entry in definitions.items():
        members = sorted(p['id'] for p in profiles if p['raw_type'] == domain_type and set(conditions) <= vectors[p['id']])
        common = sorted(set.intersection(*(vectors[k] for k in members))) if members else []
        concepts.append(dict(id=entry['id'], label=entry['label'], domain_type=domain_type,
            definition=dict(conditions=list(conditions), version=1, operator='all'), members=members,
            source_members=sorted({m for k in members for m in by_kind[k]['source_members']}),
            observed_common_attributes=common, support=len(members), origins=sorted(origins[entry['id']]),
            status='candidate', interpretation='supported_kind_pattern_not_universal_specimen_property'))
    concepts.sort(key=lambda c: int(c['label'][1:]))
    by_concept = {c['id']: c for c in concepts}
    expanded = {c['id']: expand(c['definition']['conditions']) for c in concepts}
    entails = set()
    equivalent = []
    for child in concepts:
        for parent in concepts:
            if child['id'] == parent['id'] or child['domain_type'] != parent['domain_type']:
                continue
            if set(parent['definition']['conditions']) <= expanded[child['id']]:
                if set(child['definition']['conditions']) <= expanded[parent['id']]:
                    if child['id'] < parent['id']:
                        equivalent.append(dict(left=child['id'], right=parent['id'], type='DEFINITION_EQUIVALENT'))
                else:
                    entails.add((child['id'], parent['id']))
    hierarchy = []
    for child, parent in transitive_reduction(entails):
        via = set(by_concept[parent]['definition']['conditions']) - set(by_concept[child]['definition']['conditions'])
        hierarchy.append(dict(child=child, parent=parent, type='DEFINITION_IMPLIES',
            proof=dict(rule='positive_condition_implication_v1', rule_derived_attributes=sorted(via))))
    inclusion_pairs = {(a['id'], b['id']) for a in concepts for b in concepts
                       if a['domain_type'] == b['domain_type'] and a['members'] and set(a['members']) < set(b['members'])}
    observed = [dict(child=a, parent=b, type='OBSERVED_INCLUSION', status='needs_review')
                for a, b in transitive_reduction(inclusion_pairs) if (a, b) not in entails]
    coextensions = [dict(left=a['id'], right=b['id'], type='OBSERVED_COEXTENSION', status='needs_review')
        for i, a in enumerate(concepts) for b in concepts[i+1:]
        if a['domain_type'] == b['domain_type'] and a['members'] and a['members'] == b['members']
        and not (set(a['definition']['conditions']) <= expanded[b['id']]
                 and set(b['definition']['conditions']) <= expanded[a['id']])]
    owners = defaultdict(set)
    membership = []
    for c in concepts:
        for kind in c['members']:
            owners[kind].add(c['id']); membership.append(dict(kind=kind, concept=c['id']))
    domain_relations = {}
    residual, relation_evidence, linked = [], {}, set()
    connections = semantic_connections(prepared['nodes'], prepared['edges'], set(prepared['objects']))
    for relation in connections:
        rid = relation['id']
        relation_evidence[rid] = dict(relation, annotation=prepared['nodes'][rid].get('annotation', {}))
        head = owners[prepared['object_to_kind'].get(relation['source'])]
        tail = owners[prepared['object_to_kind'].get(relation['target'])]
        pairs = [(a, b) for a in sorted(head) for b in sorted(tail) if a != b]
        if not pairs:
            residual.append(dict(relation, reason='internal_relation' if head and tail else 'unmapped_endpoint'))
        else:
            linked.add(rid)
        for a, b in pairs:
            key = (a, relation['predicate'], b)
            group = domain_relations.setdefault(key, dict(id=identifier(VERSION, 'relation', *key), source=a, target=b,
                predicate=relation['predicate'], predicate_label=translate_term(relation['predicate']), weight=0, connection_ids=[],
                grounding='member_evidence_not_universal'))
            if rid not in group['connection_ids']:
                group['connection_ids'].append(rid); group['weight'] += 1
    uncovered = [dict(kind=p['id'], reason_label='没有足够的已确认属性匹配当前候选') for p in profiles if not owners[p['id']]]
    registry_out = dict(schema_version=1, attributes=[catalog[k] for k in sorted(catalog)],
                        concepts=sorted(definitions.values(), key=lambda c: int(c['label'][1:])))
    attrs = [dict(catalog[k], source_value_ids=sorted(provenance[k])) for k in sorted(catalog)]
    summary = dict(version=VERSION, source_objects=len(prepared['objects']), kind_count=len(profiles),
        attribute_kinds=sum(p['eligible'] for p in profiles), concept_count=len(concepts),
        reused_concepts=len(seed['concepts']), newly_discovered_concepts=len(concepts)-len(seed['concepts']),
        supported_concepts=sum(c['support'] >= min_support for c in concepts),
        overlapping_kinds=sum(len(owners[p['id']]) > 1 for p in profiles), covered_kinds=sum(bool(owners[p['id']]) for p in profiles),
        definition_edges=len(hierarchy), observed_edges=len(observed), observed_coextensions=len(coextensions),
        domain_relation_groups=len(domain_relations), original_relations=len(connections),
        unique_domain_evidence=len(linked), projected_weight_sum=sum(r['weight'] for r in domain_relations.values()),
        uncovered_kinds=len(uncovered), min_support=min_support, semantic_review_completed=False,
        registry_seed_sha256=hashlib.sha256(canonical(seed).encode('utf-8')).hexdigest(),
        input_sha256={name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['nodes.jsonl', 'edges.jsonl']})
    return dict(summary=summary, concepts=concepts, attributes=attrs, kind_profiles=profiles, membership=membership,
        hierarchy=hierarchy, observed_inclusions=observed, observed_coextensions=coextensions,
        definition_equivalences=equivalent, domain_relations=sorted(domain_relations.values(), key=lambda r: r['id']),
        uncovered=uncovered, residual_relations=residual, relation_evidence=relation_evidence, registry=registry_out,
        _raw_nodes=prepared['raw_nodes'], _raw_edges=prepared['edges'])


def export_graph(data):
    nodes = {n['id']: n for n in data['_raw_nodes']}; edges = list(data['_raw_edges'])
    for p in data['kind_profiles']:
        if p['id'] not in nodes:
            nodes[p['id']] = dict(p, kind='entity_kind')
    for a in data['attributes']:
        nodes[a['id']] = dict(a, kind='concept_attribute')
    for c in data['concepts']:
        nodes[c['id']] = dict(c, kind='ontology_candidate')
        for aid in c['definition']['conditions']:
            edges.append(dict(id=identifier(c['id'], aid, 'USES_CONDITION'), source=c['id'], target=aid, type='USES_CONDITION'))
    for row in data['membership']:
        edges.append(dict(id=identifier(row['kind'], row['concept'], 'MATCHES_CANDIDATE'),
                          source=row['kind'], target=row['concept'], type='MATCHES_CANDIDATE'))
    for key, type_ in [('hierarchy', 'DEFINITION_IMPLIES'), ('observed_inclusions', 'OBSERVED_INCLUSION')]:
        for row in data[key]:
            edges.append(dict(row, id=identifier(row['child'], row['parent'], type_), source=row['child'], target=row['parent'], type=type_))
    for key, type_ in [('observed_coextensions', 'OBSERVED_COEXTENSION'),
                       ('definition_equivalences', 'DEFINITION_EQUIVALENT')]:
        for row in data[key]:
            edges.append(dict(row, id=identifier(row['left'], row['right'], type_),
                              source=row['left'], target=row['right'], type=type_))
    edges.extend(dict(r, type='DOMAIN_RELATION') for r in data['domain_relations'])
    unique = {}
    for e in edges:
        if e['id'] in unique and unique[e['id']] != e:
            raise ValueError('Conflicting exported edge identity')
        unique[e['id']] = e
    return nodes, unique


def render_report(data, path):
    template = Path(__file__).with_name('ontology_learning_report.html').read_text(encoding='utf-8')
    if template.count('__LEARNING_DATA__') != 1:
        raise ValueError('Invalid candidate report template')
    public = {key: value for key, value in data.items() if not key.startswith('_')}
    Path(path).write_text(template.replace('__LEARNING_DATA__', canonical(public).replace('<', '\\u003c')), encoding='utf-8')


def build(source, output, registry=None, min_support=2):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    data = infer(source, registry, min_support)
    nodes, edges = export_graph(data)
    output.mkdir(parents=True)
    for filename, key in FILES:
        (output/filename).write_text(''.join(canonical(r)+'\n' for r in data[key]), encoding='utf-8')
    for filename, rows in [('nodes.jsonl', nodes.values()), ('edges.jsonl', edges.values())]:
        (output/filename).write_text(''.join(canonical(r)+'\n' for r in rows), encoding='utf-8')
    for filename, key in [('summary.json', 'summary'), ('registry.json', 'registry'),
                          ('relation_evidence.json', 'relation_evidence'), ('definition_equivalences.json', 'definition_equivalences')]:
        (output/filename).write_text(json.dumps(data[key], ensure_ascii=False, indent=2), encoding='utf-8')
    render_report(data, output/'知识图谱到本体候选.html')
    return data['summary']


def validate(source, output, registry=None, min_support=2):
    output = Path(output); expected = infer(source, registry, min_support)
    for filename, key in FILES:
        if read_rows(output/filename) != expected[key]:
            raise ValueError('Changed or incomplete candidate output: '+filename)
    for filename, key in [('summary.json', 'summary'), ('registry.json', 'registry'),
                          ('relation_evidence.json', 'relation_evidence'), ('definition_equivalences.json', 'definition_equivalences')]:
        if json.loads((output/filename).read_text(encoding='utf-8')) != expected[key]:
            raise ValueError('Changed candidate metadata: '+filename)
    nodes, edges = export_graph(expected)
    for filename, wanted in [('nodes.jsonl', nodes), ('edges.jsonl', edges)]:
        rows = read_rows(output/filename); actual = {r['id']: r for r in rows}
        if len(rows) != len(actual) or actual != wanted:
            raise ValueError('Changed candidate graph: '+filename)
    owners = defaultdict(set)
    for c in expected['concepts']:
        for k in c['members']:
            owners[k].add(c['id'])
    object_owner = {}
    for p in expected['kind_profiles']:
        for key in p['source_members'] + p['reference_ids']:
            object_owner[key] = owners[p['id']]
    for relation in expected['domain_relations']:
        if relation['weight'] != len(set(relation['connection_ids'])):
            raise ValueError('Repeated relation evidence')
        for rid in relation['connection_ids']:
            raw = expected['relation_evidence'][rid]
            if (relation['source'] not in object_owner.get(raw['source'], set())
                    or relation['target'] not in object_owner.get(raw['target'], set())
                    or relation['predicate'] != raw['predicate']):
                raise ValueError('Unsupported relation projection or direction')
    result = dict(passed=True, concepts_checked=len(expected['concepts']), kinds_checked=len(expected['kind_profiles']),
                  definition_edges_checked=len(expected['hierarchy']), domain_relation_groups_checked=len(expected['domain_relations']),
                  raw_relations_accounted_for=expected['summary']['original_relations'], semantic_review_completed=False)
    (output/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='从图谱学习可重叠、分层且可复用的本体候选')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--min-support', type=int, default=2)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(validate(args.source, args.output, args.registry, args.min_support) if args.validate_only
                     else build(args.source, args.output, args.registry, args.min_support), ensure_ascii=False))
