# AGENTS.md

本文件是 `rag` 服务的 AI 编码入口。AI 修改本项目代码前，必须先阅读本文件，再按任务风险阅读 `README.md` 和 `docs/ai-coding` 下的规范。

## 项目定位

- 项目名称：`rag`
- 项目类型：RAG 知识检索服务后端；为 fleet / AI 中台提供「知识检索」能力，以 **MCP 工具 + Nacos AI Registry** 形式被智能体（如 `ai-agent` 及后续新中台 agent）发现并调用。
- 技术栈：Java 17、Spring Boot 4、（Phase 2 引入）Spring AI 2 + Qdrant 向量库、Spring Cloud Alibaba、Nacos、Dubbo、MyBatis-Plus、Gradle、`com:utils`、`com:rpc-api`
- 同级依赖：`../rpc-api` 提供跨服务 RPC 接口和 DTO 契约；`../utils` 提供公共响应、认证上下文、租户、多数据源和基础工具；`../gateway` 负责路由；`../ai-agent` 是当前主要 MCP / AI Registry 消费方
- 当前状态：从现有 Java 微服务工程骨架（`report`）复制迁移而来，只保留基础工程、配置入口、公共基础设施 DDL、CI、脚本和 AI 编码规范；**不携带任何 report 业务代码**。RAG 业务（检索、摄取、向量库、MCP 工具、AI Registry 注册）尚未实现，属 Phase 2。
- 核心风险：检索结果污染模型上下文或越权读取知识、医疗/专业知识来源不可信、embedding 模型与维度在「摄取」与「查询」两侧不一致、向量库连接与降级、敏感数据（如患者画像）归属与合规、MCP / A2A 契约不稳

## 与现有 report 骨架的差异说明（重要）

- 本服务由 `report` 工程骨架复制而来。**通用规范**（注释风格、设计模式分层、测试分层、分支、版本、RPC、utils、Nacos 配置）直接适用，无需改动。
- `docs/ai-coding` 中的**深度业务规范**（`SECURITY_CODING_SPEC.md`、`AI_DIRECTORY_STRUCTURE_GUIDE.md`、`PROJECT_CODING_SPEC.md`、`AI_DESIGN_PATTERN_GUIDE.md` 等）目前仍**保留 report 的业务示例**（报告生成/导出/审核、`/report/manage` 接口等）。这些是继承自骨架的占位示例，**将随 Phase 2 rag 业务实现逐步细化为 RAG 领域**（知识库、文档、切片、embedding、检索、MCP）；在 RAG 业务落地前，不要把这些 report 示例当作 rag 的真实业务规则。

## 修改前阅读顺序

任何代码修改前必须先阅读：

1. `README.md`：确认当前 RAG 知识检索服务职责、接口范围、表结构和验证命令。
2. `docs/ai-coding/README.md`：确认 AI 编码入口和阅读顺序。
3. `docs/ai-coding/AI_CODING_GUIDE.md`：确认执行步骤、注释规则、测试和安全要求。
4. `docs/ai-coding/AI_DIRECTORY_STRUCTURE_GUIDE.md`：确认 Java 微服务目录、测试、资源、文档和跨项目边界。
5. `docs/ai-coding/AI_COMMENT_STYLE_GUIDE.md`：确认注释规范、自解释优先、禁止注释掉死代码和排版要求。
6. `docs/ai-coding/AI_DESIGN_PATTERN_GUIDE.md`：确认 Provider、Service、Strategy、Adapter 等设计模式和禁止过度抽象规则。
7. `docs/ai-coding/BRANCHING_SPEC.md`：确认分支命名、短分支生命周期、release/hotfix、tag 和清理规则。
8. `docs/ai-coding/ENVIRONMENT_CONFIG_SPEC.md`：确认环境、Nacos namespace、Java profile 边界。
9. `docs/ai-coding/VERSIONING_SPEC.md`：确认 `group = 'com'`、`version = '1.0.0'`、补丁递增和消费者同步规则。
10. `docs/ai-coding/RPC_API_CODING_SPEC.md`：涉及 Dubbo RPC provider、consumer、接口或 DTO 时必须阅读。
11. `docs/ai-coding/TESTING_SPEC.md`：确认业务模块 SpringBootTest、真实集成测试、测试库和 AssertJ 边界。
12. `docs/ai-coding/PROJECT_CODING_SPEC.md`：确认微服务分层、RESTful、权限、多租户、数据权限和 DDL 规范。
13. `docs/ai-coding/AI_ENGINEERING_GUARDRAILS.md`：确认风险分级、Definition of Done 和交付门禁。
14. `docs/ai-coding/SECURITY_CODING_SPEC.md`：涉及检索接口、知识内容、数据隔离、脱敏、SQL、上传下载或测试安全时必须阅读。
15. `docs/ai-coding/UTILS_PUBLIC_SPEC.md`：涉及公共规范、错误码、数据库、乐观锁或 `utils` 能力时阅读。
16. `docs/ai-coding/NACOS_CONFIG_SPEC.md`：修改 Nacos 配置中心、共享 dataId 或 `application.yml` import 前必读。
17. 目标 Controller、Service、向量库配置、MCP / AI Registry 注册和真实 Nacos 模板：确认真实调用链，不按文件名猜行为。

## 项目边界

- `rag` 是**知识检索能力提供方**：输入查询，输出**带出处的检索上下文片段**。是否调用模型、是否下结论、是否保存会话由调用方（agent）负责，`rag` 不替调用方叫模型、不替调用方下结论。
- `rag` 以 **MCP 工具（如 `knowledge_search`）+ Nacos AI Registry AgentCard** 暴露能力；消费方通过 Nacos 发现，不硬编码地址。
- 向量库统一用 **Qdrant**；**摄取侧与查询侧必须使用同一 embedding 模型 + 同一维度**，维度必须等于 collection 向量维，变更即需重建 collection 并全量重嵌入。
- 摄取（解析/切片/embedding/写库）与检索（查询/向量检索/拼接）职责分离；摄取是离线/批量，未来可能外移（如 Python），检索在请求链路上。
- 敏感数据（如患者画像）归属与合规留在调用方控制面；`rag` 默认只承载可共享的知识，不持有患者主身份。
- 新增知识库、切片、向量任务或模型配置业务表时，必须按 `docs/ai-coding/PROJECT_CODING_SPEC.md` 补齐公共治理字段；`version` 只做乐观锁，业务版本使用 `*_version` 命名。
- 公共响应、认证上下文、多租户、错误码和工具能力优先复用 `../utils`；跨服务 Dubbo 契约复用 `../rpc-api`，不在本服务复制契约。
- 新增本服务 OpenAPI 入口、调整服务前缀，或需要经网关访问时，必须同步检查 `../gateway` 的 Nacos `gateway-spring.yaml`，并验证对应网关文档路径与 `/swagger-ui/index.html`。
- `application.yml` 只保留本地兜底配置；真实模型密钥、向量库密钥、数据库密码必须放 Nacos，模板只提交占位符。

## AI 工程门禁

- RAG 检索、知识摄取、embedding 调用、向量库读写、MCP 工具、AI Registry 注册、数据隔离默认中高风险。
- 新增或修改功能前，必须按 `AI_AUTOMATION_WORKFLOW.md` 整理需求说明、验收标准和开发手册。
- 完成后必须按 `docs/ai-coding/AI_ENGINEERING_GUARDRAILS.md` 做风险分级、Definition of Done、测试证据、安全检查、风险和回滚说明。
- 测试分层按 `docs/ai-coding/TESTING_SPEC.md` 执行；核心检索链路不能只靠 mock 或纯对象 `assertThat`，必须补 Spring Boot 级别测试（embedding 用 stub、向量库用 fake 或测试容器）。
- 修改 embedding / 向量库 / 检索时，必须保留「摄取与查询同模型同维度」「无命中不编造」「检索失败降级不阻断调用方」的边界。

## 多智能体协作规则

- 子智能体可并行分析 Controller、Service、向量库配置、MCP 注册、AI Registry 注册、gateway 路由和检索链路。
- 不允许多个 worker 同时修改同一核心检索 Service、向量库配置、MCP 契约或 DDL 脚本。
- RAG 服务全新或空业务库首次启动前，必须先在目标业务库手动执行 `../utils/src/main/resources/db/common-infra-schema.sql`；Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖应用首次启动自动创建该表。新增知识库业务 DDL 时追加独立 SQL 文件，不修改已执行的公共基础设施脚本。
- 最终检索归属、访问权限、向量库一致性和测试结论必须由主智能体统一判断。

## 验证命令

按风险选择验证：

```bash
./gradlew clean compileJava -x test
./gradlew test
bash scripts/check-secrets.sh
```

涉及 `rpc-api` 契约、embedding / 向量库、MCP / AI Registry 注册、Nacos / 网关路由时，还需说明契约编译、接口验证、向量库验证、Nacos 注册验证或依赖外部环境的未验证项。

## 禁止事项

- 禁止把检索结果当作绝对事实覆盖调用方的安全边界；命中必须可溯源，无命中不得编造。
- 禁止把 embedding 摄取与查询用成不同模型/不同维度。
- 禁止把模型密钥、向量库密钥、Nacos 密码、数据库密码写死在业务代码、测试资源、CI 或仓库（疑似密钥只能告警，由项目负责人处理）。
- 禁止把用户输入直接拼 SQL、文件路径、URL 目标或系统命令。
- 禁止在本服务复制 `utils` 公共工具源码或 `rpc-api` 契约源码。
- 禁止提交 `.DS_Store`、本机绝对路径、临时日志和无关构建产物。
