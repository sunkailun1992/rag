# Java 图谱门面与异步任务

设计契约见 [neo4j-graph-design.md](neo4j-graph-design.md)。`rag.engine.graph-enabled` 缺省 `false`，关闭时沿用 MySQL 结构投影，旧 Controller 路径、节点类型和 VO 构造调用保持兼容。

## 接口与状态

- `POST /api/rag/graphs/sync?knowledgeBase=...` 继续要求 `rag:graph:manage`。新后端返回原四个计数字段及可空 `buildId/status`；`QUEUED` 或 `RUNNING` 只代表派生任务尚未完成。
- `GET /api/rag/graphs/builds/{id}?knowledgeBase=...` 要求 `rag:graph:view` 或 `rag:graph:manage`；必须同时提供知识库，内部状态查询携带可信租户头。
- `GET /api/rag/graphs` 继续要求 `rag:graph:view`。新后端返回 `backend=neo4j` 和可空 `build`。节点新增 `ENTITY`，关系新增 `RELATED_TO`，旧类型保留。
- 上传结果保留原 `document/idempotent/validation`，追加可空 `graphBuild` 和 `graphStatus`。异步图服务拒收、不可用或协议校验失败时，已验证并激活的向量保留，响应 `graphStatus=FAILED`。图谱同步可单独重试。

`GraphBuildVO` 包含 `buildId/knowledgeBase/status/documentCount/processedDocumentCount/nodeCount/edgeCount/errorCode/createdAt/updatedAt`。错误只返回稳定代码，不返回内部异常正文。

## 来源与隔离

Java 从 Qdrant 当前租户的 ACTIVE 切片生成完整身份快照。计数、读回条数和再次计数必须一致，最多 10000 点；超限、缺失、重复身份或错误作用域整次拒绝。快照包含 `pointId/documentId/documentCode/documentVersion/documentRevision/chunkId/contentSha256`，不接受浏览器指定来源内容。JSON 图请求最多 8 MiB。

内部图响应的每个节点与关系必须提供 `properties.sources`，最多 8 条，并携带 `sourceCount/sourceTruncated`。Java 逐源复核上述全部身份字段、可选作用域和 ACTIVE 状态，白名单转换来源与页块元数据；未知属性不对外透传。节点、关系 ID 唯一，类型和数量有界，边端点必须出现在本次节点集合中。查询完成后重新取得活动快照，发现删文档、换版或其他来源变化时拒绝旧响应。

当前权限模型为业务权限码与租户隔离，KB 编码经过格式校验；本轮未新增用户/部门级知识库 ACL 数据源。以后引入细粒度授权时，必须先由 Java 收窄允许来源集合。

## 生命周期与预算

异步后端先完成向量读回、激活及版本对账，再提交图任务；图任务失败不回滚成功的向量。关闭新开关时保留同步 MySQL 的先图后版本对账顺序和原失败补偿。

停用文档先读取当前租户该文档的最大业务 revision，要求 Python 成功建立删除屏障并返回 epoch/revision 票据，失败时不继续修改 Qdrant 为 DELETED。向量更新失败后的图恢复必须携带本次票据，由服务重新读取仍为 ACTIVE 且 revision 相符的来源并创建任务；Python 再检查 epoch，旧补偿不能清除新删除。票据不进入公开 VO，普通同步不携带它。没有 ACTIVE 来源的单文档同步会明确停用旧图，空 KB 全量同步仍提交空来源对账任务。

来源证据可带 `evidenceTruncated/evidenceNormalized` 严格布尔标记，Java 白名单透传以区分有界原文摘录和经过规范化定位的摘录；未知属性仍不透传。

Python 检索的可选图增强继续返回 `mode=dense|hybrid`，另以 `graphAugmented/graphDegraded` 区分扩展与图降级；图不改写检索上下文为模型三元组。原向量候选作为种子，经本租户/KB 的已发布实体关系扩展最多 20 点，重新回读 ACTIVE Qdrant 原文及来源摘要、再确认图仍已发布后，以 RRF 融合并继续原重排和相关性阈值。图段共享独立 0.4 秒预算，原请求总预算保持。

`rag.engine.graph-timeout-millis` 默认 10000，限制构建接收、图查询与删除屏障请求；`rag.engine.graph-status-timeout-millis` 默认 3500，限制状态读取。两者单位均为毫秒，独立于检索与模型抽取总预算；异步任务执行时限由 Python 管理。内部 HTTP 沿用服务密钥、禁重定向、总等待时限、响应大小上限和异常脱敏。

## 验证与回滚

`GraphEngineContractIntegrationTest` 使用真实 Spring 服务与内部 HTTP 替身验证身份、作用域、任务协议、失败关闭、资源上限及查询期间来源变更；`GraphBuildHttpIntegrationTest` 仅装配 Web/Security 组件，通过真实随机端口验证公开路由、必填 KB 和权限；摄取测试覆盖图队列拒收后 ACTIVE 向量保持，删除测试覆盖屏障失败阻止向量删除。

运行 `./gradlew test --no-daemon`，不依赖开发 Nacos、真实租户、Neo4j 或付费模型。真实跨语言进程、Neo4j/Qwen、部署与浏览器验收分别取证，不能由本组替身测试推定。

回滚关闭图开关，再从 Qdrant 全量同步重建原 MySQL 结构图；不删除既有 MySQL 表或 Qdrant collection。
