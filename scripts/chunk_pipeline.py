"""无需模型调用的文档验收、清洗、结构切块与可追溯导出。"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback

from issue_diagnostics import (SourceIndex, block_syntax_issues, enrich_issue,
                               oversized_details, scan_text)

VERSION = '1.2.0'
IGNORED_PARTS = {'.ipynb_checkpoints', '.git', '__pycache__'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def eligible(path):
    return not any(p in IGNORED_PARTS or p.endswith('-checkpoint') for p in path.parts)


class TokenCounter:
    def __init__(self, tokenizer_path=None):
        self.encoder = None
        if tokenizer_path:
            from tokenizers import Tokenizer
            path = Path(tokenizer_path)
            self.encoder = Tokenizer.from_file(str(path))
            self.name = 'tokenizer.json:' + hashlib.sha256(path.read_bytes()).hexdigest()
            self.mode = 'exact'
        else:
            self.name = 'utf8_bytes_upper_bound'
            self.mode = 'estimate'

    def count(self, text):
        if self.encoder:
            return len(self.encoder.encode(text, add_special_tokens=False).ids)
        # 仅作离线验证及保守切分，不声称等同于模型的 token 数。
        return len(text.encode('utf-8'))


@dataclass
class Block:
    text: str
    kind: str
    page: int | None = None
    heading: int = 0
    block_id: int = -1
    fragment: int = 0
    location: dict | None = None


def clean_text(text):
    """只规范换行和 BOM，不擅自删除页眉、断词、数字或公式。"""
    return (text[1:] if text.startswith('\ufeff') else text).replace('\r\n', '\n').replace('\r', '\n').strip('\n') + '\n'


def parse_blocks(text, page=None, source_path=None):
    normalized = (text[1:] if text.startswith('\ufeff') else text).replace('\r\n', '\n').replace('\r', '\n')
    source = SourceIndex(normalized, source_path, 'rendered_page' if page is not None else 'source_markdown', page)
    lines = normalized.split('\n')
    def location(start, end):
        return source.location(source.starts[start], source.starts[end - 1] + len(lines[end - 1]))
    result = []
    i = 0
    boundary = re.compile(r'^\s*(?:#{1,6}\s|```|~~~|\$\$|\\\[|<table\b|\|)')
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        line = lines[i]
        start = i
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*$', line)
        if heading:
            result.append(Block(heading.group(2), 'heading', page, len(heading.group(1)), location=location(i, i + 1)))
            i += 1
            continue
        if i + 1 < len(lines) and re.fullmatch(r'\s*(?:={3,}|-{3,})\s*', lines[i + 1]):
            result.append(Block(line.strip(), 'heading', page, 1 if '=' in lines[i + 1] else 2, location=location(i, i + 2)))
            i += 2
            continue
        fence = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            kind = 'code'
            i += 1
            while i < len(lines):
                end = re.match(r'^\s*(' + re.escape(fence.group(1)[0]) + r'{3,})\s*$', lines[i])
                i += 1
                if end and len(end.group(1)) >= len(fence.group(1)):
                    break
        elif line.lstrip().startswith(('$$', '\\[')):
            kind = 'formula'
            closing = '$$' if line.lstrip().startswith('$$') else '\\]'
            i += 1
            if not (line.strip().endswith(closing) and len(line.strip()) > len(closing)):
                while i < len(lines):
                    end = closing in lines[i]
                    i += 1
                    if end:
                        break
        elif '<table' in line.lower():
            kind = 'table'
            i += 1
            while '</table>' not in '\n'.join(lines[start:i]).lower() and i < len(lines):
                i += 1
        elif ('|' in line and i + 1 < len(lines)
              and re.fullmatch(r'[\s|:\-]+', lines[i + 1]) and '-' in lines[i + 1]):
            kind = 'table'
            i += 2
            while i < len(lines) and lines[i].strip() and '|' in lines[i]:
                i += 1
        else:
            kind = 'list' if re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s)', line) else 'paragraph'
            i += 1
            while i < len(lines) and lines[i].strip() and not boundary.match(lines[i]):
                if i + 1 < len(lines) and re.fullmatch(r'\s*(?:={3,}|-{3,})\s*', lines[i + 1]):
                    break
                i += 1
            if re.search(r'!\[[^\]]*\]\(|<img\b', '\n'.join(lines[start:i])):
                kind = 'image'
        result.append(Block('\n'.join(lines[start:i]), kind, page, location=location(start, i)))
    # 图及紧邻图注绑定，避免只保留图片链接而丢失语义。
    combined = []
    for block in result:
        if (combined and combined[-1].kind == 'image' and block.kind == 'paragraph'
                and re.match(r'^(?:Fig(?:ure)?\.?\s*\d|图\s*\d)', block.text, re.I)):
            combined[-1].text += '\n\n' + block.text
            combined[-1].location.update({k: block.location[k] for k in ['end_line', 'end_column', 'char_end']})
        else:
            combined.append(block)
    return combined


def discover(base):
    pdfs = defaultdict(list)
    markdowns = defaultdict(list)
    for p in (base / 'pdf').rglob('*'):
        if p.is_file() and p.suffix.lower() == '.pdf' and eligible(p):
            pdfs[p.stem].append(p)
    for p in (base / 'markdown').rglob('*'):
        if p.is_file() and p.suffix.lower() in {'.md', '.markdown'} and eligible(p):
            key = p.parent.name if p.stem == 'markdown' else p.stem
            markdowns[key].append(p)
    return pdfs, markdowns


def externalize_images(text, markdown, output, docid, allowed_root):
    """复制原有图片或解码内嵌图片，让导出结果可独立携带。"""
    refs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
    refs += re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)', text, re.I)
    assets = []
    warnings = []
    for original in set(refs):
        ref = original.strip().strip('<>')
        if re.match(r'^(?:https?:|//|#)', ref):
            continue
        try:
            if ref.startswith('data:'):
                match = re.fullmatch(r'data:image/([\w.+-]+);base64,([\s\S]+)', ref)
                if not match:
                    raise ValueError('不支持的内嵌图片格式')
                payload = base64.b64decode(re.sub(r'\s', '', match.group(2)), validate=True)
                extension = {'jpeg': 'jpg', 'svg+xml': 'svg'}.get(match.group(1), match.group(1))
                if extension not in {'jpg', 'png', 'webp', 'gif', 'svg', 'bmp', 'tiff', 'tif'}:
                    raise ValueError('不支持的图片扩展名')
            else:
                source = (markdown.parent / ref).resolve()
                source.relative_to(allowed_root.resolve())
                payload = source.read_bytes()
                extension = source.suffix.lower().lstrip('.') or 'bin'
            if not payload:
                raise ValueError('图片为空')
            asset = output / 'assets' / docid / (hashlib.sha256(payload).hexdigest() + '.' + extension)
            asset.parent.mkdir(parents=True, exist_ok=True)
            if not asset.exists():
                asset.write_bytes(payload)
            exported_ref = '../' + asset.relative_to(output).as_posix()
            text = text.replace(original, exported_ref)
            assets.append(asset.relative_to(output).as_posix())
        except Exception as exc:
            if isinstance(exc, FileNotFoundError):
                code = 'IMAGE_FILE_MISSING'
            elif '为空' in str(exc):
                code = 'IMAGE_EMPTY_FILE'
            elif ref.startswith('data:'):
                code = 'IMAGE_MEDIA_TYPE_UNSUPPORTED' if '格式' in str(exc) or '扩展名' in str(exc) else 'IMAGE_BASE64_INVALID'
            elif isinstance(exc, ValueError) and 'subpath' in str(exc):
                code = 'IMAGE_PATH_OUTSIDE_ROOT'
            else:
                code = 'IMAGE_EMPTY_FILE' if '为空' in str(exc) else 'IMAGE_EXPORT_FAILED'
            warnings.append(dict(type='image_export_error', code=code, reference=ref[:180],
                                 reference_truncated=len(ref) > 180, exception_type=type(exc).__name__, detail=str(exc)[:250]))
    return text, sorted(set(assets)), warnings


def load_blocks(markdown, use_pages, transform=None):
    text = markdown.read_text(encoding='utf-8-sig', errors='strict')
    warnings = []
    sidecar = markdown.parent / 'middle_json.json'
    page_count = None
    if use_pages and sidecar.exists():
        try:
            structured_text = sidecar.read_text(encoding='utf-8')
            structure = json.loads(structured_text)
            from mineru.parser import ParseResult
            parsed = ParseResult.from_dict(structure)
            blocks = []
            indices = [p.page_idx for p in parsed.pages]
            if len(set(indices)) != len(indices):
                error = ValueError('页面索引重复')
                error.diagnostic_code = 'PAGE_INDEX_DUPLICATE'
                error.locations = [dict(basis='source_json', path=str(sidecar),
                                        json_pointer=f'/pages/{i}/page_idx', match='exact')
                                   for i, value in enumerate(indices) if value in indices[:i]]
                raise error
            if indices != list(range(len(indices))):
                error = ValueError('页面索引不连续或错序')
                error.diagnostic_code = 'PAGE_INDEX_GAP'
                error.locations = [dict(basis='source_json', path=str(sidecar),
                                        json_pointer=f'/pages/{i}/page_idx', match='exact')
                                   for i, value in enumerate(indices) if value != i]
                raise error
            if parsed.middle_json.is_full_document is False:
                error = ValueError('结构化结果不是全文')
                error.diagnostic_code = 'PAGE_SCOPE_PARTIAL'
                error.locations = [dict(basis='source_json', path=str(sidecar), json_pointer='/is_full_document', match='exact')]
                raise error
            markdown_index = SourceIndex(text, markdown)
            for page_number, page in enumerate(parsed.pages):
                single = ParseResult(middle_json=parsed.middle_json.model_copy(update={'pages': [page]}))
                rendered = single.markdown()
                page_blocks = parse_blocks(rendered, page.page_idx + 1, sidecar)
                for block in page_blocks:
                    pointer = f'/pages/{page_number}'
                    anchors = [(i, b) for i, b in enumerate(structure['pages'][page_number].get('blocks', []))
                               if b.get('type') == block.kind]
                    if len(anchors) == 1 and sum(b.kind == block.kind for b in page_blocks) == 1:
                        pointer += f'/blocks/{anchors[0][0]}'
                        if anchors[0][1].get('bbox'):
                            block.location['bbox'] = anchors[0][1]['bbox']
                    block.location['json_pointer'] = pointer
                    matches = markdown_index.matches(block.text)
                    if len(matches) == 1:
                        exact = matches[0]
                        exact.update(page=page.page_idx + 1, json_pointer=pointer, structure_path=str(sidecar))
                        if block.location.get('bbox'):
                            exact['bbox'] = block.location['bbox']
                        block.location = exact
                    elif len(matches) > 1:
                        block.location['markdown_match_count'] = len(matches)
                    if transform:
                        block.text = transform(block.text)
                blocks.extend(page_blocks)
            if not blocks:
                error = ValueError('结构化结果没有可用正文')
                error.diagnostic_code = 'STRUCTURE_EMPTY'
                raise error
            return text, blocks, 'structured_pages', len(parsed.pages), warnings
        except Exception as exc:
            location = dict(basis='source_json', path=str(sidecar), json_pointer='/pages', match='document_scope')
            code = getattr(exc, 'diagnostic_code', 'STRUCTURE_SCHEMA_INVALID')
            if isinstance(exc, json.JSONDecodeError):
                code = 'STRUCTURE_JSON_INVALID'
                location.update(line=exc.lineno, column=exc.colno, match='exact')
            warnings.append(dict(type='structured_fallback', code=code, detail=str(exc)[:300],
                                 exception_type=type(exc).__name__, locations=getattr(exc, 'locations', [location])))
    blocks = parse_blocks(text, source_path=markdown)
    if transform:
        for block in blocks:
            block.text = transform(block.text)
    return text, blocks, 'markdown_only', page_count, warnings


def prefix_for(headings):
    return '\n'.join('#' * (i + 1) + ' ' + h for i, h in enumerate(headings))


def split_text(text, budget, counter):
    """保留所有字符，优先在自然句界处拆分超长正文。"""
    parts = []
    while text:
        if counter.count(text) <= budget:
            parts.append(text)
            break
        low, high = 1, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if counter.count(text[:mid]) <= budget:
                low = mid
            else:
                high = mid - 1
        cut = low
        if counter.count(text[:cut]) > budget:
            raise ValueError('单字符超过可用预算')
        boundaries = [m.end() for m in re.finditer(r'[。！？!?\n]|[.;]\s', text[:cut])]
        if boundaries and boundaries[-1] >= cut // 2:
            cut = boundaries[-1]
        parts.append(text[:cut])
        text = text[cut:]
    return parts


def split_block(block, budget, counter):
    if counter.count(block.text) <= budget or block.kind in {'formula', 'code', 'image', 'heading'}:
        return [block]
    if block.kind == 'table':
        lines = block.text.splitlines()
        # HTML 合并单元格不能靠按行拆分保证语义，保持原子性并报告超限。
        if '<table' in block.text.lower() or len(lines) < 3:
            return [block]
        header = '\n'.join(lines[:2])
        pieces = []
        rows = []
        for row in lines[2:]:
            trial = header + '\n' + '\n'.join(rows + [row])
            if rows and counter.count(trial) > budget:
                pieces.append(header + '\n' + '\n'.join(rows))
                rows = []
            rows.append(row)
        if rows:
            pieces.append(header + '\n' + '\n'.join(rows))
    else:
        pieces = split_text(block.text, budget, counter)
    return [replace(block, text=t, fragment=i) for i, t in enumerate(pieces)]


def chunk_blocks(blocks, counter, target=700, maximum=1200, overlap=100):
    """重叠仅复用完整正文片段，不跨章节，也不复制表格和公式。"""
    if not (0 <= overlap < target <= maximum):
        raise ValueError('必须满足 0 <= 重叠 < 目标长度 <= 最大长度')
    chunks = []
    headings = []
    current = []
    carry = []

    def rendered(items):
        prefix = prefix_for(headings)
        body = '\n\n'.join(b.text for b in items)
        return (prefix + '\n\n' if prefix else '') + body

    def flush():
        nonlocal current, carry
        if not current:
            return
        body = '\n\n'.join(b.text for b in current)
        pages = [b.page for b in current if b.page is not None]
        text = rendered(current)
        chunks.append(dict(text=text, source_text=body, heading_path=list(headings),
                           page_start=min(pages) if pages else None,
                           page_end=max(pages) if pages else None,
                           content_type=sorted(set(b.kind for b in current)),
                           token_count=counter.count(text),
                           oversized=counter.count(text) > maximum,
                           source_fragments=[{'block': b.block_id, 'fragment': b.fragment,
                                              'content_hash': digest(b.text), 'location': b.location} for b in current]))
        carry = []
        for b in reversed(current):
            if b.kind not in {'paragraph', 'list'} or counter.count('\n\n'.join(x.text for x in [b] + carry)) > overlap:
                break
            carry.insert(0, b)
        current = []

    expected = set()
    for index, block in enumerate(blocks):
        block.block_id = index
        if block.kind == 'heading':
            flush()
            carry = []
            headings = headings[:block.heading - 1]
            headings.append(block.text)
            expected.add((index, 0, digest(block.text)))
            continue
        # 标题上下文也计入 token 预算，避免正文合规而最终文本超限。
        budget = max(1, maximum - counter.count(prefix_for(headings) + '\n\n') - 8)
        for part in split_block(block, budget, counter):
            expected.add((part.block_id, part.fragment, digest(part.text)))
            if current and counter.count(rendered(current + [part])) > maximum:
                flush()
            if not current and carry:
                if counter.count(rendered(carry + [part])) <= maximum:
                    current = list(carry)
                carry = []
            current.append(part)
            if counter.count(rendered(current)) >= target:
                flush()
    flush()
    # 孤立标题也输出，避免只含标题的文档丢失内容。
    used_headings = {h for c in chunks for h in c['heading_path']}
    for block in blocks:
        if block.kind == 'heading' and block.text not in used_headings:
            text = '# ' + block.text
            chunks.append(dict(text=text, source_text=block.text, heading_path=[block.text],
                               page_start=block.page, page_end=block.page, content_type=['heading'],
                               token_count=counter.count(text), oversized=counter.count(text) > maximum,
                               source_fragments=[{'block': block.block_id, 'fragment': 0,
                                                  'content_hash': digest(block.text), 'location': block.location}]))
    covered = {(f['block'], f['fragment'], f['content_hash']) for c in chunks for f in c['source_fragments']}
    for block in blocks:
        if block.kind == 'heading' and block.text in used_headings:
            covered.add((block.block_id, 0, digest(block.text)))
    if expected - covered:
        raise ValueError('切块覆盖校验失败')
    return chunks


def image_issues(text, parent):
    refs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
    refs += re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)', text, re.I)
    missing = []
    for ref in refs:
        ref = ref.strip().strip('<>')
        if re.match(r'^(?:https?:|data:|//|#)', ref):
            continue
        if not (parent / ref).is_file():
            missing.append(ref)
    return missing


def pdf_page_count(path):
    import pypdfium2
    document = pypdfium2.PdfDocument(str(path))
    try:
        return len(document)
    finally:
        document.close()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def execute(args):
    base = args.base.resolve()
    output = args.output.resolve()
    # 每次输出到全新目录，避免覆盖历史结果或污染输入。
    if output == base or base / 'pdf' == output or base / 'markdown' == output:
        raise ValueError('输出目录不能是输入目录')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'cleaned').mkdir()
    counter = TokenCounter(args.tokenizer)
    config = dict(version=VERSION, target=args.target, maximum=args.maximum,
                  overlap=args.overlap, tokenizer=counter.name, token_mode=counter.mode,
                  structured_pages=args.structured_pages, pdf_pages=args.pdf_pages,
                  document_ids=sorted(getattr(args, 'document_id', None) or []))
    revision = digest(json.dumps(config, sort_keys=True))
    pdfs, markdowns = discover(base)
    summary = dict(pdf_files=sum(map(len, pdfs.values())), unique_documents=len(pdfs),
                   markdown_files=sum(map(len, markdowns.values())), matched=0, missing=0,
                   empty=0, ambiguous=0, processed=0, errors=0, warnings=0,
                   chunks=0, oversized_chunks=0, page_known_documents=0,
                   orphan_markdown_ids=len(set(markdowns) - set(pdfs)),
                   snapshot_time=time.strftime('%Y-%m-%dT%H:%M:%S%z'), config=config)
    duplicates = Counter()
    started = time.monotonic()
    with (output / 'documents.jsonl').open('w', encoding='utf-8') as docs, \
         (output / 'chunks.jsonl').open('w', encoding='utf-8') as sink, \
         (output / 'issues.jsonl').open('w', encoding='utf-8') as issues:
        current_source = None
        seen_issues = set()
        def issue(docid, kind, **details):
            details = enrich_issue(docid, kind, details, current_source)
            if details['issue_id'] in seen_issues:
                return
            seen_issues.add(details['issue_id'])
            issues.write(json.dumps(dict(document_id=docid, type=kind,
                                        source_pdf=[str(p.relative_to(base)) for p in pdfs.get(docid, [])],
                                        source_markdown=[str(p.relative_to(base)) for p in markdowns.get(docid, [])],
                                        **details), ensure_ascii=False) + '\n')
            issues.flush()
            summary['warnings'] += 1

        for docid, paths in sorted(pdfs.items()):
            current_source = None
            row = dict(document_id=docid, source_pdf=str(paths[0].relative_to(base)))
            candidates = markdowns.get(docid, [])
            valid = [p for p in candidates if p.stat().st_size > 0]
            if len(paths) > 1 or len(valid) > 1:
                summary['ambiguous'] += 1
                row['status'] = 'ambiguous'
                issue(docid, 'ambiguous', pdfs=[str(p.relative_to(base)) for p in paths],
                      markdowns=[str(p.relative_to(base)) for p in valid],
                      locations=[dict(basis='document', path=str(p), match='document_scope',
                                      reason='同名来源不唯一，不能选择单个字符位置') for p in paths + valid])
            elif not valid:
                status = 'empty' if candidates else 'missing'
                summary[status] += 1
                row['status'] = status
                if candidates:
                    row['source_markdown'] = str(candidates[0].relative_to(base))
                    current_source = SourceIndex('', candidates[0])
                    issue(docid, 'empty_document', code='DOCUMENT_EMPTY')
            else:
                summary['matched'] += 1
                row.update(status='not_selected', source_markdown=str(valid[0].relative_to(base)))
                selected = not config['document_ids'] or docid in config['document_ids']
                if selected and (not args.limit or summary['processed'] + summary['errors'] < args.limit):
                    stage = '读取转换结果'
                    try:
                        md = valid[0]
                        before = md.stat()
                        assets = set()
                        image_warnings = []
                        def transform(text):
                            normalized, files, problems = externalize_images(text, md, output, docid, base / 'markdown')
                            assets.update(files)
                            image_warnings.extend(problems)
                            return normalized
                        raw, blocks, mode, pages, warnings = load_blocks(md, args.structured_pages, transform)
                        current_source = SourceIndex(raw, md)
                        warnings.extend(image_warnings)
                        if not raw.strip():
                            error = ValueError('正文只有空白字符')
                            error.diagnostic_code = 'DOCUMENT_WHITESPACE_ONLY'
                            raise error
                        if len(blocks) == 0:
                            error = ValueError('正文没有可切分的结构块')
                            error.diagnostic_code = 'DOCUMENT_NO_BLOCKS'
                            raise error
                        stage = '读取PDF页数'
                        pdf_pages = pdf_page_count(paths[0]) if args.pdf_pages else None
                        if pages is not None and pdf_pages is not None and pages != pdf_pages:
                            warnings.append(dict(type='page_count_mismatch', pdf_pages=pdf_pages, converted_pages=pages))
                        for warning in warnings:
                            issue(docid, **{('kind' if k == 'type' else k): v for k, v in warning.items()})
                        for ref in image_issues(raw, md.parent):
                            issue(docid, 'missing_image', reference=ref)
                        stage = '质量诊断'
                        for problem in scan_text(raw, md):
                            kind = problem.pop('type')
                            issue(docid, kind, **problem)
                        # 结构闭合检查基于完整原 Markdown，避免把跨页结构误报为未闭合。
                        for problem in block_syntax_issues(parse_blocks(raw, source_path=md)):
                            kind = problem.pop('type')
                            issue(docid, kind, **problem)
                        if len(raw.strip()) < 200:
                            issue(docid, 'short_document', characters=len(raw.strip()))
                        stage = '结构切块'
                        chunks = chunk_blocks(blocks, counter, args.target, args.maximum, args.overlap)
                        after = md.stat()
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            error = ValueError('读取期间源文件变化，请下次重跑')
                            error.diagnostic_code = 'SOURCE_CHANGED_DURING_READ'
                            raise error
                        source_hash = digest(raw)
                        stage = '保存导出结果'
                        for index, chunk in enumerate(chunks):
                            content_hash = digest(chunk['text'])
                            chunk.update(chunk_id=digest(f'{docid}:{source_hash}:{revision}:{index}:{content_hash}'),
                                         document_id=docid, chunk_index=index, content_hash=content_hash,
                                         source_hash=source_hash, source_pdf=row['source_pdf'],
                                         source_markdown=row['source_markdown'], pipeline_version=VERSION,
                                         asset_base='cleaned', assets=[a for a in sorted(assets) if '../' + a in chunk['text']],
                                         config_hash=revision, tokenizer=counter.name, token_mode=counter.mode,
                                         document_title=next((b.text for b in blocks if b.kind == 'heading'), docid))
                            sink.write(json.dumps(chunk, ensure_ascii=False) + '\n')
                            duplicates[content_hash] += 1
                            summary['oversized_chunks'] += chunk['oversized']
                            if chunk['oversized']:
                                issue(docid, 'oversized_atomic_block', **oversized_details(chunk, blocks, counter, args.maximum))
                        (output / 'cleaned' / (docid + '.md')).write_text(
                            '\n\n'.join('#' * b.heading + ' ' + b.text if b.kind == 'heading' else b.text for b in blocks) + '\n', encoding='utf-8')
                        row.update(status='processed', chunks=len(chunks), source_hash=source_hash,
                                   page_source=mode, pdf_pages=pdf_pages, converted_pages=pages)
                        summary['chunks'] += len(chunks)
                        summary['processed'] += 1
                        summary['page_known_documents'] += pages is not None
                    except Exception as exc:
                        row.update(status='error', error=f'{type(exc).__name__}: {exc}')
                        summary['errors'] += 1
                        details = dict(error=row['error'], stage=stage, exception_type=type(exc).__name__,
                                       code=getattr(exc, 'diagnostic_code', 'PDF_UNREADABLE' if stage == '读取PDF页数' else 'PROCESSING_FAILED'),
                                       code_location=[dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                                                      for f in traceback.extract_tb(exc.__traceback__)[-3:]])
                        if isinstance(exc, UnicodeDecodeError):
                            prefix = exc.object[:exc.start].decode('utf-8', errors='replace')
                            offset = 3 if md.read_bytes().startswith(b'\xef\xbb\xbf') else 0
                            details.update(code='MARKDOWN_UTF8_INVALID', locations=[dict(
                                basis='source_bytes', path=str(md), byte_start=exc.start + offset,
                                byte_end=exc.end + offset, line=prefix.count('\n') + 1,
                                column=len(prefix.rsplit('\n', 1)[-1]) + 1, match='exact')])
                        elif details['code'] == 'PDF_UNREADABLE':
                            details['locations'] = [dict(basis='document', path=str(paths[0]),
                                                         match='document_scope', reason='读取 PDF 页数失败，无法定位到可靠页面')]
                        issue(docid, 'processing_error', **details)
            docs.write(json.dumps(row, ensure_ascii=False) + '\n')
            docs.flush()
    summary['duplicate_chunk_occurrences'] = sum(n - 1 for n in duplicates.values())
    summary['seconds'] = round(time.monotonic() - started, 2)
    write_json(output / 'summary.json', summary)
    write_json(output / 'config.json', config)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if summary['errors'] else 0


def main():
    parser = argparse.ArgumentParser(description='文档验收、结构切块与质量检查，不调用语言模型')
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tokenizer', help='最终使用模型的 tokenizer.json；未提供时明确标记为字节估算')
    parser.add_argument('--target', type=int, default=700)
    parser.add_argument('--maximum', type=int, default=1200)
    parser.add_argument('--overlap', type=int, default=100)
    parser.add_argument('--limit', type=int, default=0, help='最多处理的已有结果数，零表示全部')
    parser.add_argument('--document-id', action='append', help='只处理指定文档标识，可重复提供')
    parser.add_argument('--structured-pages', action='store_true', help='使用 MinerU 页面结构获取真实页码')
    parser.add_argument('--pdf-pages', action='store_true', help='核对已转换文档的 PDF 页数')
    args = parser.parse_args()
    if not (0 <= args.overlap < args.target <= args.maximum) or args.limit < 0:
        parser.error('长度参数或样本数量无效')
    return execute(args)


if __name__ == '__main__':
    sys.exit(main())
