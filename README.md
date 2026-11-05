# RAG 知识检索服务说明

`rag` 是 RAG 知识检索服务后端微服务。

当前项目从现有 Java 微服务工程骨架（`report`）复制迁移而来，**不包含任何 report 业务代码**，也不包含 `user` 服务的用户、租户、角色、权限资源等业务类。当前已实现 XLSX / DOCX 结构化摄取、embedding、Qdrant 向量存储与检索、rerank 重排、文档幂等/版本、向量读回校验，以及可视化知识图谱投影；MCP 工具和 AI Registry 注册仍属后续阶段。

个人仓库仅保留 `main` 长期分支；实施中的短功能分支交付后清理，不再创建 `test/preprod`。Git 分支不改变运行 profile 或个人 `github-dev` 命名空间。

## Python 引擎演进

本批 Java 版本为 `1.1.0`，新增同仓独立的 [`engine/`](engine/README.md)。Java 继续负责权限、租户、文档版本、幂等、Qdrant 写入及图谱；现有 HTTP 和 Dubbo 契约保持。

- 开启 `rag.engine.parse-enabled` 后支持 PDF、PNG、JPG/JPEG。文字 PDF 由 pypdf + LlamaIndex 解析，扫描件/图片需另配 PaddleOCR 服务；缺少 OCR 时明确报错。
- `rag.engine.retrieval-enabled` 默认 false。开启后使用 Python 检索，失败不叠加旧查询；关闭可恢复 Java 检索。Python 可启用 LlamaIndex dense + TF-IDF sparse + RRF 混合召回，须先准备 `lexical-v1` schema、完整回填并启用 Java 双写；运行时逐范围检查索引完整性。
- PDF/OCR 保存页码、区块、坐标与全部重复来源 metadata。解析有大小、页数、并发、CPU/内存和总时间限制；不支持任意 URL/文件路径输入。
- [`evaluation/`](evaluation/README.md) 提供 60 题合成基准、28 篇通用技术语料和 Recall/MRR/nDCG/延迟评估器，不能代替真实样本质量验收。
- Nacos 唯一模板仍在 `../utils/ops/github-dev/nacos/DEFAULT_GROUP/rag-spring.yaml`；新增两开关默认关闭。Java 引擎密钥与 Python 环境变量需一致，不能放在前端或公开 Git。

完整范围、验收与回滚见 [改版手册](docs/rag-engine-upgrade.md)，Python 环境、接口契约与部署见 [引擎文档](engine/README.md)。轻量引擎与 OCR 模型分别部署；NAS 示例限制引擎 1.5 GiB，不包含 OCR 权重。

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

- Java 17
- Spring Boot 4.0.4
- Spring Cloud 2025.1.1
- Spring Cloud Alibaba 2025.1.0.0
- Nacos Client 3.2.2
- Seata Client 2.6.0
- MyBatis-Plus
- Qdrant REST 向量库
- 千问 `text-embedding-v4`（默认 1024 维）与 `qwen3-rerank`
- 公共能力依赖 `com:utils:1.5.2`，跨服务契约固定 `com:rpc-api:1.0.7`（先完成 Maven Local 消费验证，再发布远端私有 Maven 制品）
- 个人与公司共用私有 Maven 地址，utils 分别使用 `com` 与 `com.kellen` group 隔离；Java 包名保持 `com.kellen.*`。
- 测试框架统一使用 JUnit 5

## 服务职责

`rag` 定位为 fleet / AI 中台的「知识检索」能力提供方。当前 `ai` 可通过 Dubbo RPC 调用 `rag` 获取检索上下文；后续如需被智能体动态发现，再以 **MCP 工具 + Nacos AI Registry** 形式暴露给 `ai-agent` 等调用方。

- **检索**：输入查询 → embedding → Qdrant 向量检索 → 返回带出处的上下文片段（不替调用方叫模型、不下结论）。
- **摄取**：当前默认接收 XLSX 和 DOCX；启用解析引擎后增加 PDF/PNG/JPEG；XLSX 按“工作表 + 完整表头 + 单行记录”切片，DOCX 按标题段落和表格行切片，再 embedding 并写入 Qdrant。摄取侧与查询侧固定使用同 embedding 模型、同维度。
- **能力暴露**：当前先提供 `RagRetrievalRpcService` 和 `/api/rag/retrievals` HTTP 边界；后续再补 `knowledge_search` 等 MCP 工具 + Nacos AI Registry AgentCard 注册，供 fleet 内任意 agent 复用。
- **知识库管理**：Qdrant payload 显式保存租户、知识库、文档版本、来源、文件/内容指纹和重复行位置。相同文件幂等，同编码新内容生成新版本；文档内语义相同且仅序号/行号不同的记录合并向量，但保留全部定位信息。
- **知识图谱**：从活动向量切片确定性投影文档、工作表、记录和概念关系；图谱表是可重建派生索引，不让模型猜测关系，稳定节点/边编码便于后续迁移到 Neo4j。

公共工具、认证上下文、多租户、统一返回、统一异常、数据权限、MyBatis-Plus 公共配置等能力不在本服务重复实现，统一复用同级 `utils` 项目。

## 基础设施地址

除 `application.yml` 中连接 Nacos 自身的启动入口外，MySQL、Redis、RabbitMQ、Seata、XXL-JOB、Elasticsearch、Zipkin、Qdrant 等基础设施地址统一放在 Nacos（`reuse-configuration.yaml` 公共变量 / 各中间件 dataId）。业务配置只引用公共变量，不直接散落基础设施 IP。

## Nacos 配置

本地 `application.yml` 只保存基础启动入口，不保存模型密钥或 RAG 业务参数。当前 `rag` 加载（见各 `application-*.yml`）：

```text
logging.yml
reuse-configuration.yaml
traffic-governance.yaml
security-auth.yaml
swagger.yaml
ai-model-secrets.yaml   # AI 模型供应商 API Key / base-url；真实密钥只放 Nacos
rag-spring.yaml                # rag 业务配置：RAG 检索参数、collection、topK、阈值等私有 @ConfigurationProperties
rag-spring.yaml         # rag Spring / 数据源 / Spring AI 模型 / 服务私有基础配置
qdrant.yaml             # Qdrant 向量库连接，RAG 检索入口
mybatis-plus.yaml
redis.yaml
rabbitmq.yaml
elasticsearch.yaml
seata.yaml
zipkin.yaml
admin.yaml
dubbo.yaml
xxl-job.yaml
a2a.yaml                # A2A / Nacos AI Registry 共享契约，rag 作为 AI 能力注册方引用 ${custom.a2a-*}
```

## DDL

MyBatis-Plus DDL 入口在 `src/main/java/com/kellen/bean/MysqlDdl.java`。文档注册、切片元数据和向量保存在 Qdrant payload；知识图谱作为可重建派生索引，使用 `db/rag-knowledge-graph-schema.sql` 创建节点与关系表。两张表的唯一索引都显式包含 `tenant_id + knowledge_base + stable code`。

全新或空业务库首次启动前，必须先在目标业务库手动执行公共基础设施脚本：

```text
../utils/src/main/resources/db/common-infra-schema.sql
```

该脚本只维护 MyBatis-Plus DDL 记录表 `ddl_history` 和 Seata AT 补偿表 `undo_log`。Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖应用首次启动自动创建。业务 SQL 变更前先查 `ddl_history`；已执行或无法确认的脚本不回改，新增变更使用新 SQL 文件并追加到 `MysqlDdl#getSqlFiles()`。

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
