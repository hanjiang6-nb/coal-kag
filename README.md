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
虚拟环境、缓存、日志以及根目录下的 `data/`、`models/`、`checkpoints/`、`indexes/` 已默认忽略；需要版本管理时请调整 `.gitignore`。
