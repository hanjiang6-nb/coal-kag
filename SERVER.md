# 服务器信息

## 当前服务器：2026-10-28 已清空用户数据

| 项目 | 信息 |
| --- | --- |
| 主机 / SSH | `connect.westd.seetacloud.com`，端口 `37518`，用户 `root` |
| 实际主机名 | `autodl-container-65d14488ce-8a8de544` |
| 操作系统 | 已核实：Ubuntu 22.04.5 LTS，Linux 5.15.0-78-generic，x86_64 |
| 基础运行时 | `/root/miniconda3/bin/python`，Python 3.12.3；Conda 24.4.0；JupyterLab 4.4.5 |
| 项目路径 / 启动方式 | 当前未部署项目；原用户项目、模型、训练检查点及自建 `kgseg` 环境均按用户要求删除 |
| 数据盘 | `/root/autodl-tmp`，总计约 170 GiB；清理后仅保留平台 `.autodl`，占用约 2.7 MiB |
| 系统盘 | 总计约 30 GiB；清理后可写层占用约 62 MiB，基础镜像及服务保留 |
| 保留服务 | SSH、Supervisor、Jupyter、TensorBoard、AutoDL 面板与代理；平台配置在 `/init`，公共只读挂载 `/autodl-pub` 保留 |
| 服务启动 | 平台 Supervisor 启动基础服务，Jupyter 使用 `/init/jupyter/jupyter_config.py`，服务路径 `/jupyter/`；TensorBoard 使用空目录 `/root/tf-logs` |
| GPU / CUDA | 本轮未验证 GPU 分配和 CUDA 运行能力 |

```powershell
ssh -p 37518 root@connect.westd.seetacloud.com
```

2026-10-28 用户明确要求清空此服务器全部用户数据、保留系统；实际释放约 176.2 GiB，其中数据盘约 162.7 GiB、系统盘可写层约 13.4 GiB。新 SSH 登录、Jupyter/TensorBoard HTTP 200、基础运行时和关键文件哈希检查均通过。登录会自动生成零字节系统提示标记 `.cache/motd.legal-displayed`。

未在服务器保留数据备份；Git 回滚仅能恢复文档，不能恢复已删除的服务器数据。本地 `F:\kag` 的业务数据、代码和先前未提交改动均保留，旧本体开发继续停止。密码不记录在仓库中。

## 历史服务器：2026-10-19 检查快照

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
