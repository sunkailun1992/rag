# AI 编码规范入口

本目录是 AI 编码规范入口。AI 新增或修改代码时，先读主规范，再按项目现有代码实现；迁移资料只作为归档参考。

## 快速阅读

1. 先读 `AI_CODING_GUIDE.md`，确认执行步骤和禁止事项。
2. 再读 `AI_DIRECTORY_STRUCTURE_GUIDE.md`，确认 Java 微服务目录、测试、资源、文档和跨项目边界。
3. 再读 `AI_DESIGN_PATTERN_GUIDE.md`，确认RAG 知识检索服务适用的设计模式和禁止过度抽象规则。
4. 再读 `AI_AUTOMATION_WORKFLOW.md`，按需求说明、验收标准、开发手册、测试说明和交付说明组织自动化开发。
5. 再读 `AI_ENGINEERING_GUARDRAILS.md`，确认风险分级、Definition of Done、测试门禁、安全门禁和交付说明。
6. 再读 `BRANCHING_SPEC.md`，确认分支命名、短分支生命周期、release/hotfix、tag 和清理规则。
7. 再读 `ENVIRONMENT_CONFIG_SPEC.md`，确认环境、Nacos namespace、Java profile 和前端/小程序边界。
8. 再读 `VERSIONING_SPEC.md`，确认 `group = 'com'`、`version = '1.0.0'`、补丁递增和公共包消费者同步规则。
9. 再读 `RPC_API_CODING_SPEC.md`，确认 Dubbo RPC 契约、provider/consumer、`../rpc-api` 和 `../utils` 边界。
10. 再读 `TESTING_SPEC.md`，确认业务模块 SpringBootTest、真实 HTTP 集成测试、测试库和 AssertJ 边界。
11. 再读 `PROJECT_CODING_SPEC.md`，确认通用分层、返回值、权限、多租户、注释和检查清单。
12. 涉及检索接口、权限、知识内容、数据隔离、脱敏、上传下载、SQL、XSS 或测试安全场景时，读 `SECURITY_CODING_SPEC.md`。
13. 涉及错误码、乐观锁、数据库变更、分支流程时，读 `UTILS_PUBLIC_SPEC.md`。
14. 当前微服务业务改造时，读项目根目录 `README.md`。
15. 新增业务模块时参考 `examples/`；该目录是从 `utils/docs/ai-coding/examples` 同步的本地副本。
16. 需要追溯原始迁移资料时再看 `archive/`。
17. 涉及公共工具类、通用组件、基础配置、跨微服务复用能力时，先检查同级 `utils` 项目是否已有能力；已有则直接复用，缺失时再到 `utils` 实现，并在当前微服务升级依赖和调用。
18. 涉及跨服务 Dubbo RPC 接口或 DTO 时，先检查同级 `rpc-api` 项目是否已有契约；缺失时先改 `rpc-api`，再升级当前服务依赖和调用点。

## 目录结构

```text
docs/ai-coding/
  README.md
  AI_CODING_GUIDE.md
  AI_DIRECTORY_STRUCTURE_GUIDE.md
  AI_DESIGN_PATTERN_GUIDE.md
  AI_AUTOMATION_WORKFLOW.md
  AI_ENGINEERING_GUARDRAILS.md
  BRANCHING_SPEC.md
  ENVIRONMENT_CONFIG_SPEC.md
  VERSIONING_SPEC.md
  RPC_API_CODING_SPEC.md
  TESTING_SPEC.md
  PROJECT_CODING_SPEC.md
  SECURITY_CODING_SPEC.md
  UTILS_PUBLIC_SPEC.md
  examples/
    ExampleBO.java
    ExampleEntity.java
    ExampleQuery.java
    ExampleVO.java
    ExampleMapper.java
    ExampleController.java
    ExampleService.java
    ExampleServiceImpl.java
    ExampleServiceQuery.java
    ExampleServiceResults.java
    ExampleStateEnum.java
  archive/
    utils-markdown/
      Announcement.md
      ErrorCode.md
      Specification.md
```

## 必读结论

- Controller 返回统一使用 `com.kellen.utils.ApiResponse`。
- Controller 接口严格优先使用 RESTful 风格：资源路径用复数名词，`GET` 查询、`POST` 新增、`PUT` 修改、`DELETE` 删除，标准 CRUD 不使用 `/save`、`/update`、`/remove`、`/select`、`/page` 等动词路径。
- Controller 类必须添加 `@Tag`，方法必须添加 `@Operation`，避免 OpenAPI 展示默认 `xxx-controller`、`list_1`、`save_1` 等不可读名称。
- 实体公共字段继承 `com.kellen.bean.EntityBase`。
- `type/state` 等业务状态字段由具体业务模块定义 `IEnum`，不要塞进 `EntityBase`。
- 多租户和逻辑删除由框架处理，业务查询不要重复拼 `tenant_id` 或 `is_delete = 0`。
- 需要数据权限控制的业务主表默认设计 `owner_user_id` 和 `dept_id`；纯关系表、租户表、资源表等没有负责人过滤语义时不要硬加。
- 权限接口使用 `@PreAuthorize("hasAuthority('权限码')")`。
- 安全规则独立维护在 `SECURITY_CODING_SPEC.md`，新增或修改接口时必须同步检查接口鉴权、数据脱敏、水平越权、租户隔离、文件遍历、退出清理 token、XSS 跨站脚本、SQL 注入、文件上传校验、CSRF、SSRF、限流资源消耗、加密密钥、批量赋值、字段级授权、供应链、配置安全、异常失败关闭、安全日志告警和安全测试。
- `examples/` 示例按当前分层规范编写，类、字段、方法和关键逻辑都保留注释，AI 写代码时优先模仿该风格。
- `examples/` 是公共示例模板的本地副本，源头在同级 `../utils/docs/ai-coding/examples`；公共模板变更必须先改 `utils`，再同步到当前项目。
- AI 新增或修改 Java、SQL、配置、脚本、测试和示例等编程内容时，必须先阅读 `AI_COMMENT_STYLE_GUIDE.md`。
- 注释优先解释检索归属、访问权限、租户隔离、向量库与 embedding 维度一致性、敏感数据边界和失败降级策略；优先让代码自解释，禁止机械逐行、行尾堆叠和注释掉的死代码。
- AI 新增或重构知识检索、摄取、切片、embedding、向量库访问、MCP 工具和 AI Registry 注册前，必须阅读 `AI_DESIGN_PATTERN_GUIDE.md`。
- AI 新增或修改 README、AI 规范、配置、脚本、测试、示例和代码时，禁止写入个人电脑绝对路径、本机下载目录、本机 JDK 路径或本机仓库完整路径；需要表达目录关系时使用相对路径、环境变量或 `<PLACEHOLDER>` 占位符。
- 分支命名、短分支生命周期、release/hotfix、tag 和分支清理按 `BRANCHING_SPEC.md` 处理。
- 环境、Nacos namespace、Java profile 和前端/小程序边界按 `ENVIRONMENT_CONFIG_SPEC.md` 处理。
- 项目版本、公共包依赖和后端 Java 新项目基础坐标按 `VERSIONING_SPEC.md` 处理；纯 AI 规范或 README 改动不提升制品版本。
- Dubbo RPC 契约统一维护在同级 `../rpc-api`；`rag` 作为 provider 或 consumer 时都不在 `utils` 或本服务重复定义跨服务接口/DTO。
- AI 开始功能开发前必须按 `AI_AUTOMATION_WORKFLOW.md` 先整理需求说明、验收标准和开发手册；如果用户需求很小，可以在回复中简化呈现，但内部检查项不能跳过。
- AI 完成功能后必须按 `AI_ENGINEERING_GUARDRAILS.md` 做风险分级、Definition of Done、测试证据、安全检查、风险和回滚说明。
- AI 新增或修改业务代码时，必须按 `TESTING_SPEC.md` 同步补充 JUnit 5 测试；核心接口优先使用 `@SpringBootTest(webEnvironment = RANDOM_PORT)` 发真实 HTTP 请求，MockMvc/WebMvcTest 只作为 slice 补充；核心 Service 使用 `@SpringBootTest` 注入真实 Bean；`assertThat` 只是断言工具，不能替代真实业务链路验证。
- AI 自动化编写完功能代码后，必须同步检查项目根目录 `README.md`；已有则补充本次业务说明，没有则新建。
- 公共工具类、通用组件、基础能力不得直接写进业务微服务；编写前先检查同级 `utils` 项目，优先复用已有能力，减少当前微服务代码量。
- 数据权限属于公共 MyBatis-Plus 能力，统一在 `utils` 中维护；业务微服务只维护部门、角色数据范围和具体业务表字段。
- `UTILS_PUBLIC_SPEC.md` 是公共规范的 AI 阅读入口；`archive/` 只保留迁移资料，不作为默认阅读内容。

## 复制规则

- 复制到其他微服务时，可以复用本目录下的 `README.md`、`AI_CODING_GUIDE.md`、`AI_DESIGN_PATTERN_GUIDE.md`、`AI_AUTOMATION_WORKFLOW.md`、`AI_ENGINEERING_GUARDRAILS.md`、`PROJECT_CODING_SPEC.md`、`SECURITY_CODING_SPEC.md`、`UTILS_PUBLIC_SPEC.md`、`examples/`。
- 复制或修改 `examples/` 时，必须以 `utils/docs/ai-coding/examples` 为唯一公共源头；当前项目只保留方便 AI 阅读的本地副本。
- 当前微服务上下文写在项目根目录 `README.md`，复制规范到其他微服务时不要把当前服务 README 当成通用规范。
- `PROJECT_CODING_SPEC.md` 不写具体业务接口、默认账号、当前服务权限码等服务私有信息。
