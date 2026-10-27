# 可复用领域特征与本体候选

## 模型

实体提及、矿物参考概念、特征定义、特征值、属性陈述、候选聚类分别保存。特征和值的 ID 由内容确定，同一值可以被多个实体引用。陈述独立保存来源、限定词、样品、晶面和方法，不能把某个样品的观测直接变成整个物种的固定属性。

来源和审核信息仅用于追溯，不是聚类的领域特征。受控特征目前包括晶族、晶形、解理、莫氏硬度、光泽、断口、表面纹理、粒径、圆度及成因。原有其他观测维度暂保留原词，标记为待规范化，不声称已经形成完整本体。

特征值可按受控同义词映射复用。例如表面纹理中的“平滑”和“光滑”映射为同一值，但不会将板状与片状随意混同。化学式大小写严格保留，Co 与 CO 不等价。

## 全实体查询

`scripts/prepare_entity_research.py` 从三个标注文件枚举全部实体，按名称及类型建立检索单元，并保留每次提及的文献上下文。名称分组只是检索去重，不代表已经证实跨文档实体同一性。

2026-10-27 的输入有 873 次提及、537 个名称/类型项。135 个批次共执行了 537 个查询。每批记录查询词及候选链接，没有把搜索摘要自动转成事实。候选链接是同批检索结果，仍需确认与单个实体的对应关系。

“samples”“earlier ones”、局部样品编号或同名地层可能不能脱离原文唯一确定。未核实属性的项继续保留，不用通用材料属性代填。

## 外部知识

本次已核实的外部参考层覆盖高岭石、石英、黄铁矿、方解石、菱铁矿、白云石和白铁矿。采用 [MSA Handbook of Mineralogy](https://handbookofmineralogy.org/) 各物种页面及 [高岭石 AFM 研究](https://pubs.acs.org/doi/10.1021/acs.langmuir.7b03153)。物种通性与具体论文样品观测分开保存。

外部参考输入位于本地 `artifacts/entity_research_20261027/verified_mineral_references.json`，不上传 GitHub。新增参考项需包含物种名、明确的别名及属性列表，每个属性提供 `feature`、`value`、`url`、`section`，适用时还必须提供 `conditions` 和 `unit`。不得仅凭搜索标题或摘要批量标记为已核实。

手册中的晶族分类口径按来源保留，不擅自把 hexagonal 晶族和现代 trigonal 晶系混为一个字段。原始来源、值与数值区间保留，不靠光泽推断表面粗糙度。

## 构建和验证

```powershell
python -m scripts.prepare_entity_research --source data --output artifacts/<新检索批次>
python scripts/build_domain_features.py --source data --graph artifacts/knowledge_graph_20261027 --research artifacts/entity_research_20261027 --reference artifacts/entity_research_20261027/verified_mineral_references.json --output artifacts/<新特征批次>
python scripts/validate_domain_features.py --output artifacts/<新特征批次>
python -m unittest discover -s tests -v
```

准备脚本只生成清单，联网查询由实际检索工具执行；未执行的查询不能计作完成。构建只接受新输出目录，保留原图谱和所有输入。

结果包括 SQLite、节点和边、逐条属性陈述、实体覆盖清单、复核队列、输入哈希以及离线可视化。`领域特征图谱.html` 展示共享领域特征，`全部实体检索清单.html` 覆盖全部查询项，包括未补全的实体。

## 聚类的范围

当前候选聚类使用已核实参考物种的无条件特征值，采用完整连接的 Jaccard 相似度阈值 0.35，避免弱相似链式扩张。带条件观测、来源、文档编号、审核信息和未知值不参与。词表和阈值均为初步工程选择，未经过领域专家验收；聚类相似不等于同一矿物，也不自动生成正式本体公理。

本次结果有 115 个特征定义、213 个可复用值、245 条属性陈述（179 条来自现有观测，66 条来自外部资料）。134 次提及有原始观测，另有 65 次仅链接物种参考；674 次提及仍缺少已核实属性。存在 10 条无法安全绑定实体或值的原观测，已保留到复核队列。本体未完成，不得将这些统计描述为全实体属性补全成功。
