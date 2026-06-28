# 测试分层规范

## 定位

`rag` 当前是RAG 知识检索服务空业务骨架。后续新增报告生成、解析、归档、导出、审核、文件访问或 MQ 相关业务后，核心业务测试必须覆盖真实 Spring Boot 业务链路，不能只靠纯对象断言。

## 主流分层

- 单元测试：DTO、枚举、小工具、小函数，使用 JUnit 5 + AssertJ。
- Slice 测试：局部 Controller/Mapper 可以使用 `@WebMvcTest`、`@MybatisTest`，只作为补充。
- Service 集成测试：核心业务用 `@SpringBootTest` 注入真实 Spring Bean，验证事务、AOP、权限上下文、多租户、Mapper 和报告访问边界。
- HTTP 集成测试：核心对外接口用 `@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)`，通过 `TestRestTemplate` 或 `WebTestClient` 发真实 HTTP 请求。
- 跨服务测试：Dubbo、Gateway、Nacos、RabbitMQ 等链路放到独立 integration profile 或测试环境。

## assertThat 规则

`assertThat` 是断言工具，可以继续使用。判断测试是否充分，看被断言的数据是否来自真实业务链路，而不是看是否用了 `assertThat`。

## 业务测试要求

- 报告生成、查询、审核、导出、文件访问、重复生成、管理侧权限、个人侧权限和租户隔离必须有 Spring Boot 级别测试。
- Controller 测试优先走真实 HTTP 请求，验证路由、参数校验、权限、统一响应和异常处理。
- Service 测试必须验证真实 Bean、真实 Mapper、真实事务和真实租户上下文。
- 不允许只用 mock 或纯 Java 对象测试替代核心业务集成测试。

## 测试数据

- 使用 `test` profile 和测试库。
- 测试数据可以落库，但必须可重复、可识别、可清理。
- 测试前置数据必须由 SQL、fixture、builder 或测试接口显式准备，不依赖本机临时数据刚好存在。

## 必跑命令

```bash
./gradlew clean test
```
