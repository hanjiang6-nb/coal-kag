# 交接记录

## 2026-10-27 — 知识图谱到可复用本体候选

- 用户确认大方向为从知识图谱构建本体，本轮实现候选学习层：允许重叠、保留多层，先给 C 编号；具体命名、正式本体语义审核及领域约束后置。
- 开始前检查 main/工作区并成功 `git fetch origin`，读取 `git show origin/main:HANDOFF.md`；远程基线 51430bd。原有 MARKDOWN_QUALITY.md 修改及 scripts/report_markdown_audit.py 保持不变。
- 当前设备 PUGSS，Windows 10.0.26200 / PowerShell / Python 3.8.8 / Node.js v16.16.0，路径 `F:\kag`；本次未操作服务器，实际服务器信息仍见 SERVER.md。
- 上轮未提交的编号簇代码、模板、测试和共享模块工作副本及 patch 已隔离到 `artifacts/abandoned_numbered_clusters_20261027/`，不提交。共享模块先恢复到 HEAD，再仅提取 prepare_kind_profiles，保留旧入口行为；旧测试全部通过。
- 新入口 `scripts/learn_ontology_candidates.py`；新视图模板 `scripts/ontology_learning_report.html`。只按可靠正属性匹配，不把缺失当否定；物种参考不充当样品实测，同名提及不充当不同品种或同一现实实体。
- 同时保留单属性 facet 与闭合交集候选；不再只存最小外延。主组成元素来自完整化学式解析，未知语法不猜。定义只用规范化条件；候选 ID 不含成员列表，C 编号由一致的注册表链管理，冲突拒绝。
- 正式运行：`python scripts/learn_ontology_candidates.py --source artifacts/domain_features_20261027_release --output artifacts/ontology_learning_20261027_release`；相同命令加 `--validate-only` 全量验证通过。
- 首轮结果：537 个候选项，9 项有正属性；33 个候选覆盖 7 个种类，7 个均多重归属；37 条严格定义蕴含、29 条观测包含线索、24 对观测同外延。原始语义关系 174，领域投影 0；530 项未覆盖。33 个候选不是 33 个已验收本体。
- C13 为主组成 Fe 条件，成员菱铁矿/黄铁矿/白铁矿；C12 为 FeS2 条件，成员黄铁矿/白铁矿；C5 为碳酸盐条件，成员菱铁矿/白云石/方解石。候选可共享成员，上位候选也保留。
- 复用运行：`python scripts/learn_ontology_candidates.py --source artifacts/domain_features_20261027_release --registry artifacts/ontology_learning_20261027_release/registry.json --output artifacts/ontology_learning_reuse_replay_20261027_release`；同命令加 `--validate-only` 通过。注册表 ID、编号和定义精确保持，33 复用、0 新增。registry 是生成数据，不上传 GitHub；新设备需同步注册表基线及本地业务数据。
- `python -m unittest discover -s tests -v`：47 项通过，新增 9 项覆盖重叠/上位候选、单关键条件、样本包含非定义关系、同外延非等价、增加成员后复用身份、方向/证据去重、完整输出篡改、安全嵌入、化学语法及大小写/单位/值类型区分。
- 真实 Chrome 检查：`python artifacts/ontology_learning_20261027_release/check_learning_browser.py`，33 唯一节点、37 定义线、29 观测线开关、成员多归属、编号/组成/未覆盖搜索、零领域边不造线及 SVG 导出全部通过；脚本和结果留忽略目录。已实际渲染并查看图示，正式入口 `知识图谱到本体候选.html`，SVG 为 `可复用本体候选.svg`。
- 已校验导出图所有原始及新增节点/边，并补齐观测同外延、定义等价的明确类型边。最早 verified 目录保留作历史试跑，正式输出以 release 为准。
- 提交前执行 `python scripts/check_repository.py`、暂存区凭据检查和 `git diff --cached --check`。只上传代码、模板、测试和文档；不上传图谱、注册表、HTML 或其他业务生成物。
- 下一步补充属性覆盖并审核候选定义、关系和领域约束，再形成正式本体。回滚代码使用 `git revert <本次提交>`，新产物在独立目录，原 data/历史图谱不改写。提交后推送 GitHub，失败保留本地提交并重试 `git push origin main`。

## 2026-10-27 — 最小属性本体，随后找本体之间的关系

- 用户已先确认例子并授权试跑：不同品种按固有属性抽象为最小本体；关系阶段只找本体之间的关系，不产生新本体。旧方案及旧 15 本体产物原样保留，不作为本轮输入。
- 开始前检查 main 与工作区，成功 `git fetch origin` 并读取 `git show origin/main:HANDOFF.md`；远程基线 93ac790，本地此前 4 个提交待上传。原有 MARKDOWN_QUALITY.md 和未跟踪 scripts/report_markdown_audit.py 不修改或提交。
- 当前设备 PUGSS，Windows 10.0.26200 / PowerShell / Python 3.8.8 / Node.js v16.16.0，项目 `F:\kag`；本次未操作服务器。服务器实际环境、项目路径和连接方式见 SERVER.md。
- 新入口 `scripts/cluster_minimal_ontologies.py`，中文视图模板 `scripts/minimal_ontology_report.html`；具体核心词汇、组成规则与“最小”定义见 DOMAIN_FEATURES.md。组成识别是明确的首轮受控规则，尚不覆盖所有物质。
- 正式运行：`python scripts/cluster_minimal_ontologies.py --source artifacts/domain_features_20261027_release --output artifacts/minimal_ontologies_20261027_verified`。
- 全量验证：同一命令加 `--validate-only`，通过。输出图完整节点和边均精确校验，发现并修复了最初只检查原始图节点/边的缺口，新增独立篡改检查。
- 结果：537 个种类候选项、8 个属性可用；2 个候选本体，5 个矿物种类，52 次默认可用文献提及。二硫化铁矿物含黄铁矿/白铁矿；六方晶族碳酸盐矿物含方解石/菱铁矿/白云石。
- 本体间关系为 0：174 原始已断言关系没有双端同时已归类的证据，全部存入 residual_relations.jsonl，不用技术边或共同邻居补造关系。532 项未归类，岩石品种尚缺可用属性。
- 冻结复跑：`python scripts/cluster_minimal_ontologies.py --source artifacts/minimal_ontologies_20261027_verified --output artifacts/minimal_ontologies_freeze_replay_20261027`，随后相同命令加 `--validate-only`；均通过，原 2 本体节点保持不变，新增 0。
- 浏览器：`python artifacts/minimal_ontologies_20261027_verified/check_minimal_browser.py`，真实 Chrome 检查 2 本体/5 种类、零关系不造线、中英文查未归类、定义详情与 SVG 导出通过；该本机脚本和 browser_validation.json 留在忽略目录。已渲染并查看 preview.png。
- 首轮早期输出 `artifacts/minimal_ontologies_20261027_trial/` 为历史试跑，正式结果请使用 verified 目录。`属性本体与本体间关系.html` 和 SVG、原始数据及所有生成物均不上传 GitHub。
- `python -m unittest discover -s tests -v`：38 项全部通过，含新增 7 项多品种/核心维度/方向权重/多类归属/完整导出图篡改拒绝/安全嵌入测试。另核对输入 SHA-256 未变，冻结复跑的两个本体完整 payload 精确相等。
- 提交前运行 `python scripts/check_repository.py`、暂存区凭据检查和 `git diff --cached --check`。
- 下一步优先补充不同岩石品种的组成/结构等有来源属性，以及已归类种类之间的真实关系；本轮是候选发现和工程验证，未声称完成正式本体语义验收。
- 回滚代码 `git revert <本次提交>`；输出均在新目录，原 data、特征图和旧聚类不改写。提交后推送 GitHub；失败保留本地提交，待重试 `git push origin main`。

## 2026-10-27 — 聚类层次图

- 开始前检查 main、工作区及远程，成功执行 `git fetch origin` 并读取 `git show origin/main:HANDOFF.md`；远程基线 93ac790，本地已有 3 个待上传提交。
- 当前设备 PUGSS，Windows 10.0.26200 / PowerShell，Python 3.8.8、Node.js v16.16.0，路径 `F:\kag`。本次未操作服务器，已知服务器环境和路径见 SERVER.md。
- 新增 `scripts/visualize_ontology_hierarchy.py` 和原生离线 SVG 模板 `scripts/ontology_hierarchy.html`。树展示生成阶段与成员归属，虚线展示支撑关系；没有生成或改写 is-a 分类、合并距离或冻结本体。
- 实际生成命令：`python scripts/visualize_ontology_hierarchy.py --source artifacts/two_stage_ontologies_20261027_verified --output artifacts/two_stage_ontologies_20261027_verified/聚类层次图.html`。
- 结果为 7 个属性本体、8 个关系本体、100 个成员和 780 个未聚类对象。关系第 1/2 轮生成本体，第 3 轮停止；成员和未聚类分支可展开，支持中文/原文/编号搜索、缩放、详情和 SVG 导出。
- 16 条本体支撑边仅累计目标本体出生轮的原始连接；第 1/2 轮权重 25/13 为进入新本体的连接子集，不能当作整轮所有边界连线数量。原有本体、成员、原始图谱与旧页面保持不变。
- `python -m unittest discover -s tests -v`：31 项通过。新增 4 项覆盖完整唯一归属、已有本体保留、出生轮累计、停止轮、异常拒绝、安全嵌入及原始文件哈希保持。
- `python scripts/validate_two_stage_ontologies.py --source artifacts/domain_features_20261027_release --output artifacts/two_stage_ontologies_20261027_verified`：全量验证通过；此项不代表专业概念的语义验收。
- 实际浏览器检查：`python artifacts/two_stage_ontologies_20261027_verified/check_hierarchy_browser.py`，通过默认 15 本体可见、100 成员展开/收起、中英文搜索、16 支撑边、详情及导出按钮检查。该检查脚本和结果仅保留本机忽略目录。
- Chrome headless 已渲染并查看 `hierarchy_preview.png` 和 `hierarchy_diagram.png`；同目录保存 `聚类层次图.svg`。切换设备可用上述生成命令复现 HTML，SVG 通过页面“导出图”获取。
- 提交前执行 `python scripts/check_repository.py`、暂存区凭据检查和 `git diff --cached --check`。只提交代码、模板、测试和交接；原有 MARKDOWN_QUALITY.md 修改和 scripts/report_markdown_audit.py 保持原样。
- 下一步：使用层次图审核候选的概念定义及关系角色。数据和图形产物不通过 GitHub 传输；新设备需具备原始聚类结果或先运行聚类构建程序。提交后推送 GitHub，若网络失败保留本地提交并重试 `git push origin main`。
- 回滚代码：`git revert <本次提交>`；删除本次新增可视化产物即可移除图示，不影响业务数据及既有聚类。

## 2026-10-27 — 当前两阶段本体页面中文化

- 开始前检查分支和工作区，成功执行 `git fetch origin` 并读取 `git show origin/main:HANDOFF.md`；远程基线为 93ac790，本地此前有 f0b6663、e4aa229 两个待上传提交。
- 当前设备 Pugss，Windows / PowerShell，Python 3.8.8，Node.js；项目路径 `F:\kag`。本次未连接或修改服务器，已有服务器信息仍见 SERVER.md。
- 新增 `graph_zh_glossary.py`、`graph_zh_geology.py` 可复用译名及术语字典，`ontology_report_zh.py` 显示元数据和 `two_stage_report.html` 模板。未知专名保留原拼写，不推断实体身份。
- 默认中文详情为成员和属性编号补充名称，原始数据可切换查看；搜索同时支持中文、原名及编号。公式保持大小写，原文证据不翻译，不修改原始事实或聚类依据。
- 本地页面：`artifacts/two_stage_ontologies_20261027_verified/两阶段本体聚类.html`。15 个本体及其全部 100 个成员均有中文名称或中文通名部分；Soutpansberg 等不确定专名保留。
- 执行 `python -` 调用 `report()` 刷新当前 HTML，并逐文件核对 SHA-256：13 个 JSONL、SQLite 和 summary 文件均保持不变。翻译前 HTML 备份为同目录 `report_before_zh.html`。
- 验证命令 `python -m unittest discover -s tests -v`：27 项通过，含中文/英文搜索、中文与原始详情切换、证据安全嵌入、公式及原始成员/权重保持。
- 验证命令 `python scripts/validate_two_stage_ontologies.py --source artifacts/domain_features_20261027_release --output artifacts/two_stage_ontologies_20261027_verified`：通过，核对 880 对象、15 个本体、174 原始关系和 85 条跨轮边界记录。
- 已执行 Chrome headless 渲染，截图 `preview_zh.png` 并查看；此检查不代表对所有专业译名或本体语义进行专家验收。
- 提交前执行 `python scripts/check_repository.py`、`git diff --cached --check` 及暂存区凭据检查。只提交源代码、模板、测试和交接，不提交数据及可视化产物；原有 MARKDOWN_QUALITY.md 修改和 scripts/report_markdown_audit.py 保持原样。
- 下一设备先检查工作区并 `git fetch origin`，可快进时 `git pull --ff-only`。生成图谱未通过 GitHub 分发，需保留本机 artifacts 或在具备数据的设备重新运行构建入口；无需显卡完成中文显示。
- 回滚代码：`git revert <本次提交>`；仅恢复旧显示时可把本地 `report_before_zh.html` 复制回原页面。已有本体、成员归属和业务数据均未改写。

## 2026-10-27 — 天然属性生成本体，再按累计关系生成新本体

- 用户最终澄清：两阶段都生成“本体”，此前“实体”为笔误。第一阶段使用天然/客观物性；关系阶段按已聚类本体成员到未聚类节点的原始连线总数计算权重；已有本体不可再次聚类。
- 开始前检查了本地与缓存 GitHub 交接。fetch 发生连接重置，未确认远程最新状态；上次 f0b6663 尚待上传。本机 Pugss / Windows / PowerShell / Python 3.8 / Node.js，路径 F:\kag；未修改服务器。
- 新入口 `scripts/cluster_two_stage_ontologies.py`，独立验证 `scripts/validate_two_stage_ontologies.py`；旧路由实现保留为历史方案，不再作为当前流程。
- 天然属性仅含组成/结构/形貌/硬度等物性，剔除来源、用途、审核和成因标签。矿物类别参考只用于类别相似度并保留推导记录，不修改样品原观测。
- 第一阶段完整连接 Jaccard 默认 0.45；第二阶段存储原始连线整数权重，再以计数向量的加权 Jaccard 默认 0.5 比较剩余节点。两者均为待调优的工程参数。
- 每轮形成的新本体立即冻结，其成员退出候选池；下一轮重新从原始语义关系累计权重。固定本体只作为锚点，绝不作为再次合并的成员。
- 主结果：`artifacts/two_stage_ontologies_20261027_verified/两阶段本体聚类.html`，880 对象；属性阶段 7 个本体/76 成员；关系阶段 3 轮含停止轮，生成 8 个本体/24 成员；780 个未聚类。首轮权重 41、末轮 19，不能把跨轮数值相加当作去重关系总数。
- 输入没有显式标记的既有本体。完整输出再次作为输入运行在 `artifacts/two_stage_freeze_replay_20261027_verified/`，验证 15 个既有本体原 ID、成员及节点内容全部保持不变，新增本体数为 0。
- 验证：26 项单元测试通过，含整数边权、逐轮新增锚点、冻结复跑、重复成员拒绝、非物性标签排除、权重篡改检测及 Node 交互。全量独立验证和完整冻结复跑均通过；Chrome 已渲染并检查权重图。
- 输出图谱相同 ID 的相同技术边已去重，冲突边拒绝；原始已断言语义关系仍为 174 条，不以技术边数量充当关系权重。独立验证发现后完成修复并重跑通过。
- 下一步：核实缺失特征和关系，审核候选本体含义与阈值；当前并非全体节点聚类完成或正式本体专家验收。
- 回滚：代码 `git revert <本次提交>`；输出均为新目录，原始 data 和旧图谱未改写。代码/测试/交接可上传，数据及生成图谱不上传；原有 MARKDOWN_QUALITY.md 与未跟踪报告脚本保留原样。

## 2026-10-27 — 按属性、关系与形成方式分流聚类

- 用户已确认：自然物质属性为主、人工对象关系为主、加工天然物采用混合；空间/时间/地层/过程等走结构关系。
- 开始前已 fetch 并读取最新 GitHub 交接和 AGENTS.md。本机 Pugss / Windows / PowerShell / Python 3.8 / Node.js，路径 F:\kag；未连接或修改服务器。
- 新增聚类及独立验证脚本，使用已有领域特征图谱，未新增网络事实或改写原数据。路由属于可追溯的规则候选，未把名称线索认证为真实形成史。
- 默认权重：属性主导 0.8/0.2，关系主导 0.2/0.8，混合 0.5/0.5，结构关系 0/1；主视图至少两项特征，完整连接阈值 0.45。工程参数尚未专家调优。
- 覆盖 880 对象（873 实体提及＋7 参考概念）：524 属性、288 结构关系、11 混合、3 人工关系候选、54 形成方式待确认。25 个达到门槛，生成 2 个多成员候选簇与 21 单例；855 个保持待补充。
- 输出：`artifacts/ontology_routing_20261027_release/` 的 `聚类策略与结果.html`、`属性关系聚类图谱.html`、SQLite、实体策略、候选簇、待补充列表和 validation。初步输出 `artifacts/ontology_routing_20261027/` 仅为历史试运行。
- 验证：20 项单元测试通过，覆盖自然/人工权重切换、混合缺失、同段线索隔离、关系方向、不可信关系排除、完整连接、输入保留及篡改检测；完整结果独立验证通过（880 对象、23 个簇/单例、855 待补充）。Chrome headless 已渲染并查看策略页截图。
- 下一步：优先补充人工材料的原料、工艺、用途等经核实关系，以及自然材料缺少的领域属性；复核形成方式候选并调优参数。缺失主视图不退化成仅按类型硬分组，未产生正式本体。
- 所有数据和可视化仅保留在忽略的 artifacts 内；只提交代码、测试与说明。先前 MARKDOWN_QUALITY.md 的修改和未跟踪报告脚本保持原样。
- 回滚：代码用 `git revert <本次提交>`；输出为新目录，旧特征图谱和 data 未改写。

## 2026-10-27 — 领域特征、全实体查询与复用

- 用户澄清：需要形貌、表面、组成等领域特征，用于聚类和本体候选；允许联网补充，并要求所有实体都检索、特征可复用。此前误解成来源/审核属性的改动已撤回，未纳入提交；生成的试验目录留在忽略目录中。
- 开始前已 fetch 并读取 GitHub 最新交接；本机 Pugss / Windows / PowerShell / Python 3.8 / Node.js，路径 F:\kag。未操作服务器。
- 已枚举 873 次实体提及、537 个名称/类型查询项，实际通过联网工具执行全部 537 个查询，记录 135 个批次。候选链接是批次级来源，不等于每条属性或身份已核实。
- 已核实外部资料覆盖 7 种矿物，包含高岭石带尺度/晶面/样品条件的表面特征。一般物种参考与具体样品观测分开，不把矿物通性直接灌入样品。
- 复用模型：特征定义 → 特征值，实体 → 属性陈述 → 共享值；陈述保留来源和条件。115 定义、213 共享值、245 陈述；完整连接 Jaccard 产生 5 个参考物种候选簇，尚非正式本体。
- 覆盖情况：134 次提及有原观测、65 次仅物种参考、674 次属性待核实（458 个名称/类型项）。10 条不明确的原观测已隔离；不能宣称完成全部属性补全。
- 交付目录：`artifacts/domain_features_20261027_release/`，包含 `领域特征图谱.html`、`全部实体检索清单.html`、SQLite、JSONL、summary 和 validation。检索及核实参考输入在 `artifacts/entity_research_20261027/`。数据均不提交 GitHub。
- 验证：15 项单元测试通过；全量 `validate_domain_features.py` 通过，包括实体覆盖、共享 ID、条件、引用及 SQLite 完整性；Chrome headless 已实际渲染并检查图谱截图。仅证明结构和实现，不认证全部语义。
- 下一步：按全实体清单继续核实来源、解决身份歧义，并扩展受控特征与参考种类；未核实属性保持缺失，不用于聚类。
- 回滚：代码 `git revert <本次提交>`；所有输出使用新目录，data 和原图谱保持不变。先前 `MARKDOWN_QUALITY.md` 与未跟踪报告脚本未修改或提交。

## 2026-10-27 — 离线图谱可视化

- 新增 `scripts/visualize_knowledge_graph.py`，生成无需联网或服务端的 Canvas 交互视图，支持搜索、层/类型/关系筛选、缩放拖动及证据查看。
- 本地输出 `F:\kag\artifacts\knowledge_graph_20261027\知识图谱.html`：873 个实体提及、493 条普通或限定关系；默认只显示 174 条银标事实。观测和统计记录仍保留在原 SQLite。
- 本机 Pugss / Windows / Python 3.8 / Node.js，未操作服务器；原有未提交文件保持原样。
- 验证：提交前运行全量单元测试（包含 Node 模拟交互与脚本安全嵌入）、仓库检查和差异检查；未完成真实浏览器视觉验收。
- 可视化含业务证据，仅保留在被忽略的 artifacts 目录；GitHub 只提交生成器、测试及交接。
- 回滚使用 `git revert <本次提交>`；原图谱和输入未改写。

## 2026-10-27 — data 标注知识图谱

- 已检查本地工作区；原有 `MARKDOWN_QUALITY.md` 修改和未跟踪报告脚本保持原样，未纳入本次提交。
- GitHub `git fetch origin` 连接 443 失败，已读取缓存 `origin/main` 的交接（98bc962），不能声称已获取最新交接。
- 输入：本机 `F:\kag\data` 的 76 普通、45 扩展、21 待核查范围，19 个文件哈希全部匹配；未调用付费模型，未启动训练或修改服务器。
- 产物：`artifacts/knowledge_graph_20261027/` 下 SQLite 知识图谱、节点/边 JSONL、审查问题及汇总。1,831 节点、2,978 边，174 条默认银标陈述；限定、转述、待核查层单独保留。
- 完整性：证据精确子串、实体类型及端点、SQLite 完整性和外键检查通过，数据审查异常为零；这不代表人工金标或语义准确率认证。
- 当前设备：Pugss，Windows / PowerShell，F:\kag，Python 3.8。每个实体作为范围内提及保存，未猜测跨文档同名实体身份。
- 提交前验证：`python -m unittest discover -s tests -v` 12 项通过，`python scripts/check_repository.py` 及 `git diff --cached --check` 均通过。
- 下一步可查询 SQLite `default_facts` 视图；跨文献实体消歧和人工验收仍未完成。数据及图谱产物被 Git 忽略。
- 回滚：代码用 `git revert <本次提交>`；输出为新目录，原始 data 未改写。

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
