# 微服务 AI 编码规范

## 目标

本规范是可复制到多个微服务的通用编码规则。AI 编写代码时，应根据本文件和 `examples/` 下的示例直接创建或修改 Java 文件。

公共团队规则、错误码、数据库变更和乐观锁规则见 `UTILS_PUBLIC_SPEC.md`。归档原文见 `archive/utils-markdown/`，默认不需要阅读。

当前微服务的接口、权限码、默认数据、业务边界和特殊约束写在项目根目录 `README.md`，不要写进本通用规范。

## Utils 边界规范

公共工具类、通用组件、基础配置和跨微服务复用能力统一放在同级 `utils` 项目维护，业务微服务只通过依赖引用。AI 编写代码前必须先检查 `utils` 是否已有可复用能力，优先复用已有工具，减少当前业务微服务代码量。

需要进入 `utils` 编写的场景包括：

- 通用返回对象、异常处理、错误码、认证安全、租户上下文、JWT、Redis、HTTP、JSON、对象转换、日期时间、加密解密等公共工具。
- MyBatis-Plus 公共配置、自动填充、乐观锁、租户插件、逻辑删除、DDL 公共能力等基础组件。
- MyBatis-Plus 数据权限插件、数据权限上下文、数据范围解析等跨微服务访问控制能力。
- AOP、幂等、防重复提交、请求日志、动态数据源、SQL 参数校验等跨业务服务复用能力。
- 多个微服务都会使用，或未来明显会复用的工具方法、注解、配置类、基础抽象。

规则：

- AI 不得因为当前业务项目临时缺少工具类，就直接在当前微服务新增 `utils`、`common`、`helper` 等公共包。
- 确认属于公共能力时，先检索同级 `utils` 项目已有实现；已有能力直接调用，不重复编写。
- `utils` 确实缺少能力时，才修改同级 `utils` 项目，执行 `mvn -q -DskipTests install`，再回到当前微服务升级或确认 `com:utils` 依赖版本。
- 业务微服务只保留当前业务专属代码，例如 Controller、Service、Mapper、Entity、BO、Query、VO、业务枚举和业务 SQL。
- 如果无法判断是否应进入 `utils`，先按“是否会被两个以上微服务复用”判断；会复用则进入 `utils`，不会复用才留在当前微服务。

## 公共 examples 同步规范

`docs/ai-coding/examples` 是公共模板在当前微服务的本地副本，方便 AI 在只读取当前项目时也能学习标准分层、注释和代码风格。

规则：

- 公共 `Example*` 模板的唯一源头是同级 `../utils/docs/ai-coding/examples`。
- 修改公共模板时，必须先修改 `utils` 源头，再同步到 `user`、`rag` 等业务微服务本地副本。
- 当前项目不得单独长期修改公共 `Example*` 模板，避免不同微服务的 AI 参考样例漂移。
- 当前项目确实需要RAG 知识检索服务专属示例时，新增 `docs/ai-coding/project-examples` 或 `docs/ai-coding/business-examples`，不要放进公共 `examples`。
- 同步 examples 后，必须检查 `README.md`、`PROJECT_CODING_SPEC.md` 和 `examples/README.md`，确认同步规则仍然清晰。

## 技术基线

- Java 17
- Spring Boot 4.0.4
- Spring Cloud 2025.1.1
- Spring Cloud Alibaba 2025.1.0.0
- Nacos Client 3.2.2
- Seata Server 2.7.0
- Seata Client 2.6.0
- MyBatis-Plus 3.5.15
- 包名跟随当前微服务已有基础包结构。
- Servlet 和 Validation 使用 `jakarta.*`

## 配置中心规范

基础设施地址必须集中维护，避免蒲公英、Tailscale、节点小宝等组网地址变化时散落修改。

规则：

- 连接 Nacos 自身所需的通用骨架保留在本地 `application.yml`；Nacos 地址、namespace、config/discovery 绑定和 `spring.config.import` 必须放在 `application-dev/test/prod.yml` 对应环境文件中。
- MySQL、Redis、RabbitMQ、Seata、XXL-JOB、Elasticsearch、Kibana、Zipkin 等基础设施地址优先放入 Nacos 公共配置，例如 `reuse-configuration.yaml`。
- 业务配置只引用公共变量，例如 `${custom.infra-host}`、`${custom.infra-mysql-addr}`、`${custom.infra-xxl-job-admin}`、`${custom.local-service-host}`。
- 不要在多个 `*.yaml` 配置文件中直接写同一个基础设施 IP。
- 需要区分服务端地址和本机回调地址时，使用不同变量；例如 XXL-JOB Admin 地址使用基础设施地址，执行器回调 IP 使用当前服务所在机器地址。
- 如果当前微服务复制到其他项目，只调整公共配置中的基础设施变量，不批量替换各业务配置文件。

## 路径与本机环境规范

- README、AI 规范、YAML、properties、SQL、脚本、测试、示例和 Java 代码中不得写入个人电脑绝对路径、下载目录、IDE 路径、JDK 安装路径或本机仓库完整路径。
- 需要描述同级仓库时，使用 `../utils`、`../gateway`、`../user`、`../message`、`../report` 这类相对路径，不使用开发者机器上的完整目录。
- 需要描述可变安装目录、日志目录、上传目录、导出目录、临时目录或 JDK 路径时，使用环境变量、Nacos 配置、`~` 用户目录、`${user.home}`、`${java.io.tmpdir}` 或 `<PLACEHOLDER>` 占位符。
- 只有运行时从配置读取的业务目录可以进入代码；临时本地调试路径不得提交到仓库，调试样例应改为单元测试或文档占位符。
- 提交前必须使用 `rg` 搜索本机用户名、用户目录、仓库根目录和系统盘路径关键字，检查是否残留本机路径。

## 标准分层

新业务模块建议使用以下结构：

```text
<base-package>.<module>
  controller
  dto
  entity
    bo
    vo
    enums
  mapper
  service
    query
    results
    impl
```

复杂查询和结果转换必须按职责补齐：

```text
<base-package>.<module>.service.query
<base-package>.<module>.service.results
<base-package>.<module>.service.impl
```

## 请求对象拆分规范

Controller 入参必须按接口语义保持清晰，不要把查询条件和写入参数混用。

推荐命名：

```text
XxxBO
XxxQuery
XxxBindRoleBO
XxxBindResourceBO
```

规则：

- 简单 CRUD 可以使用一个 `XxxBO`，通过 `Save`、`Update` 校验分组区分新增和修改；标准删除优先使用路径 `id`，不要为了删除强制创建删除 BO。
- 新增、修改字段差异很大，或授权、绑定、批量删除等语义独立时，再拆成 `XxxSaveBO`、`XxxUpdateBO`、`XxxBindBO`、`XxxBatchRemoveBO` 等专项对象。
- 修改入参必须包含数据库旧 `version`，用于 MyBatis-Plus 乐观锁。
- 删除优先使用路径参数 `/{id}` 定位资源；只有删除还必须携带复杂业务条件时，才增加专项删除对象或专项动作接口。
- 查询条件使用 `Query`，不要和写入 BO 混用。
- GET 列表和分页查询使用 Query 对象承接 URL 查询参数，并使用 Springdoc `@ParameterObject` 展开 OpenAPI 参数；不要给 GET 查询接口添加 `@RequestBody`。
- Query 中 `current`、`size` 这类分页字段只使用 `@Min` 校验取值范围，不使用 `@NotNull(groups = Select.class)`；OpenAPI 会忽略校验分组并把复用同一 Query 的 `/options` 接口也错误标记为必填。
- 普通查询和分页统一使用 `GET` URL 参数；普通 CRUD 分页不使用 `POST /page`。
- 响应对象使用 `VO`，不要直接把包含密码等敏感字段的 Entity 返回给前端。
- Controller 只接收请求对象、调用 Service、组装 `ApiResponse`，不写业务规则、不写 SQL、不写初始化数据。
- Controller 必须按业务资源拆分，例如租户、用户、角色、资源、授权关系分别建 Controller，不要把多个资源维护接口塞进一个 `ManageController`。
- Service 必须按业务资源拆分，例如登录认证、基础数据、核心业务对象、资源对象、授权关系分别建 Service，不要把多个资源的逻辑塞进一个 `XxxService` 或 `XxxManageService`。
- Service 负责业务编排、事务、鉴权上下文、租户上下文和 Mapper 调用。
- ServiceQuery 负责 `QueryWrapper` 查询条件、排序、显示字段、通用关键字等查询增强。
- ServiceResults 负责 DO 转 VO、分页转换、枚举说明、关联信息补全等结果增强。
- Mapper 只负责数据访问，普通 CRUD 优先使用 MyBatis-Plus。

## RESTful 接口规范

Controller 必须优先使用严格 RESTful 风格，路径表达资源，HTTP 方法表达动作。

基础资源接口：

```text
GET    /<resources>              分页查询，Query 参数通过 URL 传递
GET    /<resources>/{id}         查询单个资源详情
POST   /<resources>              新增资源，body 使用 BO
PUT    /<resources>/{id}         修改资源，body 使用 BO，路径 id 与 body id 必须一致或由 Service 校验
PATCH  /<resources>/{id}         局部修改，可选；没有明确局部更新需求时不创建
DELETE /<resources>/{id}         删除资源，不使用 `POST /remove`
```

集合辅助接口：

```text
GET    /<resources>/options      非分页轻量选择项列表，用于下拉、树、授权回显等选择场景
GET    /<resources>/count        按 Query 条件统计数量；只有页面确实需要独立统计时创建
```

规则：

- 路径使用复数名词，例如 `/report/manage/templates`、`/report/manage/records`。
- 不使用动词路径表达标准 CRUD，例如 `/save`、`/update`、`/remove`、`/select`、`/page`。
- 查询使用 `GET`，分页通过集合资源表达，例如 `GET /report/manage/templates?tenantId=100&current=1&size=10`。
- 分页接口使用 `@GetMapping(params = {"current", "size"})` 表达分页参数存在性；`current`、`size` 缺失时不进入分页接口，不依赖 `@NotNull` 让 OpenAPI 标记必填。
- 如果同一个资源既需要分页管理列表，又需要非分页轻量列表，不要用两个相同的 `GET /<resources>` 依赖 `params` 区分；OpenAPI/OpenAPI 无法稳定展示同一 path + method 的两个操作。非分页轻量列表统一使用 `GET /<resources>/options`。
- `/options`、`/count` 是集合辅助接口，不是每个资源必写；只在前端页面或业务流程明确需要时创建。
- `options` 表示当前资源的轻量选择项集合，适合下拉框、树选择器、授权回显等场景；如果接口返回完整管理列表，应使用分页 `GET /<resources>`，不要滥用 `/options`。
- 修改和删除时，资源 ID 放在 path 里，例如 `PUT /report/manage/templates/{id}`、`DELETE /report/manage/templates/{id}`。
- 写入 body 不重复表达 path 已经表达的动作；body 只承接资源字段和乐观锁 `version`。
- 当前租户优先从请求头、登录上下文或路径父资源中获取；不要在所有 body 中机械重复租户字段。
- 授权、绑定、生成、导出、回调等关系动作按子资源表达，例如 `POST /report/manage/records/{recordId}/export-tasks` 创建报告导出任务。
- 无法自然映射为资源 CRUD 的业务动作，也要优先转成资源创建或子资源维护，例如生成报告使用 `POST /report/manage/generation-tasks` 创建生成任务。
- 旧接口改造为 RESTful 时，必须同步更新 Controller、Service 入参、权限资源初始化 SQL、前端调用、OpenAPI 文档、README 和测试用例。

## DDL 维护规范

数据库结构使用 MyBatis-Plus 自动维护 DDL，不在 Controller 或 Service 中执行 `CREATE TABLE`。

项目 DDL 入口：

```text
com.kellen.bean.MysqlDdl
```

SQL 脚本位置：

```text
src/main/resources/db/*.sql
```

规则：

- 全新或空业务库首次启动前，必须先在目标业务库手动执行 `../utils/src/main/resources/db/common-infra-schema.sql`。Seata AT 的 `DataSourceProxy` 会早于 MyBatis-Plus DDL 检查 `undo_log`，所以不能指望应用首次启动自动建出公共基础表。
- 修改 `src/main/resources/db/*.sql` 前，必须先查看 `MysqlDdl#getSqlFiles()` 确认脚本列表和执行顺序。
- 修改任意已存在 SQL 脚本前，必须连接当前目标数据库查询 `ddl_history`，确认该脚本是否已经执行。
- 如果 `ddl_history` 已存在该脚本记录，禁止继续修改该 SQL 文件；表结构、默认数据、权限资源树等后续变更必须新增独立 SQL 脚本。
- 如果无法连接数据库、无法确认 `ddl_history`、或环境不明确，必须按“脚本可能已执行”处理，只能新增 SQL 脚本，不能修改历史 SQL。
- 只有明确确认 `ddl_history` 没有该脚本记录时，才允许修改该未执行脚本。
- 原始建表和基础数据脚本一旦在任何环境执行过，后续变更只能新增脚本，不允许把新变更继续塞回原脚本。
- 新增或修改表结构、默认数据、权限资源树时，新增独立 SQL 脚本并追加到 `MysqlDdl#getSqlFiles()`。
- `MysqlDdl#getSqlFiles()` 统一声明脚本路径。
- 基础字典、默认角色、默认权限资源等初始化数据也写入 DDL SQL 脚本。
- SQL 脚本必须支持重复执行，使用 `IF NOT EXISTS`、`INSERT IGNORE` 或具备明确租户/主键条件的确定性 `UPDATE`。
- MySQL 环境不一定支持 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`；新增字段需要兼容旧 MySQL 时，使用 `information_schema.COLUMNS` 判断字段是否存在，再通过 `PREPARE/EXECUTE` 执行 `ALTER TABLE`。
- 不再为基础数据新增业务初始化接口，避免启动后还需要人工调用初始化接口。
- 表结构必须包含 `version`，并由实体继承 `EntityBase.@Version`。
- 多租户业务表必须包含 `tenant_id`，业务 SQL 不手写租户条件。
- 需要参与数据权限的业务主表默认包含 `owner_user_id` 和 `dept_id`：`owner_user_id` 表示数据负责人或归属用户，`dept_id` 表示数据归属部门。
- `owner_user_id` 不要求无脑加到所有表；租户表、权限资源表、字典表、登录/初始化类配置表、纯关系表通常不加，除非业务明确需要按负责人过滤。
- 关系表优先通过主表做权限控制，例如用户角色、角色资源、角色数据范围这类关系表不要为了统一字段而强行补 `owner_user_id`。

## 公共字段

数据库表默认包含这些公共字段：

```sql
code varchar(255) DEFAULT NULL COMMENT '编码',
description varchar(255) DEFAULT NULL COMMENT '说明',
create_date_time datetime DEFAULT NULL COMMENT '创建时间',
create_name varchar(255) DEFAULT NULL COMMENT '创建人',
modify_date_time datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
modify_name varchar(255) DEFAULT NULL COMMENT '修改人',
is_delete bit(1) DEFAULT b'0' COMMENT '删除状态',
label varchar(255) DEFAULT NULL COMMENT '标签',
sorting int DEFAULT '0' COMMENT '排序',
version int DEFAULT '1' COMMENT '版本号',
tenant_id varchar(64) NOT NULL DEFAULT '1' COMMENT '租户id'
```

这些字段由 `utils` 中的 `com.kellen.bean.EntityBase` 承接。实体默认继承：

```java
public class Xxx extends EntityBase {
}
```

`type` 和 `state` 不放入 `EntityBase`。如果某张表需要 `type/state`，由业务模块自己定义字段和枚举。

数据归属字段：

```sql
owner_user_id varchar(64) DEFAULT NULL COMMENT '负责人用户ID',
dept_id varchar(64) DEFAULT NULL COMMENT '归属部门ID'
```

`owner_user_id` 和 `dept_id` 不属于所有表的硬性公共字段，只有需要数据权限过滤的业务主表才默认添加。本人数据优先使用 `owner_user_id`；部门数据使用 `dept_id`。如果表自身没有负责人语义，例如系统级模板、字典、纯配置表，不要为了统一字段强行添加。

## 枚举规范

业务枚举放在当前模块的 `entity.enums` 包中，并实现 `IEnum`。

整数枚举：

```java
public enum XxxStateEnum implements IEnum<Integer> {
    默认(0, "默认");

    private final Integer value;
    private final String desc;
}
```

字符串枚举：

```java
public enum XxxCategoryEnum implements IEnum<String> {
    FRONTEND("FRONTEND", "前端资源");

    private final String value;
    private final String desc;
}
```

禁止把业务枚举放进 `EntityBase`。

## 实体规范

实体使用 MyBatis-Plus：

```java
@Getter
@Setter
@TableName("xxx_table")
@Schema(description = "xxx对象")
public class Xxx extends EntityBase {
}
```

字段注释使用 `@Schema(description = "...")`。

如果字段是数据库枚举，Java 类型使用对应枚举，而不是裸 `Integer/String`。

## Mapper 规范

Mapper 继承 `BaseMapper`：

```java
public interface XxxMapper extends BaseMapper<Xxx> {
}
```

除非确实需要复杂 SQL，否则优先使用 MyBatis-Plus 条件构造器。

## Service 规范

Service 负责业务编排，Controller 不直接堆业务逻辑。

写操作需要事务：

```java
@Transactional(rollbackFor = Exception.class)
```

对象转换优先使用项目已有工具：

```java
GeneralConvertor.convertor(source, Target.class)
```

先查询后修改的业务必须带上数据库记录的 `version` 字段，由 MyBatis-Plus `@Version` 和 `OptimisticLockerInnerInterceptor` 校验并发写入，不能绕过版本号直接更新。

更新示例优先使用：

```java
int count = mapper.updateById(entity);
```

要求：

- BO 修改入参必须包含查询得到的旧 `version`。
- VO 查询响应必须返回当前 `version`，方便前端修改时原样提交。
- 不要用只按 `id` 拼接的 `UpdateWrapper` 替代 `updateById(entity)`，否则 AI 容易漏掉乐观锁旧版本号。
- 如确实使用 `update(entity, wrapper)`，必须保证实体中有旧 `version`，且 wrapper 不复用。

## 测试规范

项目统一使用 JUnit 5 编写测试，不再新增 JUnit4、Spock 或 Groovy 测试。

依赖基线：

```gradle
testImplementation 'org.springframework.boot:spring-boot-starter-test'
testImplementation 'org.springframework.security:spring-security-test'
test {
    useJUnitPlatform()
}
```

规则：

- 新增或修改业务代码时，必须同步新增或更新测试用例。
- 测试类放在 `src/test/java`，包名与被测类保持一致。
- 测试类命名使用 `XxxTest`；集成测试可使用 `XxxIntegrationTest`，但不得默认依赖未准备好的外部服务。
- 测试分层以 `TESTING_SPEC.md` 为准；核心业务链路必须有 Spring Boot 级别测试，不能只靠纯对象断言或 mock。
- 核心接口功能测试优先使用 `@SpringBootTest(webEnvironment = RANDOM_PORT)` 发真实 HTTP 请求，验证请求参数、权限、统一响应、错误场景和 Controller 到 Service 的参数传递。
- `MockMvc`、`@WebMvcTest` 可以用于局部 Controller slice 验证，但只能作为补充，不能替代核心接口的真实 HTTP 集成测试。
- 纯转换类或工具类测试只能作为补充，不能替代接口请求层测试。
- 普通单元测试使用 JUnit 5、Mockito、AssertJ，不启动完整 Spring 容器。
- 核心 Service 测试优先使用 `@SpringBootTest` 注入真实 Spring Bean，验证业务分支、事务边界、租户上下文、Mapper、乐观锁版本号和异常路径。
- 外部客户端、Redis、MQ、Nacos、第三方服务可使用 stub、fake、mock、测试容器或显式开关隔离，避免测试不可重复。
- 外部依赖测试必须使用 test profile、mock、测试容器或显式开关，不能默认要求本机存在 MQ、Redis、Nacos、数据库等服务。
- 测试方法名表达业务语义，推荐 `shouldXxxWhenYyy` 风格。
- 断言必须验证关键输出和副作用，不允许只调用方法但没有断言。
- 新增代码涉及权限、多租户、乐观锁、异常处理、返回值转换时，测试必须覆盖至少一个正向场景和一个失败/边界场景。

## 注释规范

AI 编程产出的代码、SQL、配置、脚本、测试、前端调用示例等内容必须保留有效注释，但不采用机械逐行或行尾堆叠注释：

- 类必须有 JavaDoc，说明业务对象或组件职责。
- 字段必须有业务注释；实体字段优先使用 `@Schema(description = "...")`，必要时补充 JavaDoc。
- 公开方法和关键业务方法必须有 JavaDoc，说明用途、关键参数、返回值和报告/租户/权限副作用。
- 方法参数必须说明业务含义；不能只写“参数”或重复变量名。
- 认证授权、租户、权限、MQ、报告内容渲染、数据库乐观锁、SQL 参数校验、事务、异常处理、返回值组装等关键逻辑必须在逻辑块前说明“为什么这样做”和“守住什么边界”。
- 注释要解释业务目的、报告可见性、风险边界和框架衔接，不要只翻译代码或重复方法名。
- 禁止凑数、过时、误导性注释；注释与代码行为不一致时必须同步修正。
- 普通 getter/setter、显而易见的变量赋值、import、空行和单独大括号不需要注释。
- 局部改造旧代码时，只补本次改动和直接相关逻辑的说明，不借机大面积重写无关历史代码。

## Controller 规范

Controller 返回统一使用：

```java
com.kellen.utils.ApiResponse
```

成功：

```java
return ApiResponse.success(data);
```

OpenAPI / OpenAPI3 文档规则：

- Spring Boot 4 项目使用 OpenAPI3 注解，Controller 类必须添加 `@Tag(name = "...", description = "...")`。
- Controller 方法必须添加 `@Operation(summary = "...", description = "...")`，`summary` 使用前端和测试人员能直接理解的业务动作，不使用 `list`、`save`、`update` 等裸方法名。
- 查询参数、路径参数、请求头参数语义不明显时，使用 `@Parameter` 或 `@Parameters` 说明业务含义、是否必填和来源位置。
- `@Tag.name` 按业务资源命名，例如“用户管理”“角色管理”“权限资源管理”，不要让 OpenAPI 展示默认的 `xxx-controller`。
- `@Operation.summary` 必须唯一且可读，避免 OpenAPI 生成 `list_1`、`save_1`、`update_1` 这类默认 operationId。
- DTO、BO、Query、VO、Entity 字段继续使用 `@Schema(description = "...")`，让请求体和响应体在 OpenAPI 中可读。
- 接口鉴权需要通过 `@PreAuthorize` 和 OpenAPI 安全头共同表达；认证头由 `utils` 中统一 OpenAPI 配置维护，业务 Controller 不重复定义全局安全模型。

失败：

```java
return ApiResponse.fail(ReturnCode.用户密码错误, "用户名或密码错误");
```

接口响应字段统一为 `success`、`code`、`msg`、`data`、`errorMessage`、`timestamp`。不要新建临时 `Map<String, Object>` 作为接口统一响应壳。具体业务对象可以是 DTO、VO 或 `Map`，但最外层必须是 `ApiResponse`。

## 权限规范

需要鉴权的接口使用：

```java
@PreAuthorize("hasAuthority('module:resource:action')")
```

权限码格式建议：

```text
模块:资源:动作
```

## 多租户规范

租户字段统一为：

```sql
tenant_id varchar(64) NOT NULL DEFAULT '1'
```

请求头：

```text
X-Tenant-Id: 100
```

规则：

- 插入时由 `MyMetaObjectHandler` 自动填充 `tenantId`。
- 查询时由 `TenantLineInnerInterceptor` 自动追加租户条件。
- 业务代码不要重复手写 `tenant_id = ?`。
- 需要跨租户查询时必须显式使用 `TenantContextHolder.ignore()`，并在 `finally` 清理。

## 数据权限规范

数据权限使用 `utils` 中的 MyBatis-Plus `DataPermissionInterceptor` 统一处理，业务微服务不得重复编写 SQL 拦截器。

职责边界：

- `@PreAuthorize` 控制接口是否允许访问。
- `TenantLineInnerInterceptor` 控制租户之间的数据隔离。
- `DataPermissionInterceptor` 控制同一租户内本人、部门、部门树或自定义部门的数据范围。

用户体系建议字段：

```text
用户表：dept_id
角色表：data_scope
角色自定义部门表：role_id、dept_id
业务主表：owner_user_id、dept_id
```

标准数据范围：

```text
ALL        全部数据
SELF       仅本人数据
DEPT       本部门数据
DEPT_TREE  本部门及下级部门数据
CUSTOM     自定义部门数据
```

规则：

- 部门归属挂在用户下，角色只维护授权能力和数据范围。
- 用户有多个角色时，登录或网关侧应合并数据范围；`ALL` 最大，其他部门范围按部门集合并集处理。
- 登录态或网关 Header 需要携带 `deptId`、`dataScope`、`dataScopeDeptIds`，供 `utils` 数据权限插件拼接 SQL。
- 业务表只有声明了数据权限表规则后才会追加条件，避免给没有 `owner_user_id` 或 `dept_id` 字段的表拼错 SQL。
- 新增需要数据权限控制的业务主表时，DDL、Entity、BO、VO、Query、ServiceQuery 和 Nacos `security.data-permission.table-rules` 要同步补齐 `owner_user_id` 与 `dept_id`。
- 本人数据默认按 `owner_user_id` 过滤；如果历史表没有该字段，必须在表规则中明确指定可替代的用户字段。
- 多租户字段仍由租户插件处理，数据权限不要重复拼 `tenant_id`。
- 不需要数据权限的初始化、登录、字典、资源树等查询，要通过配置或 `DataPermissionContextHolder.ignore()` 显式跳过。

Nacos 配置示例：

```yaml
security:
  data-permission:
    enabled: true
    default-user-column: owner_user_id
    default-dept-column: dept_id
    ignore-tables:
      - report_task
      - report_type
    table-rules:
      report_record:
        user-column: owner_user_id
        dept-column: dept_id
      report_file:
        user-column: owner_user_id
        dept-column: dept_id
```

需要数据权限的报告业务表必须走 `DataPermissionInterceptor` 的全局表规则，不要在 Service 查询方法里为每个页面手写数据范围过滤。没有负责人或部门归属语义的报告渠道、报告场景等配置表应放入忽略表或不声明表规则。

## 逻辑删除规范

`EntityBase.isDelete` 使用 `@TableLogic`。

规则：

- 查询条件不要重复写 `is_delete = 0`。
- 删除优先使用 MyBatis-Plus 逻辑删除能力。

## README 维护规范

AI 自动化新增或修改功能代码后，必须同步检查项目根目录 `README.md`。

规则：

- 如果项目根目录已有 `README.md`，需要补充本次新增或调整的业务说明、接口说明、权限码、DDL 脚本、默认数据、启动或验证命令。
- 如果项目根目录没有 `README.md`，必须新建并写入当前服务职责、核心接口、关键配置、数据库维护方式和验证命令。
- `README.md` 只写当前微服务真实业务上下文，不写可复制的通用 AI 编码规范。
- 通用 AI 编码规范继续放在 `docs/ai-coding`，不要把当前服务的私有接口、默认账号、权限码写回通用规范。

## AI 编码检查清单

AI 每次新增模块时必须检查：

- 是否跟随当前微服务已有基础包结构。
- 是否继承 `EntityBase`。
- 是否使用 `ApiResponse` 统一返回。
- 是否使用 `jakarta.*`。
- 是否给业务枚举实现 `IEnum`。
- 是否给数据库状态字段建立对应枚举，且枚举实现 `IEnum<Integer>` 或匹配的泛型类型。
- 是否避免把业务枚举塞进 `EntityBase`。
- 是否避免重复拼 `tenant_id` 和 `is_delete`。
- 是否把跨服务通用数据权限逻辑放到 `utils`，业务服务只维护用户部门、角色数据范围和业务表字段。
- 是否在先查后改的更新逻辑中携带数据库记录的 `version`，并依赖 MyBatis-Plus 乐观锁处理并发覆盖。
- 是否按 `UTILS_PUBLIC_SPEC.md` 选择或扩展错误码。
- 是否先检查同级 `utils` 项目已有能力，并优先复用已有公共工具以减少当前微服务代码量。
- 是否把确实缺失的公共工具类、通用组件、基础配置或跨微服务复用能力放到同级 `utils` 项目，而不是写进当前业务微服务。
- 是否给受保护接口加 `@PreAuthorize`。
- 是否给新增或修改代码补齐类注释、字段注释、方法 JavaDoc 和关键行注释。
- 是否同步补充或新建项目根目录 `README.md`。
- 是否同步补充或更新 JUnit 5 测试用例。
- 是否运行 `./gradlew clean compileJava test`。
