"""Independently verify an audit's record counts and evidence paths."""
import argparse
from collections import Counter
import json
from pathlib import Path


def validate(output):
    summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    if summary['status'] != 'completed':
        raise ValueError('Audit has not completed')
    rows = [json.loads(line) for line in (output / 'documents.jsonl').read_text(encoding='utf-8').splitlines()]
    if len(rows) != summary['unique_documents'] or len(rows) != summary['counts']['checked']:
        raise ValueError('Document counts differ')
    if len({r['document_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate document IDs')
    issues = Counter()
    by_document = Counter()
    with (output / 'issues.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            item = json.loads(line)
            issues[item['code']] += 1
            by_document[item['document_id']] += 1
    if dict(issues) != summary['issue_codes']:
        raise ValueError('Issue totals differ')
    if set(by_document) - {r['document_id'] for r in rows}:
        raise ValueError('Issue references unknown document')
    for row in rows:
        if row['issue_count'] != by_document[row['document_id']]:
            raise ValueError('Per-document issue count differs')
        if row.get('pdf_pages', 0) != row.get('text_checkable_pages', 0) + row.get('text_unverifiable_pages', 0):
            raise ValueError('Page counts differ')
        if row.get('diagnostic_text') and not Path(row['diagnostic_text']).is_file():
            raise ValueError('Diagnostic evidence missing')
    if sum(r.get('pdf_pages', 0) for r in rows) != summary['counts']['pdf_pages']:
        raise ValueError('Global page total differs')
    result = dict(passed=True, checked_documents=len(rows), checked_issues=sum(issues.values()),
                  semantic_accuracy_verified=False)
    (output / 'validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.output)))
