# 论文正文继续预训练

本流程用真实论文正文做下一词预测训练（CPT），从现有 `Qwen3-VL-32B-Instruct` 继续适应领域文本。更新语言模型的 LoRA 参数；视觉编码器、词嵌入、输出头及基础权重冻结。不给模型构造问答、不调用聊天模板、不输入空白图片。原始论文和基础权重保持原位，生成的数据、检查点和日志不上传 GitHub。

## 环境与输入

- 当前服务器及资源见 `SERVER.md`，运行记录见 `HANDOFF.md`。
- 已验证 Python 3.12.3、PyTorch 2.8.0+cu128、CUDA 12.8；独立环境 `/root/autodl-tmp/envs/qwen-cpt-20261030` 继承服务器原 PyTorch，不重装基础 CUDA。
- 其余运行依赖见 `requirements-cpt.txt`。训练程序严格检查四个核心依赖版本及 PyTorch/CUDA，不支持无提示降级。
- 模型 `/root/autodl-tmp/models/Qwen3-VL-32B-Instruct`。
- 原文 `/root/autodl-tmp/cpt_multimodal_20261023`，正文来自 `documents/<id>/document.md`。

```bash
python scripts/prepare_cpt_corpus.py \
  --corpus-dir /root/autodl-tmp/cpt_multimodal_20261023 \
  --tokenizer-dir /root/autodl-tmp/models/Qwen3-VL-32B-Instruct \
  --output-dir /root/autodl-tmp/training_data/paper_cpt_2048_20261030_parallel \
  --block-size 2048 --workers 8 --seed 20261030
```

已有完整输出时不要再次运行此命令到同一目录。程序要求输出目录为空，并以 `PREPARATION_INCOMPLETE.json` 区分失败输出；只有最后完成输入复核后才移除标记。服务器本轮冻结脚本在 `.ops/qwen_cpt_20261030/`，不要覆盖仍在运行的脚本副本。

## 数据语义与验证范围

实际 Markdown 字节在处理前后都对照迁移 manifest 的 `output_sha256` 核验。历史 `source` 路径仅留存来源信息，不声称重新核实已经不存在的源文件。清理图片目标和控制字符，保留文字描述、标题、公式和单位；拒绝正文中的模型控制 token。以清理后全文的 SHA-256 完全去重，并按固定哈希划分论文，之后分别打包。

本轮 4755 篇均通过，没有清理后完全重复：训练 4666 篇、验证 48 篇、测试 41 篇。清理后正文 78,570,785 token，另加每篇一个 EOS；训练流 77,296,675 token、37,743 个 2048 长度块，实际因果预测目标 77,258,932 个。所有 token 流回读核验，尾部不丢弃，padding 不参与损失。串行与8进程全量运行的数组、mask、分片元数据和文档清单共10个文件 SHA-256 完全一致。

EOS 分隔连续文档，但不隔离文档间注意力。每个独立块的首 token 无前驱，不是预测目标。未执行近重复论文检测，验证/测试集不保证没有内容相近或不同版本论文；损失下降也不能直接证明知识掌握、指令遵循或下游任务提升。

## 实测配置选择

使用真实论文前向、反向和优化器更新测量有效预测 token/秒，记录峰值显存；梯度累积只决定一次参数更新的有效批量，不视作单步吞吐提升。

| 配置 | 单步批量 | 测得 token/秒 | 说明 |
| --- | ---: | ---: | --- |
| BF16 LoRA | 1 | 576.9 | 预热后2步，保留约16%显存余量 |
| BF16 LoRA | 2 | 591.6 | 速度只略增，显存余量低于10% |
| BF16 LoRA | 4 | — | 真实 CUDA 内存不足 |
| NF4 QLoRA | 1 | 542.6 | 预热后2步 |
| NF4 QLoRA | 2 | 532.5 | 未提高吞吐 |
| NF4 QLoRA | 4 | 510.9 | 未提高吞吐 |
| NF4 QLoRA | 8 | — | 真实 CUDA 内存不足，停止继续增大 |

这些是短时测量，正式运行速度以训练日志为准。BF16 探测脚本的梯度检查和优化器计时与通用基准略有不同，不视为精确硬件性能排名。选择 BF16、rank16、单步批量1；448个语言投影模块共134,217,728个可训练参数。NF4路径会经PEFT把非量化冻结参数转换为FP32，不能把整个非量化部分描述为BF16。

## 正式训练与恢复

用户选择完整一轮。正式训练从基础模型及固定种子新建适配器，不沿用基准测量或验收训练的参数。

```bash
python -u scripts/train_domain_cpt.py --mode train --precision bf16 \
  --model-dir /root/autodl-tmp/models/Qwen3-VL-32B-Instruct \
  --dataset-dir /root/autodl-tmp/training_data/paper_cpt_2048_20261030_parallel \
  --output-dir /root/autodl-tmp/training_runs/qwen_cpt_bf16_epoch1_20261031 \
  --epochs 1 --microbatch 1 --grad-accum 8 --rank 16 \
  --learning-rate 2e-5 --eval-batches 337 --eval-every 500 \
  --save-every 100 --keep-checkpoints 2 --minimum-free-disk-gib 10
```

完整一轮共4718个优化器步骤，最后一个窗口使用剩余7个块，不重复补样。基线和周期验证覆盖完整验证集337块；完成后测完整测试集289块。验证和测试为论文语言模型损失，未提供通用能力或领域问答质量验收。

运行应放在后台会话，日志保留在服务器。本轮脱离SSH会话的启动方式、PID和状态文件见 `HANDOFF.md`。TensorBoard事件写入 `/root/tf-logs`，逐步原始指标及审计文件写入输出目录。查看 `train_steps.jsonl`、`LATEST_CHECKPOINT.json` 和 `train_report.json`；最后一项只有全轮结束后生成。

中断后从 `LATEST_CHECKPOINT.json` 指定的完整检查点恢复，保留同样的训练配置，增加 `--resume-from-checkpoint <完整检查点路径>`。如目标输出已存在冲突文件，改用一个新的输出目录。程序核验数据签名、基础模型配置、适配器哈希和训练配置，恢复优化器、调度器、随机状态、epoch和批次位置；基准检查点禁止用于正式续训。

恢复开始还会逐张量核对适配器、优化器及参数顺序，并核对调度器、Python/NumPy/CPU/CUDA随机状态，生成零容差的 `resume_state_audit.json`。数值计算策略会记录指纹并要求恢复一致；缺少该记录的早期测试检查点不能直接用新版续训。

默认fast模式采用高吞吐GPU内核，不承诺后续反向计算逐位可重复。本轮真实对照发现：同一个检查点两次fast续训也出现微小参数差异；严格确定性计算后，完整训练与断点续训的最终适配器SHA完全相同。确定性模式用 `--deterministic` 开启，训练、续训和重载必须一致携带；短时实测约542～548 token/秒，较fast慢约6%～8%。正式训练按用户的吞吐优先要求使用fast，不把数值微差当作恢复状态验证通过的替代条件。

```bash
python scripts/train_domain_cpt.py --mode reload-check --precision bf16 \
  --model-dir /root/autodl-tmp/models/Qwen3-VL-32B-Instruct \
  --dataset-dir /root/autodl-tmp/training_data/paper_cpt_2048_20261030_parallel \
  --output-dir /root/autodl-tmp/training_runs/qwen_cpt_bf16_epoch1_20261031
```

重载检查在独立进程重新加载基础模型与适配器，在同一真实验证块上比对损失和选定logits；这证明保存/加载一致性，不代表生成质量。

## 磁盘上限与回滚

只保存适配器、tokenizer和优化器/调度器/RNG等续训状态，不重复保存66.7GB基础模型。每次保存先估算当前张量和元数据字节，并要求写完后仍有10GiB余量；不足时中止该次训练并保留已有检查点。这个检查不能阻止其他进程同时耗尽磁盘。

检查点先写入唯一的未完成目录，全部写入后原子改名。只清理当前run_id下最近两份以外的完整周期检查点；不清理其他运行、符号链接或部分写入的目录。正式运行正常保留两份周期检查点和一份最终结果，保存期间短暂需要第三份空间。验收/基准输出及意外中断的partial另占空间，应单独核对，不能把它们算成自动回收。

代码回滚使用 `git revert <提交>`。训练回退加载已保存的完整检查点；Git不保存业务语料、训练数组或适配器产物，无法恢复已清理的旧检查点。不要把32B原始权重或超过100MB的适配器直接加入Git。
