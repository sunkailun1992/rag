# AI 目录管理规范

本规范约束 AI 在 `rag` RAG 知识检索服务中新增、移动、拆分和命名目录的方式。目录管理必须基于当前 Java 17、Spring Boot、Gradle、MyBatis-Plus、RabbitMQ 和 Nacos 微服务结构。

## 核心依据

- Gradle Java SourceSet：生产源码放 `src/main/java`，生产资源放 `src/main/resources`，测试源码放 `src/test/java`，测试资源放 `src/test/resources`。
- Spring Boot 包结构：主应用类位于根包下，业务组件位于根包子包内，避免默认包和扫描边界外的业务类。
- Java 包命名：包名小写，目录结构必须和 `package` 声明一致。
- MyBatis-Plus 分层：Controller、Service、Mapper、Entity、BO、Query、VO、ServiceQuery、ServiceResults 职责清晰。
- GitHub / AI 规范：CI 放 `.github/workflows/`，AI 规范放 `docs/ai-coding/`，根目录只保留 `AGENTS.md` 作为入口。

## 当前标准目录

```text
.
├── AGENTS.md
├── README.md
├── build.gradle
├── settings.gradle
├── gradle/
├── src/
├── docs/ai-coding/
├── scripts/
└── .github/
```

生产代码根包：

```text
src/main/java/com/kellen
```

后续报告业务包建议：

| 目录 | 职责 |
| --- | --- |
| `com/kellen/rag/controller` | RAG 知识检索服务 HTTP 接口入口。 |
| `com/kellen/rag/entity` | 报告、报告文件、生成任务等数据库实体和枚举。 |
| `com/kellen/rag/entity/bo` | 新增、修改、生成、审核、导出等写入请求对象。 |
| `com/kellen/rag/entity/query` | 查询请求对象。 |
| `com/kellen/rag/entity/vo` | 接口响应视图对象。 |
| `com/kellen/rag/mapper` | MyBatis-Plus Mapper 和 XML 对应接口。 |
| `com/kellen/rag/service` | Service 接口。 |
| `com/kellen/rag/service/impl` | Service 实现和报告生成、归档、导出等业务编排。 |
| `com/kellen/rag/service/query` | 查询服务入参和复杂读模型。 |
| `com/kellen/rag/service/results` | Service 层输出结果对象。 |
| `com/kellen/job` | 报告相关定时任务入口。 |
| `com/kellen/bean` | 当前服务本地基础对象；公共基础能力优先回到 `../utils`。 |

## 目录规则

- 新增报告能力优先放在 `com/kellen/rag` 现有分层下，不新建并行的 `dao`、`domain`、`manager`、`modules` 体系。
- 报告生成、解析、归档、导出、审核、文件访问和 MQ 处理等扩展应先复用现有 service / strategy / adapter 边界。
- 新增数据库表必须同步判断是否需要 `entity`、`mapper`、`service`、`service/impl`、`bo`、`query`、`vo` 和 DDL。
- 新增测试必须放 `src/test/java/com/kellen`，测试配置和 Mock 数据放 `src/test/resources`。
- SQL 初始化或迁移脚本按当前项目约定放 `src/main/resources/db`；不得提交本地导出的临时 SQL、含密钥 SQL 或生产数据 dump。
- 当前按技术分层组织包（controller/service/mapper/entity 等，package-by-layer）；当生成、解析、归档、导出、审核、文件访问等报告业务域在多个层目录中持续膨胀，且改动总是跨多个层目录联动时，才评估按业务特性分包（package-by-feature）。演进必须有真实维护痛点，不为小规模代码强行切换，并同步 Spring 组件扫描、MyBatis 扫描、测试和文档。
- AI 规范统一放 `docs/ai-coding/`；根目录不再新增 `AI_*.md`、`*_SPEC.md` 或临时分析文档。
- 当前仓库不得嵌套 `utils`、`user`、`gateway`、`admin-web`、`ai` 等同级项目副本；跨项目修改必须切换到真实同级仓库。
- 构建产物、日志、IDE 文件、本机模块文件和系统文件不得提交，例如 `build/`、`.gradle/`、`logs/`、`.idea/`、`*.iml`、`.DS_Store`。

## 变更流程

1. 先判断文件属于源码、测试、资源、文档、脚本、CI 还是工具配置。
2. 查找现有同类目录，优先复用，不新增平行体系。
3. 移动 Java 文件时同步 `package`、import、测试、README 和 AI 规范引用。
4. 目录移动和行为修改尽量分开，避免 Review 时混淆。
5. 执行 `git diff --check`，涉及 Java 目录或 package 变化时执行 `./gradlew clean compileJava -x test`。

## 检查清单

- 是否符合 Java / Spring Boot / Gradle 主流目录约定？
- 是否保持 `com/kellen/rag` RAG 知识检索服务边界清晰？
- 是否避免把测试、临时脚本、构建产物或日志放进生产目录？
- 是否没有嵌套同级项目副本？
- 是否没有移动或替换已有密钥、RabbitMQ 地址、Nacos 地址、数据库连接和生产配置？
