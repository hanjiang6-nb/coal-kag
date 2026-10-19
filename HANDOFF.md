# 交接记录

## 2026-10-19 — Markdown 全量质量检查与目录整理

- 完成：嵌套文档 `0a4ef632d36927c62acf4d99a2a2803293a0a14a10999bbefd793c233c6f9a2b` 已整理至独立文档目录；先备份再移动，文件 SHA-256 一致。
- 环境：本机 `Pugss`，Windows / PowerShell，`F:\kag`，Python 3.8；服务器 Ubuntu 22.04.5、Python 3.12，项目 `/root/autodl-tmp/rag`，当前 0.5 核 CPU / 2 GiB 内存，无 GPU。连接信息见 `SERVER.md`。
- 实现：复用服务器原有诊断规则，新增逐篇审计和独立统计验证脚本；既有 `chunk_pipeline.py` 的本地副本仅做 Python 3.8 等价兼容调整，未覆盖服务器生产文件。
- 验证：本地 `python -m unittest discover -s tests -v` 11 项通过；服务器 `python -m unittest discover -s quality_tests -v` 7 项通过；服务器原有诊断测试 13 项通过。
- 检查状态：8,443 篇全量审计正在运行，目录 `quality_runs/audit_20261019_final`，不能将当前部分统计当作最终结果。
- 旧批次：`quality_runs/audit_20261019` 保留了归档备份和移动清单；该初始审计已停止并由最终批次替代，以避免反复解析内嵌图片编码。业务正文未改写。
- 报告定位：编码/结构提示指向 `audit_text/` 派生正文，PDF 页码为原文页码；文本覆盖提示只是候选，不保证扫描页、数字及公式语义准确。
- 下一步：等待最终批次结束，执行 `python scripts/validate_markdown_audit.py --output quality_runs/audit_20261019_final`，汇总并复核候选问题，记录最终结论；不得把尚未核查的提示标为已修复。
- 回滚：目录整理清单位于 `quality_runs/audit_20261019/normalization.json`，源目录备份在该批次 `backups/`；先检查源/目标是否占用，再按清单回移。代码回滚用 `git revert <本次提交哈希>`。

## 2026-10-19 — 登记服务器

- 完成：新增 `SERVER.md`，记录用户提供的 SSH 主机、端口、用户名；不记录密码。
- 当前设备：`Pugss`，Windows / PowerShell，路径 `F:\kag`，Python 3.8，Git 2.45.1.windows.1。
- 服务器：`connect.weste.seetacloud.com:43496`，用户 `root`。未登录；操作系统、运行环境、项目及数据路径仍待核实。
- 验证：`python -m unittest discover -s tests -v` 四项测试通过，`python scripts/check_repository.py` 和 `git diff --cached --check` 均通过；SSH 连接及远程运行环境不在本次验证范围。
- 下一步：实际开展服务器工作时核实并更新 `SERVER.md`；约 22 GB 数据留在本地或服务器。
- 同步：上次提交 `f88f99d` 已推送并核对；本次提交后推送到 `origin/main` 并核对哈希。

## 2026-10-19 — 允许上传模型

- 完成：允许模型及权重上传；单文件严格小于 100,000,000 字节；约 22 GB 业务数据继续保留在本地或服务器。
- 当前设备：`Pugss`，Windows / PowerShell，路径 `F:\kag`，Python 3.8，Git 2.45.1.windows.1。
- 服务器信息：尚未提供，仍待补充；本次没有业务数据或模型实体文件需要传输。
- 验证：`python -m unittest discover -s tests -v` 四项测试全部通过；`python scripts/check_repository.py` 和 `git diff --cached --check` 通过。`git check-ignore` 确认数据路径被忽略，模型路径未被忽略（模型查询返回 1 为预期结果）。
- 下一步：业务数据放入 `data/` 或 `datasets/`，模型可放入 `models/` 或 `checkpoints/`；人工确认模型文件不含业务数据。补充服务器环境后继续工程开发。
- 同步：目标 `origin/main`，提交后推送并核对远程哈希；上次提交 `62f4920` 已确认上传成功。

## 2026-10-19 — 仓库协作规范

- 目标：`https://github.com/hanjiang6-nb/coal-kag.git`，分支 `main`。
- 完成：建立代理协作规范、变更记录、交接记录及仓库上传检查。
- 当前设备：`Pugss`，Windows，PowerShell，项目路径 `F:\kag`。
- 已知运行时：Python 3.8，Git 2.45.1.windows.1。
- 服务器：尚未提供服务器信息；主机别名、操作系统、运行时版本、部署路径和启动方式均待补充。
- 项目状态：尚无业务代码、业务测试或部署配置。
- 验证：`python -m unittest discover -s tests -v` 三项测试通过；`python scripts/check_repository.py` 通过；`git diff --cached --check` 通过。测试覆盖大小上限、禁止文件及允许文件；尚无业务功能可测试。首次测试遇到本机同名包导入冲突，已通过添加 `scripts/__init__.py` 修复并重跑通过。
- 下一步：同步仓库后添加业务代码和相关测试，补充实际服务器信息；每次提交更新此文件与更改记录。
- 上传状态：提交后执行推送，并核对远程分支提交哈希。
