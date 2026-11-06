# LlamaIndex、Neo4j 与 Qdrant 图谱接入

## 目标与边界

沿用已确定的 FastAPI + LlamaIndex PropertyGraph + Neo4j + Qdrant 方案。Java 是认证、租户和活动文档来源的权威门面；Python 负责有证据的实体关系抽取与派生图索引。Qdrant 保留原有切片、dense/sparse schema、embedding 模型与维度。前端只访问 Java，不持有内部服务或模型凭据。美联与 Yudao 不在本轮范围。

候选 LightRAG 属于另一套检索编排，不叠加；旧 MySQL 结构投影与迁移开关已退役。Neo4j Community 使用既有 `neo4j` 数据库，以租户、知识库及来源身份隔离，不为每个租户创建数据库。

## 构建与发布

图谱抽取是异步派生任务，上传向量成功不等于图谱完成；图谱失败不撤销已经验证的活动向量。任务状态为 QUEUED、RUNNING、SUCCEEDED、FAILED、CANCELLED。状态存入 Neo4j，重启中断任务须明确失败，支持重试；禁止把任务接收当完成。

Java 生成最多 10000 条活动切片身份快照，Python 仅按快照读取 Qdrant，并逐项复核 tenant、KB、documentId、version、revision、chunkId 和 contentSha256。来源内容不由浏览器传入图引擎。全量范围超限整次拒绝，不得截断后清理。

Neo4j路径的Java快照使用Qdrant payload selector，仅读取tenant/KB/ACTIVE及七项来源身份需要的9个字段；点ID仍取顶层id。完整count/scroll/count、重复检查与查询前后身份比较保留。旧MySQL投影已删除，Java不再为图谱下载全文。Python仍自行回读并校验原文，不以Java提供的哈希代替正文指纹核对。

图模型使用显式固定的千问快照和Pydantic生成的严格JSON Schema：响应仅含 `triples`，每项仅含必填 `subjectQuote/predicate/objectQuote` 字符串，长度1至160、关系上限与配置相同。两个Quote字段要求单个连续原文引用，不生成组装后的概念别名。对象形状完整验证后，字段原值一一转入官方Simple提取器，不做类型转换、筛项、裁长或自由格式降级。完整字段/原文匹配和后续作用域防线保留。

原文明示的动作、要求和状态可作为连续引用的对象节点；谓词只承担短关系标签，不把整个动作放进谓词并输出空对象。否定、条件和程度必须保留，不补造原文未给出的对象。

单片累计最多三个模型请求。原文错误可可靠建立局部计划时，冻结完整三元组，只为非法subject/object槽提供当前来源同支持子句的连续引用候选；冻结peer与颜色、范围、限定语保持。动态严格schema绑定当前节点的repairKey、全部槽与候选，局部LLM副本避免并发串用计划。局部修复不能改其他字段、谓词、数量或顺序。该计划不是通用纠错器：计划不适用或局部响应失败时，在剩余预算内根据完整原文及受控反馈独立重抽完整候选，不把旧错误关系冻结为事实。每个候选执行相同官方提取及全部守卫；不得逐项丢弃坏关系，先前声称非空后返回空数组仍明确失败。请求耗尽后不发布新图。worker和parent只允许白名单错误附带不可读的来源身份hash，原文/候选/模型输出不写日志或公开任务协议。结构正确、名称归一后出现在原文与关系语义已人工确认是不同证据。

每个任务有独立 staging generation；LlamaIndex 提取器之后强制作用域变换，实体出现的存储 ID 包含 tenant、KB、generation 和 pointId。同名实体按授权范围内 canonical name 聚合展示，边按主体、谓词、客体聚合并保留各自来源。禁止默认全局 name MERGE，禁止任意模型 Cypher。每条关系附有来自原切片的证据；模型原文、额外属性及控制字段不得未经校验入库。

模型请求的短暂传输故障不能被误当作源码异常或实体错误。Qwen适配边界仅把已知传输/上游状态转为固定模型错误类型；超时、明确临时网络、429与500/502/503/504可重发，网络、slot和完整重抽合计最多三个请求。传输重试保留当前官方extractor、完整prompt和局部schema，不修改语义反馈；取消、父deadline、鉴权、证书、URL/协议、非法或超大响应不重试。worker与parent只接受类型和固定码/reason/hash配对，不根据任意异常文本生成诊断，也不返回上游正文或地址。

构建使用一个可终止子进程，默认单并发、有界待处理队列、独立总预算；不占用现有三秒检索预算。先完整写入、来源读回核对，再原子激活。发布比较文档 revision，并受删除 tombstone、任务 deadline 和活动来源重检约束；迟到旧任务不可覆盖新版本或复活删除文档。失败 staging 不可查询，可安全清理。

## 内部契约（统一 camelCase）

所有 `/v1/graphs/**` 接口要求内部 Service Bearer 和可信 `X-Tenant-Id`；租户不在 JSON 中接收。图请求体上限独立于小型检索请求，最大 8 MiB。

`ActiveGraphSource` 必填：`pointId, documentId, documentCode, documentVersion(int), documentRevision(long), chunkId, contentSha256`。

- `POST /v1/graphs/builds`：`{knowledgeBase, activeSources: ActiveGraphSource[], reconcile: boolean, restoreFence?: GraphRemovalFence}`。`reconcile=true` 是完整 KB 重建；false 只替换快照涉及的文档。返回 `GraphBuild`。`restoreFence` 仅由 Java 向量删除失败的补偿链路使用，不接受浏览器传入。
- `POST /v1/graphs/queries`：`{knowledgeBase, activeSources, searchText?: string, nodeType?: string, viewMode: OVERVIEW|DETAIL, limit: int}`。返回既有 Graph VO，加 `backend: "neo4j"` 和可空 `build: GraphBuild`。
- `POST /v1/graphs/deactivations`：`{documentId,documentRevision}`。以 tenant+documentId 建立删除屏障，退役其来源；返回 `{deactivated:true,barrierEpoch,documentRevision}`。返回票据仅供内部补偿，不出公开 VO。
- `GET /v1/graphs/builds/{buildId}?knowledgeBase=...`：返回所属 tenant+KB 的 `GraphBuild`；不得按 buildId 全局读取。

`GraphBuild`：`buildId,knowledgeBase,status,documentCount,processedDocumentCount,nodeCount,edgeCount,errorCode?(脱敏),createdAt?,updatedAt?`。计数只能来自实际处理结果。

`GraphRemovalFence`：`documentId,barrierEpoch,documentRevision`。屏障记录被删除来源的最大 revision；普通同 revision 构建不能在向量删除窗口清除屏障，更高 revision 的重新摄取可新生。恢复同 revision 必须具备当前 epoch/revision 精确匹配的内部票据，并重新验证活动来源；旧补偿不能撤销后一次删除。

任务使用持久提交序号；全库对账只退役在自身快照时刻已经存在的旧来源，不取消后来排队的新文档任务。切片必须显式设置 LlamaIndex `NodeRelationship.SOURCE`，避免官方序列化将 `document_id` 改成无来源值。持久化读回必须包含来源、文档和版本身份，清理后仍可追溯实体与关系。

公开 `POST /api/rag/graphs/sync` 保留路径；启用新后端时返回原四个计数字段及可选 `buildId/status`，前端按任务状态解释。新增公开 `GET /api/rag/graphs/builds/{id}?knowledgeBase=...`，沿用 graph manage/view 权限。

公开 GraphNode 增加 `ENTITY`，关系增加 `RELATED_TO`，保留原类型。节点/边 `properties.sources[]` 最多返回 8 条，另给 `sourceCount/sourceTruncated`。每条来源含 ActiveGraphSource 全部身份字段，并可带 `sourceName,sourceType,sourceLocator,chunkType,sectionPath,sourceMetadata,evidence`；Java 按当前活动快照逐项复核，前端只显示白名单字段。共享实体不伪装为只有一个来源。查询有节点、关系、来源和响应体上限，悬空端点不可返回。

检索继续从 Qdrant 返回原文证据。图增强只允许固定、有界、带作用域的关联扩展，并二次回读 ACTIVE Qdrant 原文；继续经过原相关性重排。图中模型结论不能直接替代原文。

## 配置与交付

Java 固定使用 Engine/Neo4j，已移除旧后端迁移开关。Python 新增私有 Neo4j 连接、graph model、并发、队列和超时参数，真实值仅在 Git 外与个人 github-dev。官方 `llama-index-graph-stores-neo4j==0.8.0` 与现有 core 0.14.25 配套；使用现有 bounded HTTP 的 LlamaIndex LLM adapter 或具备相同边界的官方适配，禁止悄悄使用默认 OpenAI 凭据/embedding。

验收分开记录：本地测试、真实 Neo4j/APOC、真实 Qwen 抽取、Java 网关与浏览器、多租户/KB/删除/换版反例、Git/CI、镜像部署。未经实际验证不得宣称后一阶段完成。回滚恢复匹配制品和配置；本版不支持旧开关回滚。旧表清理候选只作审查，不自动执行，不删除 Qdrant collection。
