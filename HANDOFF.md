# 交接记录

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
