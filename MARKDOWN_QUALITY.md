# Markdown 质量检查

## 检查范围

服务器项目位于 `/root/autodl-tmp/rag`。输入为 `pdf/` 和 `markdown/`，业务数据与检查产物不提交 GitHub。

`scripts/chunk_pipeline.py` 与 `scripts/issue_diagnostics.py` 来自服务器已有检查代码；前者仅将两处 BOM 前缀处理改为兼容本机 Python 3.8 的等价写法。服务器原有文件未覆盖。

`scripts/audit_markdown.py` 复用既有文件发现、编码、结构和图片路径检查规则，逐篇核对：

- PDF 与非空 Markdown 的对应关系、UTF-8、替换字符和异常控制字符。
- 表格、代码围栏、公式边界，以及本地图片路径、内嵌图片 Base64 编码。
- 可用结构文件中的全文标记、页数和索引，结构正文与 Markdown 的词项覆盖。
- PDF 每页可提取文本与 Markdown 的词项覆盖；有结构文件时也逐页比较转换结构。

词项覆盖以英文词和中文单字计算，至少 40 个词项且覆盖不足 50% 时列为候选；它不验证阅读顺序、数字、公式或语义。正常符号、断词、扫描页也可能造成提示，不能自动当作错误修复。

含图片 Base64 的源文件保留原样。检查使用去除图片编码的 `audit_text/` 派生正文，问题行列指向这个派生文件，而不是声称为原 Markdown 的字符偏移。PDF 页码来自原 PDF 或页面结构，不由 Markdown 推测。

## 执行和验证

服务器已安装 `pypdfium2`，检查无需 GPU 或语言模型。应在新目录中执行，避免覆盖历史检查：

```bash
python scripts/audit_markdown.py --base /root/autodl-tmp/rag --output /root/autodl-tmp/rag/quality_runs/<新批次>
python scripts/validate_markdown_audit.py --output /root/autodl-tmp/rag/quality_runs/<新批次>
```

结果为 `summary.json`、逐篇 `documents.jsonl`、逐问题 `issues.jsonl` 及定位正文 `audit_text/`。
独立验证仅保证统计、记录和证据路径一致，不代表候选异常已修复或语义质量合格。

## 嵌套目录整理

指定 `--normalize-id <文档标识>` 可将嵌套文档目录整理至 `markdown/<文档标识>/`。脚本要求来源唯一、目标不存在、图片资源自包含；有外部相对资源或链接时拒绝移动。先备份目录，再移动，并核对所有文件 SHA-256。回滚使用该批次 `normalization.json` 中的原路径、目标路径和备份路径。
