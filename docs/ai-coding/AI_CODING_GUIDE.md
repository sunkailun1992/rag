# AI 编码执行指南

## 使用方式

当用户要求新增或修改业务功能时，AI 应按以下顺序工作：

1. 阅读当前业务模块已有代码。
2. 阅读 `AI_AUTOMATION_WORKFLOW.md`，先形成需求说明、验收标准和开发手册。
3. 阅读 `AI_ENGINEERING_GUARDRAILS.md`，确认风险等级、必须测试、安全门禁、Review 门禁和交付说明。
4. 阅读 `AI_DIRECTORY_STRUCTURE_GUIDE.md`，确认 Java 微服务目录、测试、资源、文档和跨项目边界。
5. 阅读 `AI_DESIGN_PATTERN_GUIDE.md`，确认RAG 知识检索服务适用的设计模式和禁止过度抽象规则。
6. 阅读 `PROJECT_CODING_SPEC.md`。
7. 阅读 `UTILS_PUBLIC_SPEC.md`，确认公共规范、错误码、数据库、乐观锁、分支流程和注释要求。
8. 如果任务涉及当前微服务已有业务、接口、权限码、默认数据或启动配置，阅读项目根目录 `README.md`。
9. 参考 `examples/` 下的示例。
10. 如涉及公共规范原文、错误码完整表、团队流程或 `utils` 能力，阅读 `archive/utils-markdown/` 下对应文档。
11. 判断本次代码是否属于公共工具类、通用组件、基础配置或跨微服务复用能力；如果是，先切到同级 `utils` 项目检索已有实现。
12. `utils` 已有能力时优先复用，不要在当前微服务重复编写；确实缺失时再在 `utils` 实现并安装依赖。
13. 只有明确属于当前微服务业务边界的代码，才按当前项目现有结构创建 Java 文件。
14. 新增或修改 Controller 时，先按 `PROJECT_CODING_SPEC.md` 的 RESTful 接口规范设计路径和 HTTP 方法。
15. 新增或修改 Controller 时，补齐 `@Tag`、`@Operation`、必要的 `@Parameter` 和请求/响应对象 `@Schema`，确保 OpenAPI 文档展示业务名称而不是默认方法名。
16. 涉及 MySQL、Redis、RabbitMQ、Seata、XXL-JOB、Elasticsearch、Kibana、Zipkin 等基础设施地址时，优先读取项目根目录 `README.md` 的基础设施地址约定，不要在业务配置中新增裸 IP。
17. 新增或修改 README、AI 规范、配置、脚本、测试、示例和代码时，禁止写入个人电脑绝对路径；目录关系使用相对路径，外部安装位置使用环境变量或 `<PLACEHOLDER>` 占位符。
18. 代码编写完成后补充或更新 JUnit 5 测试用例；接口功能优先从 Controller 请求层覆盖请求参数、权限、统一响应和 Service 调用，再按风险补充 Service/Mapper 单元测试。
19. 检查项目根目录 `README.md`，已有内容时补充本次新增或调整的业务说明，缺少文件时新建。
20. 编译和测试验证，并按 `AI_ENGINEERING_GUARDRAILS.md` 输出风险、回滚和未验证项。

## 编码生成规则

- 业务编码必须由后端统一生成，不允许前端、脚本或测试数据各自拼接随机编码。
- 新增需要人工输入 `code` 的业务表单时，必须先检查同级 `utils` 或当前微服务是否已有可复用编码生成能力。
- 编码生成接口必须受当前业务管理权限保护，例如报告记录可使用 `report:record:create` 或更细的编码生成权限。
- 生成规则集中在当前业务服务类中，不要把编码格式散落在 Controller、前端或 SQL 脚本中。
- 生成目标必须覆盖当前微服务所有需要人工输入 `code` 的表单，例如报告记录、报告类型、报告任务等。
- 生成结果只能包含小写字母、数字、下划线和冒号等 ASCII 可读字符；必须带时间和随机后缀，降低并发生成碰撞概率。

## DDL 变更规则

- 修改 `src/main/resources/db/*.sql` 前，先检查 `MysqlDdl#getSqlFiles()` 和数据库 `ddl_history` 的执行记录。
- 全新或空业务库首次启动前，必须先在目标业务库手动执行 `../utils/src/main/resources/db/common-infra-schema.sql`；Seata AT 会在 `DataSource` 初始化时先检查 `undo_log`，不能依赖 MyBatis-Plus DDL 首次启动自动创建该表。
- 必须连接当前目标数据库查询 `ddl_history`；只有明确确认脚本未执行，才允许修改该脚本。
- 已发布、已执行、可能已执行、无法确认执行状态的脚本禁止改原文件；原始建表和基础数据脚本一旦执行，后续变更必须新增独立 SQL。
- 如果 `ddl_history` 已记录某个脚本，或无法连接数据库确认执行状态，后续表结构、默认数据、树状权限资源等变更必须新增独立 SQL 脚本，并按顺序追加到 `MysqlDdl#getSqlFiles()`。
- 新增 SQL 必须具备重复执行安全性，优先使用 `CREATE TABLE IF NOT EXISTS`、`INSERT IGNORE` 和带租户/主键条件的确定性 `UPDATE`。
- 新增字段不要默认使用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，当前 MySQL 环境可能不支持；需要用 `information_schema.COLUMNS` 判断后配合 `PREPARE/EXECUTE` 动态执行。
- AI 生成新业务表时必须按 `PROJECT_CODING_SPEC.md` 的公共治理字段清单补齐 DDL，不得只写业务字段；同步检查 Entity、BO、VO、Query、ServiceQuery、索引、唯一约束和 SQL 注释。
- 数据库列 `version` 只表示乐观锁；业务版本使用 `*_version` 命名，例如 `template_version`、`prompt_version`、`protocol_version`。
- 新增需要数据权限控制的业务主表时，默认同时设计 `owner_user_id` 和 `dept_id` 字段，并同步补齐 Entity、BO、VO、Query、ServiceQuery 与 Nacos `security.data-permission.table-rules`。
- `owner_user_id` 不是所有表的强制字段；租户表、资源表、字典表、初始化配置表和纯关系表没有明确负责人过滤语义时不要硬加。

`examples/` 是当前 AI 编码的主要参考，不是可编译源码目录。示例需要贴近当前分层规范：完整分层、统一方法命名、类/字段/方法/关键逻辑注释齐全。

## 多智能体协作规则

- 可以使用多个子智能体并行协作，但子智能体默认只能执行需求分析和项目学习，不直接修改代码。
- 大项目学习时，可以让多个 explorer 分别阅读前端、后端、数据库、配置和部署脚本；这类读多写少任务冲突小，收益高。
- 问题排查时，可以让多个 explorer 分别检查前端请求、后端接口、配置权限、数据库和日志；主智能体必须最后汇总证据并判断根因。
- 代码 Review 可以按安全风险、逻辑 bug、测试缺口、性能问题和可维护性拆分给多个 reviewer；结论必须由主智能体统一收口。
- 测试回归和日志分析可以并行：一个 agent 跑测试，一个 agent 分析失败日志，一个 agent 查最近改动；最终修复仍由主智能体或一个明确 worker 收口。
- 大功能可以拆给多个 worker 独立实现，但必须先划清写入边界，例如 worker A 只改前端页面、worker B 只改后端 API、worker C 只补测试。
- 如果多个 worker 需要修改同一个核心 Service、同一个 SQL 脚本、同一个公共工具类或同一份配置文件，不允许并行写入，必须改为主智能体串行处理。
- 子智能体输出应包含读取范围、关键发现、风险点和建议，不应直接给出未经主智能体验证的最终结论。

## 安全编码规则

安全细则单独维护在 `SECURITY_CODING_SPEC.md`。新增或修改接口、权限、报告内容、查询、文件、日志、上传下载、脱敏、测试时，必须先阅读该文件并按检查清单验证。

## 注释要求

AI 新增或修改 Java、SQL、YAML、脚本、测试和示例等编程内容时，必须遵守 `AI_COMMENT_STYLE_GUIDE.md`。

核心原则：

- 修改注释前先识别文件类型和框架上下文；规范未覆盖时，先查官方或主流规范并补充到注释规范文件。
- 优先让代码自解释，能用类名、方法名、BO/VO/Query 类型、报告状态常量和小方法表达的意图，不用注释补救。
- 注释解释长期维护需要知道的报告归属、访问权限、租户隔离、文件链路、MQ、SQL 迁移和失败策略。
- 禁止逐行翻译式注释，禁止用注释保留废弃实现、调试 main、临时 SQL 或整块旧代码。
- 注释必须保持缩进、对齐、换行和段落美观一致；不能为了补说明把 Java、XML、SQL 或 YAML 弄乱。

## 设计模式要求

AI 新增或重构 Java、SQL、配置、测试和示例结构前，必须遵守 `AI_DESIGN_PATTERN_GUIDE.md`。

核心原则：

- RAG 知识检索服务优先沿用 Controller、Service、Mapper、Entity、BO、Query、VO 分层。
- 报告类型、生成流程、解析流程、审核状态、文件访问、MQ 和通知通道出现稳定扩展点时，再使用 Strategy、Observer、State、Pipeline 或 Adapter。
- 管理侧、个人侧、异步任务侧和外部回调侧权限边界不能被抽象混淆。
- 不为了普通 CRUD 硬套 Factory、Manager、Abstract 层或过深继承。

## 禁止事项

- 不要把业务状态枚举加入 `EntityBase`。
- 不要返回裸 `Map` 作为统一响应。
- 不要在业务 SQL 中重复处理租户和逻辑删除。
- 不要把公共工具类、通用组件、基础配置或跨微服务复用能力直接写进业务微服务；编写前必须先检查同级 `utils` 是否已有实现，已有则复用，缺失才在 `utils` 维护。
- 不要新增 JUnit4、Spock、Groovy 测试；当前项目统一使用 JUnit 5。
- 不要只写脱离请求入口的转换类测试来代表接口功能正常；核心 Controller 接口必须优先按 `TESTING_SPEC.md` 用真实 HTTP 集成测试验证，MockMvc/WebMvcTest 只作为局部 slice 补充。
- 不要让普通测试默认依赖真实 MQ、Redis、Nacos、数据库或第三方服务；这些必须 mock、使用测试容器、测试 profile 或显式集成测试开关。
- 不要忽略 `UTILS_PUBLIC_SPEC.md` 中的乐观锁、枚举、错误码和数据库变更记录要求。
- 不要在 Nacos 业务配置或本地配置中新增散落的基础设施裸 IP；除连接 Nacos 自身的启动入口外，基础设施地址统一放入公共配置并通过变量引用。
- 不要为标准 CRUD 新增 `/save`、`/update`、`/remove`、`/select`、`/page` 等动词路径；标准 CRUD 必须优先使用 RESTful 资源路径和 HTTP 方法表达。
- 不要在仓库文件中写入个人电脑绝对路径、下载目录、IDE 路径、JDK 安装路径或本机仓库完整路径；本地私有路径放到环境变量、用户级 Gradle/Maven 配置、IDE 运行配置或未提交的本地配置中。

## 推荐生成顺序

新增一个普通业务模块时，按顺序创建：

1. `entity/enums/*Enum.java`
2. `entity/*Entity.java`
3. `entity/bo/*BO.java`
4. 按业务需要补充 `entity/bo/*BindBO.java`、`*GrantBO.java` 等专项写入对象
5. `entity/query/*Query.java`
6. `entity/vo/*VO.java`
7. `mapper/*Mapper.java`
8. `service/*Service.java`
9. `service/query/*ServiceQuery.java`
10. `service/results/*ServiceResults.java`
11. `service/impl/*ServiceImpl.java`
12. `controller/*Controller.java`

## 验证命令

```bash
./gradlew clean compileJava test
bash scripts/check-secrets.sh
```

如果依赖 `utils` 有调整，先在同级 `utils` 项目执行：

```bash
./gradlew publishToMavenLocal
```

再回到当前项目编译。
