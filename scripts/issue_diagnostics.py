"""细分问题类型并记录可核实的位置，不推断缺失页码或不可靠的原文映射。"""
import bisect
import hashlib
import json
import re

SCHEMA_VERSION = 2
# 级别区分检测到的错误、待核查候选和规则提示，不把所有标记都认定为损坏。
CATALOG = {
    'TABLE_HTML_OVERSIZED': ('切块长度', 'warning', '复杂网页表格超长', '检查合并单元格，按逻辑子表拆分或单独索引'),
    'TABLE_ROW_OVERSIZED': ('切块长度', 'warning', '表格单行超长', '检查该行的长单元格，不截断单元格内容'),
    'TABLE_HEADER_OVERSIZED': ('切块长度', 'warning', '表头超长', '压缩冗余表头或分组列，同时保留原表'),
    'TABLE_OVERSIZED': ('切块长度', 'warning', '表格块超长', '按完整行或逻辑子表拆分'),
    'FORMULA_OVERSIZED': ('切块长度', 'warning', '公式块超长', '核对公式边界，完整公式可单独保存和索引'),
    'CODE_OVERSIZED': ('切块长度', 'warning', '代码块超长', '按函数或完整逻辑单元拆分'),
    'IMAGE_CAPTION_OVERSIZED': ('切块长度', 'warning', '图片或图注块超长', '核对图注边界及是否仍含内嵌编码'),
    'HEADING_CONTEXT_OVERSIZED': ('切块长度', 'warning', '标题上下文导致超长', '检查标题识别或减少重复的标题上下文'),
    'CHUNK_TEXT_OVERSIZED': ('切块长度', 'warning', '正文切块超长', '核对切分预算和结构边界'),
    'ENCODING_REPLACEMENT_CHARACTER': ('字符编码', 'warning', '出现替换字符 U+FFFD', '按具体行列对照原 PDF，不直接猜测替换字符'),
    'ENCODING_NUL_CHARACTER': ('字符编码', 'error', '出现空字符 U+0000', '核对原始内容后移除非法空字符或重新转换'),
    'ENCODING_CONTROL_CHARACTER': ('字符编码', 'warning', '出现异常控制字符', '确认是否为分页符或识别残留，不自动删除'),
    'MARKDOWN_UTF8_INVALID': ('字符编码', 'error', 'Markdown 不是有效 UTF-8', '按字节位置核对编码或重新导出'),
    'REPEATED_LINE': ('重复内容', 'info', '同一行多次重复', '对照原 PDF 判断是否为页眉页脚，正常重复内容应保留'),
    'IMAGE_FILE_MISSING': ('图片资源', 'error', '本地图片文件缺失', '核对图片路径和转换输出目录'),
    'IMAGE_PATH_OUTSIDE_ROOT': ('图片资源', 'warning', '图片路径超出文献目录', '确认图片来源后复制到文献资源目录'),
    'IMAGE_BASE64_INVALID': ('图片资源', 'error', '内嵌图片编码无效', '重新导出该图片或重新转换对应页面'),
    'IMAGE_MEDIA_TYPE_UNSUPPORTED': ('图片资源', 'warning', '内嵌图片格式不支持', '确认格式并转换为受支持的图片格式'),
    'IMAGE_EMPTY_FILE': ('图片资源', 'error', '图片内容为空', '重新导出该图片'),
    'IMAGE_EXPORT_FAILED': ('图片资源', 'error', '图片导出失败', '查看错误类型和具体图片引用'),
    'HTML_TABLE_UNCLOSED': ('结构语法', 'warning', '网页表格标签未闭合', '检查表格起始位置与后续页面，确认是否截断'),
    'CODE_FENCE_UNCLOSED': ('结构语法', 'warning', '代码围栏未闭合', '核对围栏起止位置后修复'),
    'MATH_FENCE_UNCLOSED': ('结构语法', 'warning', '公式定界符未闭合', '对照原 PDF 检查公式结束位置'),
    'TABLE_COLUMN_COUNT_INCONSISTENT': ('结构语法', 'warning', '表格列数不一致候选', '核对标记行，确认是否有未转义竖线或单元格缺失'),
    'PAGE_COUNT_FEWER': ('转换完整性', 'warning', '转换页数少于 PDF', '核对缺少页面，不能仅凭页数判断正文是否遗漏'),
    'PAGE_COUNT_MORE': ('转换完整性', 'warning', '转换页数多于 PDF', '检查重复页、来源错配或结构数据异常'),
    'PAGE_INDEX_DUPLICATE': ('页面结构', 'warning', '页面索引重复', '核对结构文件标出的页面节点'),
    'PAGE_INDEX_GAP': ('页面结构', 'warning', '页面索引缺失或错序', '核对页面索引及全文导出范围'),
    'PAGE_SCOPE_PARTIAL': ('页面结构', 'warning', '结构结果标记为非全文', '重新导出全文结构或确认解析页范围'),
    'STRUCTURE_JSON_INVALID': ('页面结构', 'warning', '页面结构 JSON 无效', '按结构文件行列修复或重新转换'),
    'STRUCTURE_SCHEMA_INVALID': ('页面结构', 'warning', '页面结构格式不兼容', '检查结构字段和转换工具版本'),
    'STRUCTURE_EMPTY': ('页面结构', 'warning', '页面结构没有正文', '核对结构文件与原 PDF'),
    'DOCUMENT_SHORT': ('转换完整性', 'warning', '正文过短候选', '对照原 PDF 判断是否为正常短文'),
    'DOCUMENT_EMPTY': ('转换完整性', 'error', '转换结果为空', '重新转换并确认正文非空'),
    'DOCUMENT_WHITESPACE_ONLY': ('转换完整性', 'error', '转换结果只有空白', '重新转换并核对正文'),
    'DOCUMENT_NO_BLOCKS': ('结构语法', 'error', '没有可切分的正文结构', '检查 Markdown 及结构解析逻辑'),
    'SOURCE_AMBIGUOUS': ('文件对应', 'error', 'PDF 与 Markdown 对应不唯一', '核对同名文件并指定正确来源'),
    'SOURCE_CHANGED_DURING_READ': ('文件状态', 'warning', '读取期间源文件发生变化', '待写入完成后重新验证'),
    'PDF_UNREADABLE': ('文件读取', 'error', '无法读取 PDF 页数', '检查 PDF 损坏、加密及读取权限'),
    'PROCESSING_FAILED': ('程序执行', 'error', '文档处理失败', '查看处理阶段、异常类型及代码位置'),
    'UNCLASSIFIED': ('未分类', 'warning', '尚未分类的问题', '查看原始详情并补充诊断规则'),
}


class SourceIndex:
    def __init__(self, text, path=None, basis='source_markdown', page=None):
        self.text = text
        self.path = str(path) if path else None
        self.basis = basis
        self.page = page
        self.starts = [0] + [m.end() for m in re.finditer('\n', text)]

    def location(self, start=0, end=None):
        start = min(max(start, 0), len(self.text))
        end = min(max(end if end is not None else start + 1, start), len(self.text))
        line = bisect.bisect_right(self.starts, start) - 1
        # 结束位置采用闭区间行列；字符偏移的结束值采用开区间。
        last = max(start, end - 1)
        end_line = bisect.bisect_right(self.starts, last) - 1
        value = dict(basis=self.basis, line=line + 1, column=start - self.starts[line] + 1,
                     end_line=end_line + 1, end_column=last - self.starts[end_line] + 1,
                     char_start=start, char_end=end, match='exact')
        if self.path:
            value['path'] = self.path
        if self.page is not None:
            value['page'] = self.page
        return value

    def matches(self, needle):
        if not needle:
            return []
        found = []
        start = 0
        while True:
            position = self.text.find(needle, start)
            if position < 0:
                return found
            found.append(self.location(position, position + len(needle)))
            start = position + max(1, len(needle))


def excerpt(text, maximum=260):
    text = re.sub(r'data:image/[^;\s]+;base64,[A-Za-z0-9+/=\s]+', '[内嵌图片编码已省略]', text)
    text = re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', lambda m: f'〈U+{ord(m.group()):04X}〉', text)
    return text[:maximum] + ('…' if len(text) > maximum else '')


def classify(kind, details):
    if details.get('code') in CATALOG:
        return details['code']
    if kind == 'oversized_atomic_block':
        return {'table': 'TABLE_OVERSIZED', 'formula': 'FORMULA_OVERSIZED',
                'code': 'CODE_OVERSIZED', 'image': 'IMAGE_CAPTION_OVERSIZED'}.get(
                    (details.get('content_type') or [''])[0], 'CHUNK_TEXT_OVERSIZED')
    if kind == 'page_count_mismatch':
        return 'PAGE_COUNT_FEWER' if details['converted_pages'] < details['pdf_pages'] else 'PAGE_COUNT_MORE'
    return {'suspect_encoding': 'ENCODING_REPLACEMENT_CHARACTER', 'repeated_line_candidates': 'REPEATED_LINE',
            'image_export_error': 'IMAGE_EXPORT_FAILED', 'missing_image': 'IMAGE_FILE_MISSING',
            'structured_fallback': 'STRUCTURE_SCHEMA_INVALID', 'short_document': 'DOCUMENT_SHORT',
            'processing_error': 'PROCESSING_FAILED', 'ambiguous': 'SOURCE_AMBIGUOUS',
            'empty_document': 'DOCUMENT_EMPTY'}.get(kind, 'UNCLASSIFIED')


def enrich_issue(docid, kind, details, source=None):
    result = dict(details)
    code = classify(kind, result)
    category, severity, message, suggestion = CATALOG[code]
    locations = result.get('locations') or []
    if not locations and source:
        reference = result.get('reference')
        if reference:
            locations = source.matches(reference)
        elif code in {'DOCUMENT_SHORT', 'DOCUMENT_EMPTY', 'DOCUMENT_WHITESPACE_ONLY', 'DOCUMENT_NO_BLOCKS'}:
            locations = [source.location(0, min(len(source.text), 1))]
    if not locations:
        locations = [dict(basis='document', path=source.path if source else None,
                          match='not_locatable', reason='文档级问题或没有可靠的字符映射')]
    result.update(schema_version=SCHEMA_VERSION, code=code, category=category, severity=severity,
                  message=message, suggestion=suggestion, locations=locations)
    identity = json.dumps([docid, code, locations, result.get('chunk_index'), result.get('reference')], sort_keys=True, ensure_ascii=False)
    result['issue_id'] = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    return result


def scan_text(text, path):
    index = SourceIndex(text, path)
    issues = []
    for match in re.finditer('[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
        value = ord(match.group())
        code = ('ENCODING_REPLACEMENT_CHARACTER' if value == 0xfffd else
                'ENCODING_NUL_CHARACTER' if value == 0 else 'ENCODING_CONTROL_CHARACTER')
        issues.append(dict(type='suspect_encoding', code=code, character=f'U+{value:04X}',
                           locations=[index.location(match.start(), match.end())],
                           excerpt=excerpt(text[max(0, match.start() - 60):match.end() + 60])))
    repeats = {}
    for number, line in enumerate(text.split('\n')):
        stripped = line.strip()
        if 4 <= len(stripped) <= 120 and not re.fullmatch(r'[-|:=+_\s]+', stripped):
            offset = index.starts[number] + len(line) - len(line.lstrip())
            repeats.setdefault(stripped, []).append(index.location(offset, offset + len(stripped)))
    for line, locations in repeats.items():
        if len(locations) >= 5:
            issues.append(dict(type='repeated_line_candidates', code='REPEATED_LINE',
                               occurrence_count=len(locations), locations=locations,
                               excerpt=excerpt(line), action='仅报告，不自动删除'))
    return issues


def block_syntax_issues(blocks):
    issues = []
    for block_number, block in enumerate(blocks):
        text = block.text
        code = None
        if block.kind == 'table' and '<table' in text.lower() and '</table>' not in text.lower():
            code = 'HTML_TABLE_UNCLOSED'
        elif block.kind == 'code':
            lines = text.splitlines()
            opening = re.match(r'\s*(`{3,}|~{3,})', lines[0])
            if opening and (len(lines) < 2 or not re.fullmatch(r'\s*' + re.escape(opening[1][0]) + '{' + str(len(opening[1])) + r',}\s*', lines[-1])):
                code = 'CODE_FENCE_UNCLOSED'
        elif block.kind == 'formula':
            stripped = text.strip()
            fence = '$$' if stripped.startswith('$$') else '\\]'
            if not stripped.endswith(fence) or (fence == '$$' and len(stripped) <= 2):
                code = 'MATH_FENCE_UNCLOSED'
        if code:
            issues.append(dict(type='structure_syntax', code=code, locations=[block.location] if block.location else [],
                               block_id=block_number, excerpt=excerpt(text)))
        if block.kind == 'table' and '<table' not in text.lower():
            lines = text.split('\n')
            def columns(line):
                stripped = line.strip()
                cells = re.split(r'(?<!\\)\|', stripped)
                if stripped.startswith('|'):
                    cells = cells[1:]
                if re.search(r'(?<!\\)\|$', stripped):
                    cells = cells[:-1]
                return len(cells)
            expected = columns(lines[0])
            local = SourceIndex(text)
            for row_number, line in enumerate(lines[2:], 2):
                # 行内公式和代码中的竖线不按列分隔符推断，避免误报。
                if '`' in line or '$' in line or columns(line) == expected:
                    continue
                locations = []
                if block.location:
                    loc = dict(block.location)
                    start = local.starts[row_number]
                    loc.update(line=block.location['line'] + row_number,
                               end_line=block.location['line'] + row_number, column=1,
                               end_column=max(1, len(line)), char_start=block.location['char_start'] + start,
                               char_end=block.location['char_start'] + start + len(line))
                    locations = [loc]
                issues.append(dict(type='structure_syntax', code='TABLE_COLUMN_COUNT_INCONSISTENT',
                                   locations=locations, expected_columns=expected, actual_columns=columns(line),
                                   block_id=block_number, table_row=row_number + 1, excerpt=excerpt(line)))
    return issues


def oversized_details(chunk, blocks, counter, maximum):
    selected = [blocks[f['block']] for f in chunk['source_fragments']]
    first = selected[0]
    text = chunk['source_text']
    code = 'CHUNK_TEXT_OVERSIZED'
    details = dict(chunk_index=chunk['chunk_index'], token_count=chunk['token_count'], limit=maximum,
                   excess_tokens=chunk['token_count'] - maximum,
                   page_start=chunk['page_start'], page_end=chunk['page_end'],
                   content_type=chunk['content_type'], heading_path=chunk['heading_path'],
                   block_ids=[b.block_id for b in selected], locations=[b.location for b in selected if b.location],
                   excerpt=excerpt(text))
    body_tokens = counter.count(text)
    if body_tokens <= maximum:
        code = 'HEADING_CONTEXT_OVERSIZED'
    elif first.kind == 'table':
        if '<table' in text.lower():
            code = 'TABLE_HTML_OVERSIZED'
            details.update(table_rows=len(re.findall(r'<tr\b', text, re.I)),
                           merged_cells=bool(re.search(r'(?:rowspan|colspan)\s*=', text, re.I)))
        else:
            lines = text.splitlines()
            header = '\n'.join(lines[:2])
            header_tokens = counter.count(header)
            row_tokens = [counter.count(row) for row in lines[2:]]
            code = 'TABLE_HEADER_OVERSIZED' if header_tokens >= maximum else 'TABLE_ROW_OVERSIZED'
            details.update(header_tokens=header_tokens, largest_row_tokens=max(row_tokens, default=0),
                           table_rows=len(row_tokens))
    else:
        code = {'formula': 'FORMULA_OVERSIZED', 'code': 'CODE_OVERSIZED',
                'image': 'IMAGE_CAPTION_OVERSIZED'}.get(first.kind, code)
    details['code'] = code
    return details
