# RAG 知识检索服务说明

`rag` 是 RAG 知识检索服务后端微服务。

当前项目从现有 Java 微服务工程骨架（`report`）复制迁移而来，只保留基础工程、统一异常处理、DDL 入口、CI、脚本和 AI 编码规范；**不包含任何 report 业务代码**，也不包含 `user` 服务的用户、租户、角色、权限资源等业务类。当前已先发布 RAG 检索的 Dubbo RPC / HTTP 边界，真实 embedding、Qdrant 检索、摄取、MCP 工具和 AI Registry 注册仍属 Phase 2，尚未实现。

## 技术基线

- Java 17
- Spring Boot 4.0.4
- Spring Cloud 2025.1.1
- Spring Cloud Alibaba 2025.1.0.0
- Nacos Client 3.2.2
- Seata Client 2.6.0
- MyBatis-Plus
- （Phase 2）Spring AI 2 + Qdrant 向量库
- 公共能力依赖 `com:utils`，跨服务契约依赖 `com:rpc-api`
- 测试框架统一使用 JUnit 5

## 服务职责

`rag` 定位为 fleet / AI 中台的「知识检索」能力提供方。当前 `ai` 可先通过 Dubbo RPC 调用 `rag` 获取检索上下文；后续如需被智能体动态发现，再以 **MCP 工具 + Nacos AI Registry** 形式暴露给 `ai-agent` 等调用方。后续围绕以下边界展开：

- **检索**：输入查询 → embedding → Qdrant 向量检索 → 返回带出处的上下文片段（不替调用方叫模型、不下结论）。
- **摄取**：文档解析 / 切片 / embedding / 写入 Qdrant；离线/批量，未来可能外移（如 Python）。摄取侧与查询侧必须同 embedding 模型、同维度。
- **能力暴露**：当前先提供 `RagRetrievalRpcService` 和 `/api/rag/retrievals` HTTP 边界；后续再补 `knowledge_search` 等 MCP 工具 + Nacos AI Registry AgentCard 注册，供 fleet 内任意 agent 复用。
- **知识库管理**：知识文档登记、版本、来源、collection 与权限范围（敏感数据归属留调用方控制面）。

公共工具、认证上下文、多租户、统一返回、统一异常、数据权限、MyBatis-Plus 公共配置等能力不在本服务重复实现，统一复用同级 `utils` 项目。

## 基础设施地址

除 `application.yml` 中连接 Nacos 自身的启动入口外，MySQL、Redis、RabbitMQ、Seata、XXL-JOB、Elasticsearch、Zipkin、Qdrant 等基础设施地址统一放在 Nacos（`reuse-configuration.yaml` 公共变量 / 各中间件 dataId）。业务配置只引用公共变量，不直接散落基础设施 IP。

## Nacos 配置

本地 `application.yml` 只保存连接 Nacos 的启动入口和 `spring.config.import` 远程配置列表。当前 `rag` 加载（见 `application-dev.yml`）：

```text
logging.yml
reuse-configuration.yaml
traffic-governance.yaml
security-auth.yaml
swagger.yaml
ai-model-secrets.yaml   # AI 模型供应商 API Key / base-url，供后续 embedding/rerank 引用；真实密钥只放 Nacos
rag.yaml                # rag 业务配置：RAG 检索参数、collection、topK、阈值等私有 @ConfigurationProperties
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

MyBatis-Plus DDL 入口在 `src/main/java/com/kellen/bean/MysqlDdl.java`。当前 `rag` 暂无业务表，`MysqlDdl#getSqlFiles()` 返回空列表。

全新或空业务库首次启动前，必须先在目标业务库手动执行公共基础设施脚本：

```text
../utils/src/main/resources/db/common-infra-schema.sql
```

该脚本只维护 MyBatis-Plus DDL 记录表 `ddl_history` 和 Seata AT 补偿表 `undo_log`。Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖应用首次启动自动创建。后续新增知识库业务表时：SQL 放 `src/main/resources/db/*.sql`，先查 `ddl_history`，已执行的脚本不回改，新增变更用新 SQL 文件并追加到 `MysqlDdl#getSqlFiles()`。

## 接口文档

OpenAPI 原始文档地址：

```text
http://127.0.0.1:7320/v3/api-docs
```

Controller 必须使用 OpenAPI3 注解（`@Tag` / `@Operation` / `@Schema`），确保文档展示业务名称而非默认方法名。需要鉴权的接口使用 `@PreAuthorize`，权限码命名建议 `rag:<resource>:<action>`（如 `rag:document:create`、`rag:document:list`）。

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
