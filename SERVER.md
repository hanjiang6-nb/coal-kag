# 服务器信息

以下连接信息由用户于 2026-10-19 提供，用于多设备交接。

| 项目 | 信息 |
| --- | --- |
| 主机 | `connect.weste.seetacloud.com` |
| SSH 端口 | `43496` |
| 登录用户 | `root` |
| 操作系统 | 已核实：Ubuntu 22.04.5 LTS，x86_64 |
| Python / CUDA / GPU 环境 | `/root/miniconda3/bin/python`，Python 3.12；本次登录未分配 GPU，CPU 配额 0.5 核，内存上限 2 GiB；CUDA 版本未核实 |
| 项目路径 | 已核实：`/root/autodl-tmp/rag` |
| 数据路径（约 22 GB） | PDF：`/root/autodl-tmp/rag/pdf/`；转换结果：`/root/autodl-tmp/rag/markdown/`；数据不上传 GitHub |
| 模型路径 | 待核实；允许上传模型，单文件须小于 100 MB |
| 启动方式 | 原转换入口为 `start_resume8.sh` / `convert_missing.py`；本次只运行质量检查，见 `MARKDOWN_QUALITY.md` |

```powershell
ssh -p 43496 root@connect.weste.seetacloud.com
```

密码不保存在仓库中，登录时通过 SSH 提示输入或使用另行配置的 SSH 密钥。
2026-10-19 已通过 SSH 登录核实以上已知信息。资源配置是本次检查快照，切换服务器或工作模式后需重新核实。

代码仓库：`https://github.com/hanjiang6-nb/coal-kag.git`。
切换设备前阅读 `HANDOFF.md`，检查工作区并同步代码；不要覆盖服务器上已有的数据或配置。
