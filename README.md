# KAG

KAG 工程化项目。

## Git 使用

提交项目文件：

```powershell
git add .
git commit -m "Add project files"
```

在代码托管平台创建空仓库后，将下方地址替换为实际仓库地址并推送：

```powershell
git remote add origin <仓库地址>
git push -u origin main
```

## 文件管理

环境变量和密钥不提交到仓库，可使用 `.env.example` 提供配置示例。
约 22 GB 的业务数据仅保留在本地或服务器；`data/`、`datasets/`、索引、数据库及日志等不上传。
模型及权重允许上传，`models/`、`checkpoints/` 不再忽略，但每个文件必须严格小于 100,000,000 字节。
提交前执行 `python -m unittest discover -s tests -v`，暂存后执行 `python scripts/check_repository.py`。模型扩展名也可能用于数据文件，仍需人工确认内容，禁止将业务数据放入模型目录上传。
