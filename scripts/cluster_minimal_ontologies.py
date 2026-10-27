"""Distinct kinds -> specific attribute concepts -> directed concept relations.

Core attribute closures propose classes; repeated mentions never count as extra
kinds. Relations are evidence projections, not new classes or universal axioms.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_knowledge_graph import identifier
from cluster_ontology_candidates import read_rows, object_info
from cluster_two_stage_ontologies import attribute_profiles, semantic_connections, MATERIAL_TYPES
from graph_zh_glossary import translate_name, translate_term

VERSION = 'minimal-attribute-concepts-v1'
CORE = {'formula', 'composition_family', 'crystal_family', 'cleavage_quality',
        'mohs_hardness', 'surface_texture', 'grain_size', 'roundness', 'morphology'}
DIMENSIONS = {'formula': 'composition', 'composition_family': 'composition',
              'crystal_family': 'crystallography', 'cleavage_quality': 'physical',
              'mohs_hardness': 'physical', 'surface_texture': 'surface',
              'grain_size': 'size', 'roundness': 'morphology', 'morphology': 'morphology'}
FAMILY_LABELS = {'carbonate': '碳酸盐组成', 'iron_disulfide': '二硫化铁组成'}
FAMILY_SOURCES = {
    'carbonate': ['https://www.handbookofmineralogy.org/pdfs/calcite.pdf',
                  'https://www.handbookofmineralogy.org/pdfs/dolomite.pdf',
                  'https://www.handbookofmineralogy.org/pdfs/siderite.pdf'],
    'iron_disulfide': ['https://www.handbookofmineralogy.org/pdfs/pyrite.pdf',
                       'https://www.handbookofmineralogy.org/pdfs/marcasite.pdf'],
}


def composition_family(formula):
    """Conservative reviewed formula patterns, not mineral-name assignments."""
    if not isinstance(formula, str):
        return None
    formula = unicodedata.normalize('NFKC', formula).replace(' ', '')
    if formula == 'FeS2':
        return 'iron_disulfide'
    match = re.fullmatch(r'((?:[A-Z][a-z]?\d*)+)(?:CO3|\(CO3\)\d*)', formula)
    metals = {'Li', 'Na', 'K', 'Mg', 'Ca', 'Sr', 'Ba', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn', 'Cd', 'Pb'}
    if match and set(re.findall(r'[A-Z][a-z]?', match[1])) <= metals:
        return 'carbonate'
    return None


def nominal_key(name, kind):
    text = ' '.join(unicodedata.normalize('NFKC', name).split())
    # Co and CO must not become the same nominal kind.
    symbol = re.fullmatch(r'(?:[A-Z][a-z]?\d*|[()])+', text)
    return kind + ':' + (text if symbol else text.casefold())


def concept_extents(profiles, tokens):
    """Smallest multi-kind core closures in the current observed vocabulary.

    Habit, luster and fracture are descriptors, not sufficient core dimensions.
    Composition takes precedence over accidental physical matches in minerals.
    """
    results = []
    partitions = defaultdict(list)
    for profile in profiles:
        if profile['eligible']:
            partitions[profile['raw_type']].append(profile)
    for kind, rows in sorted(partitions.items()):
        vectors = {r['id']: frozenset(a['id'] for a in r['attributes'] if a['key'] in CORE) for r in rows}
        intents = set(vectors.values()) - {frozenset()}
        frontier = list(intents)
        while frontier:
            current = frontier.pop()
            for vector in vectors.values():
                overlap = current & vector
                if overlap and overlap not in intents:
                    intents.add(overlap)
                    frontier.append(overlap)
                    if len(intents) > 50000:
                        raise ValueError('Core closure limit exceeded; narrow the attribute vocabulary')
        candidates = {}
        for intent in intents:
            extent = frozenset(key for key, vector in vectors.items() if intent <= vector)
            if len(extent) < 2:
                continue
            closed = set.intersection(*(set(vectors[k]) for k in extent))
            if len({DIMENSIONS[tokens[k]['key']] for k in closed}) < 2:
                continue
            shared_composition = any(tokens[k]['key'] in {'formula', 'composition_family'} for k in closed)
            all_have_formula = all(any(tokens[v]['key'] == 'formula' for v in vectors[k]) for k in extent)
            if kind == 'mineral' and all_have_formula and not shared_composition:
                continue
            candidates[extent] = closed
        for extent, intent in candidates.items():
            if any(other < extent for other in candidates):
                continue
            results.append((sorted(extent), sorted(intent)))
    return sorted(results)


def concept_label(definition, raw_type):
    values = {(a['key'], json.dumps(a['value'], ensure_ascii=False)) for a in definition}
    if ('formula', '"FeS2"') in values:
        return '二硫化铁矿物'
    if ('composition_family', '"carbonate"') in values:
        return '六方晶族碳酸盐矿物' if ('crystal_family', '"hexagonal"') in values else '碳酸盐矿物'
    return '、'.join(a['label'] + '=' + a['value_label'] for a in definition) + '的' + translate_term(raw_type)


def infer(source):
    source = Path(source)
    raw_nodes = read_rows(source / 'nodes.jsonl')
    nodes = {n['id']: n for n in raw_nodes}
    edges = read_rows(source / 'edges.jsonl')
    if len(nodes) != len(raw_nodes) or any(e['source'] not in nodes or e['target'] not in nodes for e in edges):
        raise ValueError('Duplicate graph identifiers or dangling endpoints')
    objects, vectors, evidence, borrowed = attribute_profiles(nodes, edges)
    refs = defaultdict(set)
    for edge in edges:
        if edge['type'] == 'HAS_SPECIES_REFERENCE' and edge['source'] in objects:
            if nodes[edge['target']]['kind'] != 'reference_concept':
                raise ValueError('Species reference target is not a reference concept')
            refs[edge['source']].add(edge['target'])
    if any(len(values) > 1 for values in refs.values()):
        raise ValueError('Ambiguous species reference')
    groups = defaultdict(list)
    for key, node in objects.items():
        name, kind, level = object_info(node)
        group = key if level == 'reference_kind' else (next(iter(refs[key])) if refs[key]
                    else identifier(VERSION, 'nominal-kind', nominal_key(name, kind)))
        groups[group].append(key)
    tokens = {}
    for key, node in nodes.items():
        if node['kind'] != 'feature_value':
            continue
        feature, value = node['key'], node['value']
        display = str(value) if feature == 'formula' else translate_term(str(value))
        if feature == 'crystal_family' and value == 'cubic':
            display = '等轴晶族'
        tokens[key] = dict(id=key, key=feature, label=translate_term(feature), value=value,
                           value_label=display, unit=node.get('unit'), derived=False, source_value_ids=[key])
    profiles = []
    object_to_kind = {}
    for group, members in sorted(groups.items()):
        reference_ids = [key for key in members if objects[key]['kind'] == 'reference_concept']
        source_members = [key for key in members if objects[key]['kind'] == 'entity_mention'
                          and objects[key].get('layer') != 'pending']
        pending_members = [key for key in members if objects[key].get('layer') == 'pending']
        representative = objects[reference_ids[0] if reference_ids else members[0]]
        name, kind, _ = object_info(representative)
        usable = set(vectors[reference_ids[0]]) if reference_ids else (
            set.intersection(*(set(vectors[k]) for k in source_members)) if source_members else set())
        attrs = {key: dict(tokens[key]) for key in usable}
        for key in sorted(usable):
            if tokens[key]['key'] != 'formula':
                continue
            family = composition_family(tokens[key]['value'])
            if family:
                aid = identifier(VERSION, 'composition-family', family)
                attribute = dict(id=aid, key='composition_family', label='组成族', value=family,
                                 value_label=FAMILY_LABELS[family], unit=None, derived=True,
                                 rule='reviewed_formula_pattern_v1', source_value_ids=[key],
                                 rule_sources=FAMILY_SOURCES[family])
                attrs[aid] = attribute
                if aid not in tokens:
                    tokens[aid] = dict(attribute)
                else:
                    tokens[aid]['source_value_ids'] = sorted(set(tokens[aid]['source_value_ids'] + [key]))
        core_dimensions = {DIMENSIONS[a['key']] for a in attrs.values() if a['key'] in CORE}
        profiles.append(dict(id=group, label=translate_name(name), type=translate_term(kind), raw_type=kind,
            original_names=sorted({object_info(objects[k])[0] for k in members}),
            source_members=sorted(source_members), reference_ids=reference_ids, pending_source_members=pending_members,
            identity_status='matched_species_reference_not_specimen_identity' if reference_ids else 'nominal_kind_candidate_not_identity_merge',
            attributes=[attrs[k] for k in sorted(attrs)],
            evidence_claim_ids=sorted({claim for k in reference_ids + source_members for claim in evidence[k]['attributes']}),
            eligible=kind in MATERIAL_TYPES and len(core_dimensions) >= 2))
        for key in reference_ids + source_members:
            object_to_kind[key] = group
    by_kind = {p['id']: p for p in profiles}
    frozen = [n for n in raw_nodes if n.get('ontology_frozen') is True or n['kind'] in {'ontology', 'ontology_class', 'frozen_ontology'}]
    fixed_kinds = set()
    for ontology in frozen:
        members = ontology.get('kind_members')
        if not members or ontology.get('members') != members or len(members) != len(set(members)):
            raise ValueError('Existing ontology needs this scheme\'s explicit kind_members; use the original feature graph for a new trial')
        if not set(members) <= set(by_kind):
            raise ValueError('An existing ontology contains an unknown kind')
        fixed_kinds.update(members)
    available = [p for p in profiles if p['id'] not in fixed_kinds]
    ontologies = list(frozen)
    for members, intent in concept_extents(available, tokens):
        definition = [tokens[key] for key in intent]
        shared_ids = set.intersection(*(set(a['id'] for a in by_kind[k]['attributes']) for k in members))
        oid = identifier(VERSION, 'ontology', members, intent)
        ontologies.append(dict(id=oid, kind='ontology', members=members, kind_members=members,
            mention_members=sorted({key for member in members for key in by_kind[member]['source_members']}),
            label=concept_label(definition, by_kind[members[0]]['raw_type']),
            definition=definition, shared_attributes=[tokens[key] for key in sorted(shared_ids)],
            ontology_frozen=True, frozen=True, semantic_status='candidate',
            method='minimal_multi_kind_core_closure', definition_scope='observed_attribute_vocabulary_not_universal_axiom'))
    membership = defaultdict(set)
    for ontology in ontologies:
        for key in ontology['kind_members']:
            membership[key].add(ontology['id'])
    connections = semantic_connections(nodes, edges, set(objects))
    relation_groups = {}
    residual = []
    linked_connections = set()
    evidence_details = {}
    for connection in connections:
        key = connection['id']
        evidence_details[key] = dict(connection, annotation=nodes[key].get('annotation', {}))
        head = membership[object_to_kind.get(connection['source'])]
        tail = membership[object_to_kind.get(connection['target'])]
        pairs = [(a, b) for a in sorted(head) for b in sorted(tail) if a != b]
        if not pairs:
            residual.append(dict(connection, reason='internal_relation' if head and tail else 'unmapped_endpoint'))
            continue
        linked_connections.add(key)
        for a, b in pairs:
            group_key = (a, connection['predicate'], b)
            group = relation_groups.setdefault(group_key, dict(id=identifier(VERSION, 'relation', *group_key),
                source=a, target=b, predicate=connection['predicate'], predicate_label=translate_term(connection['predicate']),
                weight=0, connection_ids=[], grounding='member_evidence_not_universal'))
            if key not in group['connection_ids']:
                group['connection_ids'].append(key)
                group['weight'] += 1
    unassigned = []
    for profile in profiles:
        if membership[profile['id']]:
            continue
        reason = 'no_supported_distinct_peer' if profile['eligible'] else 'insufficient_core_attributes'
        unassigned.append(dict(kind=profile['id'], reason=reason, reason_label=(
            '没有属性证据支持的其他不同品种' if profile['eligible'] else '固有核心属性不足')))
    summary = dict(version=VERSION, source_objects=len(objects), source_mentions=sum(n['kind'] == 'entity_mention' for n in objects.values()),
        kind_count=len(profiles), eligible_kinds=sum(p['eligible'] for p in profiles),
        new_ontologies=len(ontologies)-len(frozen), existing_ontologies=len(frozen),
        clustered_kinds=len({k for k in membership if membership[k]}),
        clustered_mentions=len({m for o in ontologies for m in o['mention_members']}), unassigned_kinds=len(unassigned),
        relation_groups=len(relation_groups), original_relations=len(connections),
        between_ontology_connections=len(linked_connections), unmapped_connections=sum(r['reason'] == 'unmapped_endpoint' for r in residual),
        internal_connections=sum(r['reason'] == 'internal_relation' for r in residual),
        relationship_generated_ontologies=0, formal_semantics_verified=False,
        input_sha256={name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['nodes.jsonl', 'edges.jsonl']})
    return dict(summary=summary, kind_profiles=profiles, ontologies=ontologies,
                relations=sorted(relation_groups.values(), key=lambda r: r['id']), unassigned=unassigned,
                residual_relations=residual, relation_evidence=evidence_details, _raw_nodes=raw_nodes, _raw_edges=edges)


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def exported_graph(data):
    graph_nodes = {n['id']: n for n in data['_raw_nodes']}
    graph_edges = list(data['_raw_edges'])
    for profile in data['kind_profiles']:
        if profile['id'] not in graph_nodes:
            graph_nodes[profile['id']] = dict(profile, kind='entity_kind')
    for ontology in data['ontologies']:
        graph_nodes[ontology['id']] = ontology
        for member in ontology['kind_members']:
            graph_edges.append(dict(id=identifier(member, ontology['id'], 'SUBKIND_OF'),
                                    source=member, target=ontology['id'], type='SUBKIND_OF'))
    for relation in data['relations']:
        graph_edges.append(dict(relation, type='ONTOLOGY_RELATION'))
    unique_edges = {}
    for edge in graph_edges:
        if edge['id'] in unique_edges and unique_edges[edge['id']] != edge:
            raise ValueError('Conflicting graph edge identifiers')
        unique_edges[edge['id']] = edge
    return graph_nodes, unique_edges


def build(source, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    data = infer(source)
    output.mkdir(parents=True)
    for filename, key in [('kind_profiles.jsonl', 'kind_profiles'), ('minimal_ontologies.jsonl', 'ontologies'),
                          ('ontology_relations.jsonl', 'relations'), ('unassigned_kinds.jsonl', 'unassigned'),
                          ('residual_relations.jsonl', 'residual_relations')]:
        write_rows(output/filename, data[key])
    member_rows = [dict(kind=kind, ontology=o['id']) for o in data['ontologies'] for kind in o['kind_members']]
    write_rows(output/'membership.jsonl', member_rows)
    graph_nodes, unique_edges = exported_graph(data)
    data.pop('_raw_nodes')
    data.pop('_raw_edges')
    write_rows(output/'nodes.jsonl', graph_nodes.values())
    write_rows(output/'edges.jsonl', unique_edges.values())
    (output/'summary.json').write_text(json.dumps(data['summary'], ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'relation_evidence.json').write_text(json.dumps(data['relation_evidence'], ensure_ascii=False), encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    template = Path(__file__).with_name('minimal_ontology_report.html').read_text(encoding='utf-8')
    if template.count('__MINIMAL_DATA__') != 1:
        raise ValueError('Invalid report payload marker')
    (output/'属性本体与本体间关系.html').write_text(template.replace('__MINIMAL_DATA__', payload), encoding='utf-8')
    return data['summary']


def validate(source, output):
    output = Path(output)
    expected = infer(source)
    for filename, key in [('kind_profiles.jsonl', 'kind_profiles'), ('minimal_ontologies.jsonl', 'ontologies'),
                          ('ontology_relations.jsonl', 'relations'), ('unassigned_kinds.jsonl', 'unassigned'),
                          ('residual_relations.jsonl', 'residual_relations')]:
        if read_rows(output/filename) != expected[key]:
            raise ValueError('Output was changed or incomplete: ' + filename)
    if json.loads((output/'summary.json').read_text(encoding='utf-8')) != expected['summary']:
        raise ValueError('Summary does not match the inputs')
    if json.loads((output/'relation_evidence.json').read_text(encoding='utf-8')) != expected['relation_evidence']:
        raise ValueError('Relationship evidence changed')
    members = [dict(kind=k, ontology=o['id']) for o in expected['ontologies'] for k in o['kind_members']]
    if read_rows(output/'membership.jsonl') != members:
        raise ValueError('Ontology membership changed')
    node_rows = read_rows(output/'nodes.jsonl')
    actual_nodes = {n['id']: n for n in node_rows}
    expected_nodes, expected_edges = exported_graph(expected)
    if len(actual_nodes) != len(node_rows) or actual_nodes != expected_nodes:
        raise ValueError('Exported graph nodes changed or incomplete')
    graph_edges = read_rows(output/'edges.jsonl')
    edge_ids = {e['id']: e for e in graph_edges}
    if len(edge_ids) != len(graph_edges) or any(e['source'] not in actual_nodes or e['target'] not in actual_nodes for e in graph_edges):
        raise ValueError('Invalid exported graph endpoints or duplicate edges')
    if edge_ids != expected_edges:
        raise ValueError('Exported graph edges changed or incomplete')
    # Independently check each projected group's exact predicate, direction and
    # counted evidence against raw semantic links and explicit kind membership.
    mention_owner = defaultdict(set)
    by_kind = {p['id']: p for p in expected['kind_profiles']}
    for ontology in expected['ontologies']:
        if len(set(ontology['kind_members'])) < 2:
            raise ValueError('A minimal ontology needs distinct kinds')
        for kind in ontology['kind_members']:
            for mention in by_kind[kind]['source_members'] + by_kind[kind]['reference_ids']:
                mention_owner[mention].add(ontology['id'])
    for relation in expected['relations']:
        ids = relation['connection_ids']
        if relation['weight'] != len(set(ids)):
            raise ValueError('Relation weight is not a unique evidence count')
        for key in ids:
            evidence = expected['relation_evidence'][key]
            if (relation['source'] not in mention_owner[evidence['source']] or
                    relation['target'] not in mention_owner[evidence['target']] or
                    relation['predicate'] != evidence['predicate']):
                raise ValueError('Projected relation lost its semantic direction or predicate')
    result = dict(passed=True, distinct_kinds_checked=len(by_kind), ontologies_checked=len(expected['ontologies']),
                  relationship_groups_checked=len(expected['relations']),
                  original_relations_accounted_for=expected['summary']['original_relations'],
                  frozen_nodes_preserved=True, formal_semantics_verified=False)
    (output/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='不同品种属性生成最小本体，再投影本体之间的关系')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(validate(args.source, args.output) if args.validate_only else build(args.source, args.output), ensure_ascii=False))
