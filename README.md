# RAG 知识检索服务说明

`rag` 是 RAG 知识检索服务后端微服务。

当前 Java 版本为 `1.3.0`，Engine 为 `0.3.1`。本批修复 NAS 上图扩展持续超时降级：Engine 在请求外预编译图扩展查询计划，邻居扩展按名称截取候选后结果不变、执行耗时约降为三分之一，0.4秒局部／3秒整体预算不变，细节见 `engine/README.md`；源码与离线测试不表示已部署，NAS 镜像切换与图检索开关恢复另行取证。Engine `0.3.0` 修复图 worker 已失活但健康检查仍成功的缺口：图谱开启时，只有启动成功且 heartbeat/pump 两任务仍运行才健康；失租保持 fail-stop，健康与图谱入站返回503，不自动重拿租约。源码与离线测试不表示已部署或恢复实际 worker。

三个 profile 不再导入只提供 Google 凭据与模型名的 `ai-model-secrets.yaml`：Java RAG 使用 `rag.vector.*`，不创建 Google 模型。移除无关导入不改变 embedding 模型、维度、Engine 环境变量或其它 Nacos 导入；已运行进程须受控重启新制品后再核对，源码变更不代表旧进程已卸载凭据。

Java `1.2.5` / Engine `0.2.9` 增加显式 rerank 端点与 DashScope/Cohere wire 选择，可对接 LiteLLM 的完整 `/v1/rerank`；未配置新参数时保留旧路径派生。Java/Python 均严格拒绝缺失或冲突的 embedding 索引及非法向量，整份摄取不返回部分批次。物理模型、1024维默认、缓存/提取/图谱规则保持；接入参数和真实切流边界见[Engine配置](engine/README.md#启动)，源码测试不表示NAS已切换。

当前项目从现有 Java 微服务工程骨架（`report`）复制迁移而来，**不包含任何 report 业务代码**，也不包含 `user` 服务的用户、租户、角色、权限资源等业务类。当前已实现 XLSX / DOCX 结构化摄取、embedding、Qdrant 向量存储与检索、rerank 重排、文档幂等/版本、向量读回校验，以及可视化知识图谱投影；MCP 工具和 AI Registry 注册仍属后续阶段。

个人仓库仅保留 `main` 长期分支；实施中的短功能分支交付后清理，不再创建 `test/preprod`。Git 分支不改变运行 profile 或个人 `github-dev` 命名空间。

## Python 引擎演进

本批 Java 版本为 `1.1.7`，同仓独立的 [`engine/`](engine/README.md) 负责解析、检索与可选的语义图谱构建。Java 继续负责权限、租户、文档版本、幂等、Qdrant 写入和图谱来源终检；现有 HTTP 和 Dubbo 契约保持。

- 开启 `rag.engine.parse-enabled` 后支持 PDF、PNG、JPG/JPEG。文字 PDF 由 pypdf + LlamaIndex 解析，扫描件/图片需另配 PaddleOCR 服务；缺少 OCR 时明确报错。
- 检索统一调用 Python Engine，失败直接返回现有 RPC 降级结果；旧 Java 查询/重排与迁移开关已移除。Python 可启用 LlamaIndex dense + TF-IDF sparse + RRF 混合召回，须先准备 `lexical-v1` schema、完整回填并启用 Java 双写；运行时逐范围检查索引完整性。
- Java 确保集合时同时创建 `sparse_model` 的 keyword payload 索引，供逐范围稀疏完整性检查过滤使用。已有集合需显式补建这一派生索引；无需重新上传文档、写入原文或向量、重新抽取图谱，检索过滤与三秒总预算保持。
- PDF/OCR 保存页码、区块、坐标与全部重复来源 metadata。解析有大小、页数、并发、CPU/内存和总时间限制；不支持任意 URL/文件路径输入。
- [`evaluation/`](evaluation/README.md) 提供 60 题合成基准、28 篇通用技术语料和 Recall/MRR/nDCG/延迟评估器，不能代替真实样本质量验收。
- Nacos 唯一模板仍在 `../utils/ops/github-dev/nacos/DEFAULT_GROUP/rag-spring.yaml`；仅 PDF/图片解析的 `rag.engine.parse-enabled` 开关保留且默认关闭；检索和图谱固定使用 Engine/Neo4j，部署前必须配置并验证引擎地址与服务密钥。Java 引擎密钥与 Python 环境变量需一致，不能放在前端或公开 Git；回滚须恢复匹配的制品和配置。

完整范围、验收与回滚见 [改版手册](docs/rag-engine-upgrade.md)，Python 环境、接口契约与部署见 [引擎文档](engine/README.md)。轻量引擎与 OCR 模型分别部署；NAS 示例限制引擎 1.5 GiB，不包含 OCR 权重。

## LlamaIndex 与 Neo4j 知识图谱

原有 `/api/rag/graphs` 接口通过 Java 门面读取 Neo4j 派生索引。Python 使用 LlamaIndex PropertyGraph 及官方 Neo4j 适配器，Qwen 只用于实体关系抽取；Qdrant 的原文、dense/sparse schema 与嵌入维度保持独立。旧 MySQL 文档结构投影与迁移开关已移除。

构建为异步任务：同步接口返回 `buildId/status`，状态接口 `GET /api/rag/graphs/builds/{id}?knowledgeBase=...` 区分排队、运行、完成和失败。上传成功只表示向量写入和校验完成；图谱抽取失败不会撤销活动向量，可在图谱页重试。新图在完整构建和来源重检后才发布，失败时保留之前可用的图。

图谱同步可在 `POST /api/rag/graphs/sync` 携带 `X-Graph-Submission-Key`（16–128位字母、数字、下划线或连字符）。同一调用意图重试复用键；明确再次重建使用新键。键按可信租户与知识库隔离，原任务及执行输入摘要在同一 Neo4j 事务中绑定；相同键的不同输入返回 HTTP 409，终态重放不新增任务、不重新调用模型。`GET /api/rag/graphs/submissions/{key}?knowledgeBase=...` 只读查回原任务，未找到返回 `data=null`，保持 `rag:graph:view/manage` 权限与服务端租户边界。未携带键的旧客户端保持原有显式提交行为。

Engine未知提交返回404；Java门面把该只读查询的404映射为200空data（按全局序列化设置也可能省略data字段），供页面区分“未找到”与请求失败。非法提交键返回受控400，不回显原键；权限检查仍先于参数处理。空结果不允许页面自动POST，来源快照改变时原键重发会明确409，应保留原键查回原任务。

相同活动文档版本的重复上传复用稳定图谱提交身份；原图谱任务失败时重复上传也保留该失败，用户可在知识图谱页明确发起新重建。提交异常后不能把“没拿到buildId”当作“未创建”；先按键查回。图谱提交协议要求 Java `1.2.4`、Engine `0.2.7` 起与后台配套发布，不能只升级发送新字段的消费者。此前 Engine `0.2.8` 补充安全后台诊断 OTLP 导出，Java API 不变；见 [引擎日志合同](engine/README.md#指标链路与安全运行事件028)。当前未创建业务Cron或Conductor工作流。

批量抽取对每个切片执行完整出处校验。局部引用纠正不适用时，在单片最多三个模型请求的预算内重新抽取完整候选；不会把模型拼接出的名称当作原文，也不会靠删除失败切片或空图假成功。具体错误、验收及部署边界见 [批量抽取修复说明](docs/graph-batch-grounding-repair.md)。

语义实体类型为 `ENTITY`，关系为 `RELATED_TO`，实际谓词显示在 `label`。节点及关系分别提供多文档证据，包含文档版本、切片指纹、来源定位和受控摘录；同名实体的跨来源展示仅在当前租户和知识库范围内聚合。实体关系是模型抽取结果，应结合原文核实，不能替代原始文档。前端不连接 Neo4j，也不传租户或模型凭据。

Java 每次提供当前活动来源快照，并对引擎返回的证据逐条复核。构建 staging generation、删除屏障和 revision 比较阻止迟到任务复活旧来源。完整协议、限制与回滚见 [图谱设计](docs/neo4j-graph-design.md) 和 [实施与验收计划](docs/neo4j-graph-plan.md)。

NAS 示例的宿主端口默认 `18091`（`RAG_ENGINE_HOST_PORT`），避开 Seata 已使用的 `8091`；引擎容器内部仍为 `8091`。个人 Nacos 的 `rag.engine.base-url` 需与宿主端口一致，OCR 仍通过容器内网 `rag-ocr:8092` 访问。具体配置见 [NAS 双容器部署](engine/README.md#nas-双容器部署)。

本机验证（无 Nacos、无真实模型调用）：

```sh
./gradlew test bootJar
(cd engine && uv sync --frozen && uv run --frozen pytest -q)
python3 -m unittest discover -s evaluation/tests -q
python3 scripts/verify-rag-engine.py
```

## 启动入口

主应用类为 `com.kellen.RagApplication`，IDE 可直接运行该类。Gradle `bootRun` 与可执行 JAR 的入口由 `springBoot.mainClass` 统一指定；仍使用 `./gradlew bootRun` 或 `java -jar build/libs/rag.jar`，工作目录保持为当前服务仓库根目录。

## 本地 dev 启动

本地默认使用 `dev` profile。IDE 的 working directory、`./gradlew bootRun` 和 `java -jar build/libs/<服务名>.jar` 的当前目录必须是本服务仓库根目录，Spring 才会自动读取 `config/application-dev.properties`。该私有文件由本机入口生成、权限为 `0600`，仅保存 Nacos 连接的五个引导键；不放模型或数据库密钥，不提交 Git，也不修改 `application.yml`。从其它目录启动时，应显式设置 `spring.config.additional-location`，不能假设自动搜索会定位到服务仓库。

同名 OS 环境变量优先于私有文件；dev 地址按 `GITHUB_NACOS_DEV_SERVER_ADDR`、`GITHUB_NACOS_SERVER_ADDR` 依次读取，两者都未提供时拒绝缺失地址，不再回落到 `127.0.0.1:8848`。控制台使用 `18080`，客户端/SDK 使用 `8848`，SDK gRPC 连接使用 `9848`，不能把控制台端口当客户端地址。个人 namespace ID 保持 `github-dev`；远端业务配置仍从 Nacos 读取。单独激活 `test` 或 `prod` 不会读取这个 dev 私有文件，需提供其各自的连接配置。

## 技术基线

- Java 21
- Spring Boot 4.0.9
- Spring Cloud 2025.1.4
- Spring Cloud Alibaba 2025.1.0.0
- Nacos Client 3.3.0
- Seata Client 2.6.0
- MyBatis-Plus
- Qdrant REST 向量库
- 千问 `text-embedding-v4`（默认 1024 维）与 `qwen3-rerank`
- 公共能力依赖 `com:utils:1.6.7`，跨服务契约固定 `com:rpc-api:1.0.9`（先完成 Maven Local 消费验证，再发布远端私有 Maven 制品）
- 个人与公司共用私有 Maven 地址，utils 分别使用 `com` 与 `com.kellen` group 隔离；Java 包名保持 `com.kellen.*`。
- 测试框架统一使用 JUnit 5

## 服务职责

`rag` 定位为 fleet / AI 中台的「知识检索」能力提供方。当前 `ai` 可通过 Dubbo RPC 调用 `rag` 获取检索上下文；后续如需被智能体动态发现，再以 **MCP 工具 + Nacos AI Registry** 形式暴露给 `ai-agent` 等调用方。

- **检索**：输入查询 → embedding → Qdrant 向量检索 → 返回带出处的上下文片段（不替调用方叫模型、不下结论）。
- **摄取**：当前默认接收 XLSX 和 DOCX；启用解析引擎后增加 PDF/PNG/JPEG；XLSX 按“工作表 + 完整表头 + 单行记录”切片，DOCX 按标题段落和表格行切片，再 embedding 并写入 Qdrant。摄取侧与查询侧固定使用同 embedding 模型、同维度。
- **能力暴露**：当前先提供 `RagRetrievalRpcService` 和 `/api/rag/retrievals` HTTP 边界；后续再补 `knowledge_search` 等 MCP 工具 + Nacos AI Registry AgentCard 注册，供 fleet 内任意 agent 复用。
- **知识库管理**：Qdrant payload 显式保存租户、知识库、文档版本、来源、文件/内容指纹和重复行位置。相同文件幂等，同编码新内容生成新版本；文档内语义相同且仅序号/行号不同的记录合并向量，但保留全部定位信息。
- **知识图谱**：从活动向量切片确定性投影文档、章节/工作表/页面/图片、记录/区块和概念关系；图谱表是可重建派生索引，不让模型猜测关系，稳定节点/边编码便于后续迁移到 Neo4j。PDF/图片原文没有 `- 字段: 值` 结构时只产生来源结构，不自动猜测实体或关系；关系总览会将这类无概念边的文档与已有概念网络一起纳入节点预算。

图谱节点 `properties` 保留原有字段：文档有 `documentCode/documentVersion`，章节有 `sectionPath`，记录有 `chunkId/chunkType/sectionPath`。有来源数据时，文档、章节和记录增加 `sourceName/sourceType`；记录增加白名单 `sourceMetadata`（`page/bbox/block_type/ocr_capabilities`），章节只增加页码，区块坐标不代表整页坐标。OCR 能力仅发布 `textRecognition/tableStructure/layoutAnalysis` 布尔值，不透传任意解析元数据。旧文档字段缺失时保持可读；历史图谱需通过现有同步接口从活动向量重建才能补齐来源属性，无需重新 embedding。

图谱搜索及节点类型筛选继续表示“命中节点与相邻关系”，邻居可以是其他类型；命中节点优先占用节点预算。无筛选总览交替纳入文档与概念关系，超出节点或关系上限时设置 `truncated=true`，不保证截断结果涵盖全库。接口、租户/知识库过滤、节点/边稳定 ID 和关系类型保持兼容。

公共工具、认证上下文、多租户、统一返回、统一异常、数据权限、MyBatis-Plus 公共配置等能力不在本服务重复实现，统一复用同级 `utils` 项目。

## 基础设施地址

除 `application.yml` 中连接 Nacos 自身的启动入口外，PostgreSQL、Redis、RabbitMQ、Seata、Elasticsearch、Zipkin、Qdrant 等基础设施地址统一放在 Nacos（`reuse-configuration.yaml` 公共变量 / 各中间件 dataId）。业务配置只引用公共变量，不直接散落基础设施 IP。

## Nacos 配置

本地 `application.yml` 只保存基础启动入口，不保存模型密钥或 RAG 业务参数。当前 `rag` 加载（见各 `application-*.yml`）：

```text
logging.yml
reuse-configuration.yaml
traffic-governance.yaml
actuator.yaml
security-jwt.yaml
security-auth.yaml
swagger.yaml
rag-spring.yaml         # 服务框架、数据源、rag.vector.*、rag.engine.* 及其它服务专属参数
qdrant.yaml             # Qdrant 向量库连接，RAG 检索入口
mybatis-plus.yaml
redis.yaml
rabbitmq.yaml
elasticsearch.yaml
seata.yaml
dubbo.yaml
a2a.yaml                # A2A / Nacos AI Registry 共享契约，rag 作为 AI 能力注册方引用 ${custom.a2a-*}
```

## DDL

文档注册、切片元数据和向量保存在 Qdrant payload；Neo4j 是唯一派生图实现。旧节点/边表和自动建表入口已退役，历史建表 SQL 保留作为历史证据；独立清理候选与回滚边界见 [旧实现清理](docs/legacy-rag-retirement.md)。

全新或空业务库首次启动前，必须先在目标业务库手动执行公共基础设施脚本：

```text
../utils/src/main/resources/db/postgresql/common-infra-schema.sql
```

该脚本只维护 MyBatis-Plus DDL 记录表 `ddl_history` 和 Seata AT 补偿表 `undo_log`。Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖应用首次启动自动创建。业务 SQL 变更前先查 `ddl_history`；已执行或无法确认的脚本不回改，新增变更使用新 SQL 文件并追加到 `PostgresqlDdl#getSqlFiles()`。

## 接口文档

核心接口：

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| `POST` | `/api/rag/documents` | `rag:document:manage` |
| `GET` | `/api/rag/documents` | `rag:document:manage` |
| `POST` | `/api/rag/documents/{id}/validation` | `rag:document:manage` |
| `DELETE` | `/api/rag/documents/{id}` | `rag:document:manage` |
| `POST` | `/api/rag/retrievals` | `rag:retrieval:use` |
| `GET` | `/api/rag/graphs` | `rag:graph:view` |
| `POST` | `/api/rag/graphs/sync` | `rag:graph:manage` |

HTTP 权限由 RAG Controller 的 `@PreAuthorize` 强制执行；`user` 只负责下发资源和权限，管理前端的菜单/按钮权限不能替代后端鉴权。所有向量、文档和图谱查询都必须取得当前租户上下文。

## RESTful 接口约定

新增 Controller 优先使用 RESTful 资源路径与 HTTP 方法，不使用 `/save`、`/update`、`/remove`、`/select`、`/page` 等动词路径；查询用 `GET` URL 参数。详见 `docs/ai-coding/PROJECT_CODING_SPEC.md`。

## AI 编码规范

AI 编码规范入口：`AGENTS.md` → `docs/ai-coding/README.md`。

> 注：`docs/ai-coding` 中的深度业务规范当前仍保留 `report` 骨架的业务示例，将随 Phase 2 rag 业务实现逐步细化为 RAG 领域；通用规范（注释、设计、测试、分支、版本、RPC、utils、Nacos）直接适用。详见 `AGENTS.md` 的「与现有 report 骨架的差异说明」。

## 验证命令

```bash
./gradlew clean compileJava -x test --no-daemon
./gradlew test --no-daemon
bash scripts/check-secrets.sh
```

如果依赖 `utils` 有调整，先在同级 `utils` 项目执行 `./gradlew publishToMavenLocal`，再回到本项目编译。

通用安全、隔离与本批验证边界见 [安全与架构说明](docs/security-architecture-sync.md)。

## 本批可观测性接入

当前服务版本为 `1.2.3`，公共能力精确消费 `com:utils:1.6.2`，并由公共包传递引入轻量 `com:fleet-observability:0.1.2` 的安全 HTTP 终态事件。公共制品先完成 Maven Local 验证，再按对应 main 确定提交发布远端；目标环境的实际采集与全链读回单独验收。九服务共享 `actuator.yaml` 暴露 health/info/prometheus；除 health 外仍要求 `actuatorReq` 运维令牌，空值或错误值失败关闭。`logging.yml` 使用 ECS 固定字段白名单，未知 MDC、原始消息、正文和异常不进入采集结果；OTel agent 标准 `trace_id/span_id` 与业务会话 ID 分开。应用指标带固定 application/environment 标签，禁止动态业务 ID。

Admin/Zipkin 模板与三 profile 的 imports 已退役。服务 JVM 的可选 OTel agent 由统一启动入口负责，不通过全局 JAVA_TOOL_OPTIONS 注入 Gradle 或测试。部署时 Java 指标只由 Micrometer Prometheus 提供，OTEL_METRICS_EXPORTER 与 OTEL_LOGS_EXPORTER 均为 none；监控组件、配置发布和实际采集仍须独立验证。回滚须恢复匹配的制品与配置，先恢复旧 dataId 再回退旧消费者，不能用已删除的配置项假装完成回滚。

个人业务 PostgreSQL 迁移基线服务版本为 `1.2.0`，首版公共库为 `com:utils:1.6.0`（传递安全采集库 `com:fleet-observability:0.1.1`）。版本和本机构建通过不代表目标环境已切库，正式迁移与运行态验收另行记录。

个人 Java 服务已移除 XXL-JOB 依赖、执行器配置与演示任务，不再读取 `xxl-job.yaml`。Conductor OSS 作为 NAS 独立服务通过 REST 接入；当前未接入业务 Java 调度接口，不添加 Java 21 SDK 或虚构任务开关。其他环境仍使用的 NAS 容器不由本次个人源码退役处理。

## PostgreSQL 启动修复补丁

PostgreSQL 启动修复补丁版本为 `1.2.1`，公共依赖精确固定 `com:utils:1.6.1`，接入 PostgreSQL/Druid 超时单位修复及事务化、失败即拒绝启动的 DDL 执行。原定连接 3 秒、读取 30 秒预算保持，RPC 契约 `com:rpc-api:1.0.7` 与轻量采集库 `com:fleet-observability:0.1.1` 保持。公共制品、Jar 构建、服务器与 IDEA 原生启动须分别验证，修改依赖不代表目标环境已恢复。

## JSON 请求体错误映射

当前 Java 补丁 `1.2.2` 将真实 MVC 的 `HttpMessageNotReadableException` 精确映射为 HTTP 400、`A0400` 和固定提示，不再由通用异常兜底误报 500。解析正文、cause 消息及堆栈不进入该错误日志或响应，未预期业务异常仍保持 500，检索总预算和 Engine 不变。回归及运行态边界见 [JSON 请求体绑定错误](docs/json-binding-errors.md)。

本次公共观测补丁通过 `com:utils:1.6.2` 精确传递 `com:fleet-observability:0.1.2`，修复 Servlet Security 在 ERROR 分派中再次拒绝导致终态缺失的问题；认证和错误响应保持。源码与测试通过后，目标进程和追踪/日志联结仍须单独读回验收。

`1.2.8` 为第1阶段底座升级：Java 21、Gradle 9.9.1、Spring Boot 4.0.9、Spring Cloud 2025.1.4、Nacos 客户端 3.3.0，公共依赖 `com:utils:1.6.6`（传递 `com:fleet-observability:0.1.4`）与 `com:rpc-api:1.0.9`，OTel BOM 1.67.0 对齐 Java agent 2.33.1。业务接口与配置不变；运行环境须为 Java 21。

`1.3.0` 将公共依赖升到 `com:utils:1.6.7`：修复 Dubbo 上下文透传过滤器拿不到 Spring 绑定的灰度与租户配置（提供方误拒全部灰度授权、灰度实例不带授权也放行、自定义租户头名不生效），本服务代码不变。
