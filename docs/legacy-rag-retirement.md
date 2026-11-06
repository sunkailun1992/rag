# 旧 RAG 与 MySQL 图谱退役

本版 Java `1.1.6` 以 Java 权限/文档门面 + Python Engine + Qdrant + Neo4j 为唯一实现。外部 HTTP/Dubbo DTO、认证、tenant、KB、来源和版本规则保持；不执行重嵌入、全库重建或实际删表。

删除项：旧 Java 向量查询与 qwen rerank、RerankClient/RerankResult，MySQL 图谱 Store、Projector、节点/边 Entity/Mapper、五个旧投影模型、自动 MysqlDdl 入口及仅覆盖旧实现的测试。`rag.engine.retrieval-enabled` 和 `rag.engine.graph-enabled` 不再绑定，旧配置不会恢复旧后端。Engine 错误只走现有失败降级，不追加另一条查询而突破预算。

已删除仓库根目录无效的 `Dockerfile`：该继承模板仍使用 JRE 8、复制 `report-1.0.2.jar`，并依赖来源未明的 entrypoint；当前 README、脚本和 CI 不使用它。Java 门面继续通过 `./gradlew bootRun` 或 `./gradlew bootJar` 后运行可执行 JAR；Engine 与 OCR 各自使用 `engine/Dockerfile`、`engine/ocr/Dockerfile`，现有容器构建入口不变。本次不替换 Java 基础镜像，不推定已有 Java 容器部署。

保留项：XLSX/DOCX 的 POI 结构化解析仍由当前上传路径使用；PDF/图片 Engine 解析开关仍是安全停用入口；Java document embedding、dense/sparse 写入、Qdrant scroll/count/readback、文档版本/幂等/活动状态、原文和来源metadata全部仍在用。图谱前后来源身份终检、revision、删除屏障与票据补偿、内部Bearer和可信租户头均保留。节点/边 VO、枚举属于当前公开图协议，继续保留。

| 对象 | 影响 | 本批行为 |
|---|---|---|
| `rag_graph_node` | 旧确定性结构投影，非权威原文 | 删除代码引用，提供独立 DROP 候选，未连接数据库 |
| `rag_graph_edge` | 旧结构关系派生索引 | 同上，清理顺序先边后点 |
| `ddl_history`、`undo_log` | 公共基础设施 | 不改动；新库仍按公共基础设施脚本预建 |
| Qdrant payload/collection | 当前文档/版本/来源与向量权威 | 保留，不删、不重建 |
| Neo4j | 当前来源约束的派生索引 | 保留，不改数据 |

历史 `rag-knowledge-graph-schema.sql` 原样保留，不再由应用或初始化生成器注册。候选 `retired-rag-graph-cleanup.sql` 不在自动DDL注册列表中，必须独立审查。执行前逐项确认目标schema和版本、旧消费者已停止、两表完整备份和恢复演练、无额外触发器/外键/外部查询依赖；核行数与内容归属，然后验证当前Neo4j构建和删除/换版反例。本轮没有这些现场证据，不声称表无数据或可立即删除。

回滚必须恢复上一套相互匹配的 Java/Engine 制品和配置。未删旧表时可以回退旧制品；如果将来执行DROP，先从备份恢复旧表，不能仅把旧开关设回false。本版不自动向MySQL回填，不自动从Qdrant重建旧图，避免把未知现场数据当作可安全重建。旧Admin/Zipkin远端dataId也仅在全部消费者切换读回及备份后清理。

验证：完整Java单元/HTTP/合同测试、Engine来源分页与图响应校验、权限反例、版本/幂等/Qdrant读回均按当前实现运行。真实模型、Nacos、Qdrant/Neo4j与NAS部署需要独立验收，源码和本机测试不证明这些阶段完成。
