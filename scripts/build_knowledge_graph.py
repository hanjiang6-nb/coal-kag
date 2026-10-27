"""Build a provenance graph from reviewed annotations, without model inference."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

SOURCES = [('binary_records_with_provenance.json', 'binary'),
           ('extended_records_v2.json', 'extended'),
           ('pending_review_records_v2.json', 'pending')]


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def identifier(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode('utf-8')).hexdigest()


def build(source, output):
    if output.exists():
        raise ValueError('Output already exists; use a new directory')
    schema = load(source / 'schema_catalog.json')
    entity_types = {e['label'] for e in schema['entities']}
    relation_types = {e['label'] for e in schema['relations']}
    nodes, edges, problems = {}, [], []
    counts = Counter()
    manifest = {}
    def node(key, kind, payload):
        if key in nodes and nodes[key] != dict(id=key, kind=kind, **payload):
            raise ValueError('Node identity collision')
        nodes[key] = dict(id=key, kind=kind, **payload)
    def edge(head, tail, kind):
        edges.append(dict(id=identifier(head, tail, kind), source=head, target=tail, type=kind))
    expected = load(source / 'checksums.json')
    checksum_results = {}
    for name, digest in expected.items():
        path = source / name
        checksum_results[name] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    if not all(checksum_results.values()):
        raise ValueError('Input checksum mismatch: ' + str([k for k,v in checksum_results.items() if not v]))
    for filename, layer in SOURCES:
        raw = (source / filename).read_bytes()
        manifest[filename] = hashlib.sha256(raw).hexdigest()
        for record in load(source / filename):
            annotation = record['annotation']
            passage = record['input']['PASSAGE']
            scope = record['task']['id']
            sid = identifier(layer, scope)
            document = annotation['paper_id']
            did = identifier('document', document)
            node(did, 'document', dict(document_id=document))
            node(sid, 'scope', dict(layer=layer, scope_id=scope, document_id=document,
                 passage=passage, provenance={k:v for k,v in record.items() if k not in {'annotation','training'}},
                 source_file=filename, human_verified=record.get('human_verified', False)))
            edge(sid, did, 'FROM_DOCUMENT')
            counts[layer + '_scopes'] += 1
            mapping = {}
            for entity in annotation.get('entities', []):
                eid = identifier(sid, 'entity', entity['id'])
                if entity['id'] in mapping:
                    raise ValueError('Duplicate entity ID in scope')
                mapping[entity['id']] = eid
                if entity['type'] not in entity_types:
                    problems.append(dict(scope=scope, code='UNKNOWN_ENTITY_TYPE', entity=entity))
                if entity['text'] not in passage:
                    problems.append(dict(scope=scope, code='ENTITY_TEXT_NOT_IN_PASSAGE', entity=entity))
                node(eid, 'entity_mention', dict(scope=sid, layer=layer, annotation=entity))
                edge(sid, eid, 'HAS_MENTION')
            for collection in ['relations','qualified_statements','observations','statistical_relations','unresolved_items']:
                for index, item in enumerate(annotation.get(collection, [])):
                    key = identifier(sid, collection, index)
                    evidence = item.get('evidence_text')
                    if evidence and evidence not in passage:
                        problems.append(dict(scope=scope, code='EVIDENCE_NOT_EXACT', collection=collection, index=index))
                    predicate = item.get('label', item.get('predicate'))
                    if collection in {'relations','qualified_statements'} and predicate not in relation_types:
                        problems.append(dict(scope=scope, code='UNKNOWN_RELATION', predicate=predicate))
                    # Only reviewed binary asserted claims enter the default fact view.
                    # Reported claims, extended scopes and pending scopes retain separate status.
                    eligible = (layer == 'binary' and collection == 'relations'
                                and item.get('modality') == 'asserted'
                                and record.get('binary_training_eligible') is True
                                and record.get('source_verification', {}).get('verified') is True)
                    node(key, collection, dict(scope=sid, layer=layer, predicate=predicate,
                         default_fact_eligible=eligible, human_verified=record.get('human_verified',False),
                         annotation=item))
                    edge(sid, key, 'HAS_STATEMENT')
                    for role in ['head','tail','object','analyte']:
                        value = item.get(role)
                        if value in mapping:
                            edge(key, mapping[value], role.upper())
                        elif collection in {'relations','qualified_statements'} and role in {'head','tail'}:
                            problems.append(dict(scope=scope,code='UNRESOLVED_ENDPOINT',role=role,value=value))
                    for role, value in (item.get('roles') or {}).items():
                        if value in mapping:
                            edge(key, mapping[value], 'ROLE_' + role.upper())
                    counts[layer + '_' + collection] += 1
                    counts['default_fact_statements'] += eligible
    output.mkdir(parents=True)
    for name, values in [('nodes.jsonl',nodes.values()),('edges.jsonl',edges),('audit_issues.jsonl',problems)]:
        with (output / name).open('w',encoding='utf-8') as stream:
            for value in values:
                stream.write(json.dumps(value,ensure_ascii=False)+'\n')
    db = sqlite3.connect(str(output / 'knowledge_graph.sqlite'))
    db.executescript('CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,layer TEXT,payload TEXT); CREATE TABLE edges(id TEXT PRIMARY KEY,source TEXT REFERENCES nodes(id),target TEXT REFERENCES nodes(id),type TEXT);')
    db.execute('PRAGMA foreign_keys=ON')
    db.executemany('INSERT INTO nodes VALUES(?,?,?,?)',[(n['id'],n['kind'],n.get('layer'),json.dumps(n,ensure_ascii=False)) for n in nodes.values()])
    db.executemany('INSERT OR IGNORE INTO edges VALUES(:id,:source,:target,:type)',edges)
    db.execute("CREATE VIEW default_facts AS SELECT * FROM nodes WHERE json_extract(payload,'$.default_fact_eligible')=1")
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = db.execute('PRAGMA foreign_key_check').fetchall()
    db.commit(); db.close()
    result = dict(counts= dict(counts), nodes=len(nodes),edges=len({e['id'] for e in edges}),
                  audit_issues=len(problems), input_checksums=checksum_results,
                  source_sha256=manifest, sqlite_integrity=integrity,
                  foreign_key_errors=len(foreign_keys), semantic_accuracy_verified=False,
                  entity_resolution='scope-local mentions; no automatic cross-document identity merging',
                  validation_passed=not problems and integrity=='ok' and not foreign_keys)
    (output / 'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path('data'))
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = build(args.source,args.output)
    print(json.dumps(result,ensure_ascii=True))
    raise SystemExit(0 if result['validation_passed'] else 1)
