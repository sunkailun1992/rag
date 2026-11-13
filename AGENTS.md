# AGENTS.md

本文件是 `rag` 服务的 AI 编码入口。AI 修改本项目代码前，必须先阅读本文件，再按任务风险阅读 `README.md` 和 `docs/ai-coding` 下的规范。

## Nacos 配置注释要求

- 新增或修改 Nacos 配置、模板、引导配置及渲染/发布工具前，必须阅读 [Nacos 注释与归类规范](docs/ai-coding/NACOS_COMMENT_SPEC.md)。
- 每个 dataId 写明职责、环境归属、消费者；每个可调参数写明用途、必要单位、格式、空值及开关影响。YAML 使用 `#` 注释，渲染结果必须保留说明。
- 注释整理保持键、类型、值、占位符和列表/import 顺序不变；发布前读回权威配置并备份，使用旧版本比对/CAS，发布后逐项读回。真实凭据不得进入 Git 或注释。
- 只处理明确授权的仓库及环境；Yudao 项目排除，不随本规范同步修改。

## 通用工程与 Git 交付约束

- 个人 GitHub 仅保留 `main` 长期分支，不再创建 `test/preprod`；短功能分支交付后清理。Git 分支不自动决定 profile/namespace。个人 dev 入口、`github-dev` 与公共库 `com` 坐标保持，不能复制公司环境或业务分支规则覆盖。
- 修改前先核仓库、HEAD、dirty、worktree 和其他会话，再 fetch；普通新目标从最新 origin/main 建短分支，同一任务续作和用户明确基线按现有授权处理。完整功能与必需跨仓配套一起验证交付。
- 本任务已授权合 main 并清理时，完整功能进入远端 main 且包含关系读回通过后清理本地和远程功能分支；未合、被其他 worktree 使用或仍有唯一工作时保留。保护 main 和未交付工作，不强推，不用强删绕过检查。细则见 `docs/ai-coding/BRANCHING_SPEC.md`。
- 高风险变更必须有独立风险验证，不能只依赖实现者的唯一自证测试；Review 可按需补充，不增加已授权提交/合并/推送的人工批准门禁。纯文档运行差异、路径和凭据检查，不机械重跑 Java 测试。
- 修改版本前读 `docs/ai-coding/VERSIONING_SPEC.md`：MINOR/PATCH 各为 0–9，`1.2.9 -> 1.3.0`、`1.9.9 -> 2.0.0`；先核远端、tag、在途分支和已发布制品占用，同批只升一次，纯规范不升业务版本，不覆盖旧 tag/同版制品。
- Git 集成、CI、远端制品、部署和实际验收分别取证；当前任务已给出的授权持续有效，未覆盖的外部发布动作仍需明确授权。
- 维护者本人，以及维护者本机用维护者账号运行的 Claude / Codex 会话，可以直接合并到 `main`：合并前本机验证命令全过，合并后看 CI，红了马上修。推送后读回精确提交的 CI 结论。
- 云端会话（`CLAUDE_CODE_REMOTE=true`，或会话里没有本机配置）只推会话分支，由维护者或本机会话合并。其他人的代码走 PR：CI 全绿、维护者审核批准、评审对话解决后再合并。代理不修改 ruleset 与仓库权限，除非用户当场明确要求。

## 项目定位

- 项目名称：`rag`
- 项目类型：RAG 知识检索服务后端；为 fleet / AI 中台提供「知识检索」能力，以 **MCP 工具 + Nacos AI Registry** 形式被智能体（如 `ai-agent` 及后续新中台 agent）发现并调用。
- 技术栈：Java 21、Spring Boot 4、Qdrant REST 向量库、独立 Python 3.12 引擎（FastAPI / LlamaIndex / pypdf）、Spring Cloud Alibaba、Nacos、Dubbo、MyBatis-Plus、Gradle、`com:utils`、`com:rpc-api`
- Utils 制品隔离：本 GitHub 个人项目固定使用 `com:utils:1.6.6`；公司项目使用 `com.kellen:utils`。二者共用私有 Maven 地址，以 group 区分制品；Java 包名 `com.kellen.*` 不随 Maven group 改动。
- 同级依赖：`../rpc-api` 提供跨服务 RPC 接口和 DTO 契约；`../utils` 提供公共响应、认证上下文、租户、多数据源和基础工具；`../gateway` 负责路由；`../ai-agent` 是当前主要 MCP / AI Registry 消费方
- 当前状态：已实现 XLSX/DOCX 结构化摄取、embedding、Qdrant 检索、rerank、版本/幂等、读回校验及图谱。本批增加独立 Python 解析/检索引擎、PDF/OCR 适配和评估基准；旧检索与图谱迁移开关已删除。混合检索已实现 LlamaIndex dense/sparse RRF 与显式迁移，必须实际验收后启用；MCP/AI Registry、查询改写和异步摄取仍未实施，不能按文件或依赖存在宣称已启用。
- 引擎改造入口：先读 `docs/rag-engine-upgrade.md`、`engine/README.md`、`engine/openapi.json` 与 `evaluation/README.md`；不得绕过 Java 权限门面直接将 Python API 暴露给前端。
- 语义图谱入口：先读 `docs/neo4j-graph-design.md`。固定采用 LlamaIndex PropertyGraph + Neo4j 派生索引，任务接收、构建完成和实际发布分别取证；Qdrant 原文与当前活动来源仍是权威。不得按实体名称全局合并，不得执行模型生成的 Cypher，不得把抽取失败变成空图成功或撤销正确的向量上传。
- 核心风险：检索结果污染模型上下文或越权读取知识、医疗/专业知识来源不可信、embedding 模型与维度在「摄取」与「查询」两侧不一致、向量库连接与降级、敏感数据（如患者画像）归属与合规、MCP / A2A 契约不稳

## 与现有 report 骨架的差异说明（重要）

- 本服务由 `report` 工程骨架复制而来。**通用规范**（注释风格、设计模式分层、测试分层、分支、版本、RPC、utils、Nacos 配置）直接适用，无需改动。
- `docs/ai-coding` 中的**深度业务规范**（`SECURITY_CODING_SPEC.md`、`AI_DIRECTORY_STRUCTURE_GUIDE.md`、`PROJECT_CODING_SPEC.md`、`AI_DESIGN_PATTERN_GUIDE.md` 等）目前仍**保留 report 的业务示例**（报告生成/导出/审核、`/report/manage` 接口等）。这些是继承自骨架的占位示例，**将随 Phase 2 rag 业务实现逐步细化为 RAG 领域**（知识库、文档、切片、embedding、检索、MCP）；不要把这些历史 report 示例当作 rag 的真实业务规则；实现事实以 README、源码与测试为准。

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

- `rag` 是**知识检索能力提供方**：输入查询，输出**带出处的检索上下文片段**。答案生成、下结论和保存会话由调用方（agent）负责；摄取、embedding、rerank 及显式启用的图谱抽取可调用所配置模型，但不得替调用方生成答案。
- 当前 `rag` 通过 HTTP 与 Dubbo 暴露能力；MCP 工具与 Nacos AI Registry AgentCard 属后续能力，不得按历史规划推定已实现。
- 向量库统一用 **Qdrant**；**摄取侧与查询侧必须使用同一 embedding 模型 + 同一维度**，维度必须等于 collection 向量维，变更即需重建 collection 并全量重嵌入。
- 摄取（解析/切片/embedding/写库）与检索（查询/向量检索/拼接）职责分离；摄取是离线/批量，未来可能外移（如 Python），检索在请求链路上。
- 敏感数据（如患者画像）归属与合规留在调用方控制面；`rag` 默认只承载可共享的知识，不持有患者主身份。
- 新增知识库、切片、向量任务或模型配置业务表时，必须按 `docs/ai-coding/PROJECT_CODING_SPEC.md` 补齐公共治理字段；`version` 只做乐观锁，业务版本使用 `*_version` 命名。
- 公共响应、认证上下文、多租户、错误码和工具能力优先复用 `../utils`；跨服务 Dubbo 契约复用 `../rpc-api`，不在本服务复制契约。
- 新增本服务 OpenAPI 入口、调整服务前缀，或需要经网关访问时，必须同步检查 `../gateway` 的 Nacos `gateway-spring.yaml`，并验证对应网关文档路径与 `/swagger-ui/index.html`。
- `application.yml` 只保留本地兜底配置；上游模型 provider 凭据由 LiteLLM 原生控制台管理，Java/Engine 各自仅从私有 OS 输入持有最小 scope virtual key；Qdrant、数据库等基础设施凭据仍由 Nacos 管理，Git 模板只提交占位符。源码接线不代表运行态已迁移，具体临时直连例外仅记私有运行回执。

## AI 工程门禁

- RAG 检索、知识摄取、embedding 调用、向量库读写、MCP 工具、AI Registry 注册、数据隔离默认中高风险。
- 新增或修改功能前，必须按 `AI_AUTOMATION_WORKFLOW.md` 整理需求说明、验收标准和开发手册。
- 完成后必须按 `docs/ai-coding/AI_ENGINEERING_GUARDRAILS.md` 做风险分级、Definition of Done、测试证据、安全检查、风险和回滚说明。
- 测试分层按 `docs/ai-coding/TESTING_SPEC.md` 执行；核心检索链路不能只靠 mock 或纯对象 `assertThat`，必须补 Spring Boot 级别测试（embedding 用 stub、向量库用 fake 或测试容器）。
- 修改 embedding / 向量库 / 检索时，必须保留「摄取与查询同模型同维度」「无命中不编造」「检索失败降级不阻断调用方」的边界。
- 测试必须稳定可重复，按 `docs/ai-coding/TESTING_SPEC.md`「测试稳定性」：不依赖时序、连接复用或执行顺序；偶发失败按缺陷处理，不用重试、跳过或删除测试掩盖。

## 多智能体协作规则

- 子智能体可并行分析 Controller、Service、向量库配置、MCP 注册、AI Registry 注册、gateway 路由和检索链路。
- 不允许多个 worker 同时修改同一核心检索 Service、向量库配置、MCP 契约或 DDL 脚本。
- RAG 服务全新或空业务库首次启动前，必须先在目标业务库手动执行 `../utils/src/main/resources/db/postgresql/common-infra-schema.sql`；Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖应用首次启动自动创建该表。新增知识库业务 DDL 时追加独立 SQL 文件，不修改已执行的公共基础设施脚本。
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
