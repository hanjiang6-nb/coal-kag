"""Stream a quality audit using the server's existing diagnostic rules.

No model calls, source edits, image exports, or chunk regeneration.
PDF token coverage is a screening heuristic, never proof of semantic accuracy.
"""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chunk_pipeline import discover, image_issues, parse_blocks
from issue_diagnostics import scan_text, block_syntax_issues

IMAGE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)|<img\b[^>]*\bsrc=["\']([^"\']+)', re.I)
TOKENS = re.compile(r'[a-zA-Z]{2,}|[\u3400-\u9fff]')


def tokens(text):
    return TOKENS.findall(text.lower())


def coverage(source, available):
    values = tokens(source)
    return len(values), sum(word in available for word in values) / max(1, len(values))


def structure_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(structure_text(item) for item in value)
    if isinstance(value, dict):
        return ' '.join(structure_text(value[key]) for key in ('blocks', 'content', 'text', 'html', 'latex') if key in value)
    return ''


def text_without_images(raw):
    # Preserve newlines so existing line diagnostics remain attributable.
    return IMAGE.sub(lambda match: '\n' * match.group().count('\n'), raw)


def normalize_nested(base, docid, output):
    """Back up and move an entire self-contained result directory, never overwrite."""
    pdfs, markdowns = discover(base)
    matches = markdowns.get(docid, [])
    if len(pdfs.get(docid, [])) != 1 or len(matches) != 1:
        raise ValueError('Normalization requires one PDF and one Markdown')
    md = matches[0]
    source = md.parent.resolve()
    root = (base / 'markdown').resolve()
    destination = root / docid
    if source == destination:
        return dict(status='already_normalized', destination=str(destination))
    if root not in source.parents or source.name != docid or source.is_symlink():
        raise ValueError('Unexpected source directory')
    if destination.exists():
        raise ValueError('Destination exists; refusing overwrite')
    raw = md.read_text(encoding='utf-8-sig')
    for match in IMAGE.finditer(raw):
        ref = (match[1] or match[2]).strip().strip('<>')
        if re.match(r'^(https?:|data:|//|#)', ref):
            continue
        asset = (md.parent / ref).resolve()
        if source not in asset.parents or not asset.is_file():
            raise ValueError('Relative image outside result directory or missing')
    # Other relative links need manual review before relocating.
    if re.search(r'(?<!!)\[[^\]]*\]\((?!https?:|data:|#)[^)]+\)', raw, re.I):
        raise ValueError('Relative non-image link needs review')
    def hashes(folder):
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob('*') if p.is_file()}
    before = hashes(source)
    backup = output / 'backups' / docid
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, backup)
    if hashes(backup) != before:
        raise ValueError('Backup hash mismatch')
    source.rename(destination)
    if hashes(destination) != before:
        raise ValueError('Moved result hash mismatch; backup retained')
    result = dict(status='moved', source=str(source), destination=str(destination),
                  backup=str(backup), sha256=before,
                  rollback='Rename destination back to source after checking both paths')
    (output / 'normalization.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def audit_document(pdf, md, evidence=None):
    import pypdfium2
    raw = md.read_text(encoding='utf-8-sig', errors='strict')
    text = text_without_images(raw)
    # Embedded image payloads are not prose. Diagnose a saved derived view so
    # line/column offsets remain honest even when an image shares a text line.
    diagnostic_path = md
    if evidence is not None:
        evidence.write_text(text, encoding='utf-8')
        diagnostic_path = evidence
    issues = scan_text(text, str(diagnostic_path))
    issues += block_syntax_issues(parse_blocks(text, source_path=diagnostic_path))
    if evidence is None and text != raw:
        for item in issues:
            for location in item.get('locations', []):
                location.update(basis='derived_text', match='derived_view_required')
    for ref in image_issues(raw, md.parent):
        issues.append(dict(code='IMAGE_FILE_MISSING', reference=ref))
    for match in IMAGE.finditer(raw):
        ref = match[1] or match[2]
        if ref.startswith('data:') and ';base64,' in ref:
            try:
                decoded = base64.b64decode(''.join(ref.split(',', 1)[1].split()), validate=True)
                if not decoded:
                    raise ValueError('Empty embedded image')
            except Exception as exc:
                issues.append(dict(code='IMAGE_BASE64_INVALID', error=str(exc)[:150]))
    if not text.strip():
        issues.append(dict(code='DOCUMENT_WHITESPACE_ONLY'))
    elif len(text.strip()) < 200:
        issues.append(dict(code='DOCUMENT_SHORT', characters=len(text.strip())))
    sidecar = md.parent / 'middle_json.json'
    structure = None
    if sidecar.exists():
        try:
            structure = json.loads(sidecar.read_text(encoding='utf-8'))
            if not isinstance(structure.get('pages'), list):
                raise ValueError('pages must be a list')
        except Exception as exc:
            issues.append(dict(code='STRUCTURE_JSON_INVALID', error=str(exc)[:200]))
            structure = None
    if structure is None:
        issues.append(dict(code='PAGE_STRUCTURE_UNAVAILABLE'))
    document = pypdfium2.PdfDocument(str(pdf))
    available = set(tokens(text))
    checked_pages = 0
    total_source_tokens = 0
    weighted_coverage = 0
    try:
        page_count = len(document)
        if structure is not None:
            pages = structure['pages']
            indices = [p.get('page_idx') for p in pages]
            if indices != list(range(page_count)):
                issues.append(dict(code='PAGE_INDEX_OR_COUNT_MISMATCH', pdf_pages=page_count,
                                   converted_pages=len(pages), indices=indices))
            if structure.get('is_full_document') is False:
                issues.append(dict(code='PAGE_SCOPE_PARTIAL'))
            for item in pages:
                count, ratio = coverage(structure_text(item), available)
                if count >= 40 and ratio < 0.50:
                    issues.append(dict(code='STRUCTURE_TEXT_LOW_COVERAGE',
                                       page_idx=item.get('page_idx'), coverage=round(ratio, 4)))
        for index in range(page_count):
            page = document[index]
            tp = None
            try:
                tp = page.get_textpage()
                source = tp.get_text_range()
                count, ratio = coverage(source, available)
                if count >= 40:
                    checked_pages += 1
                    total_source_tokens += count
                    weighted_coverage += ratio * count
                    if ratio < 0.50:
                        issues.append(dict(code='PDF_TEXT_LOW_COVERAGE', page=index + 1,
                                           source_tokens=count, coverage=round(ratio, 4),
                                           excerpt=source[:180]))
                    if structure is not None and index < len(pages):
                        converted = set(tokens(structure_text(pages[index])))
                        _, page_ratio = coverage(source, converted)
                        if page_ratio < 0.50:
                            issues.append(dict(code='PDF_PAGE_CONVERSION_LOW_COVERAGE',
                                               page=index + 1, coverage=round(page_ratio, 4)))
            except Exception as exc:
                issues.append(dict(code='PDF_PAGE_TEXT_UNREADABLE', page=index + 1,
                                   error=str(exc)[:200]))
            finally:
                if tp is not None:
                    tp.close()
                page.close()
    finally:
        document.close()
    return dict(pdf_pages=page_count, structured_pages=len(structure['pages']) if structure else None,
                text_checkable_pages=checked_pages, text_unverifiable_pages=page_count-checked_pages,
                text_coverage=round(weighted_coverage/max(1, total_source_tokens), 4),
                characters=len(text.strip()), issues=issues)


def run(base, output):
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'documents.jsonl').exists():
        raise ValueError('Use a new output directory')
    pdfs, markdowns = discover(base)
    counts = Counter()
    codes = Counter()
    started = time.monotonic()
    evidence_root = output / 'audit_text'
    evidence_root.mkdir()
    def snapshot(status):
        value = dict(status=status, pdf_total=sum(map(len, pdfs.values())),
                     unique_documents=len(pdfs), counts=dict(counts), issue_codes=dict(codes),
                     seconds=round(time.monotonic()-started, 1),
                     semantic_accuracy_verified=False,
                     limitations='Text coverage flags candidates only; scanned pages without extractable text require visual review. Numeric and formula accuracy is not certified.')
        temporary = output / 'summary.json.part'
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        temporary.replace(output / 'summary.json')
        return value
    with (output / 'documents.jsonl').open('w', encoding='utf-8') as docs, (output / 'issues.jsonl').open('w', encoding='utf-8') as sink:
        for docid, sources in sorted(pdfs.items()):
            candidates = markdowns.get(docid, [])
            row = dict(document_id=docid, source_pdf=str(sources[0]),
                       source_markdown=[str(p) for p in candidates])
            try:
                if len(sources) != 1 or len(candidates) > 1:
                    result = dict(issues=[dict(code='SOURCE_AMBIGUOUS')])
                elif not candidates:
                    result = dict(issues=[dict(code='MARKDOWN_MISSING')])
                else:
                    evidence = evidence_root / (docid + '.md')
                    result = audit_document(sources[0], candidates[0], evidence)
                    result['diagnostic_text'] = str(evidence)
            except Exception as exc:
                result = dict(issues=[dict(code='AUDIT_FAILED', error=repr(exc))])
            problems = result.pop('issues')
            row.update(result, issue_count=len(problems), issue_codes=dict(Counter(p['code'] for p in problems)))
            for problem in problems:
                sink.write(json.dumps(dict(document_id=docid, **problem), ensure_ascii=False)+'\n')
                codes[problem['code']] += 1
            docs.write(json.dumps(row, ensure_ascii=False)+'\n')
            counts['checked'] += 1
            counts['with_issues'] += bool(problems)
            counts['pdf_pages'] += result.get('pdf_pages', 0)
            counts['text_checkable_pages'] += result.get('text_checkable_pages', 0)
            counts['text_unverifiable_pages'] += result.get('text_unverifiable_pages', 0)
            if counts['checked'] % 25 == 0:
                docs.flush(); sink.flush(); snapshot('running')
        snapshot('completed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--normalize-id')
    args = parser.parse_args()
    if args.normalize_id:
        args.output.mkdir(parents=True, exist_ok=True)
        print(json.dumps(normalize_nested(args.base.resolve(), args.normalize_id, args.output.resolve())))
    run(args.base.resolve(), args.output.resolve())
