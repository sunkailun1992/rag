# 测试分层规范

## 定位

`rag` 已有文档摄取、检索、图谱和 Python 引擎适配。Java 核心链路使用 Spring Boot 级测试，Python 解析/检索使用 pytest，跨语言合同由真实 Python 进程验证。OCR、Qdrant 和模型替身测试不得描述为真实部署或质量验收。

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

## 测试稳定性

- 测试结果只能由输入决定，不依赖真实时间、线程调度、连接复用、端口、执行顺序或本机遗留状态。需要时间的逻辑注入 `Clock`；等待异步结果用有总时限的条件等待，不用固定 `Thread.sleep` 赌时序。
- 本地 HTTP 桩服务每个测试独占随机端口，并在 `@AfterEach` 停止。同一测试对同一被测实例请求多次时，桩响应设 `Connection: close`，不依赖 JDK `HttpServer` 的 keep-alive 连接回收时序。更复杂的桩可评估 MockWebServer 或 WireMock，新增依赖按依赖变更处理。
- 连接池、事件循环、静态单例、系统属性等进程级共享状态，不能成为测试之间的隐含依赖；用到时写明隔离方式，不依赖测试类或测试方法的执行顺序。
- 新增或修改涉及网络、并发、超时、流式的测试，合并前本机连续三轮运行该测试类全部通过：`./gradlew test --rerun --tests '<测试类全名>'`。
- 偶发失败按缺陷处理：先查明原因再修，不用自动重试、放宽断言、`@Disabled` 或删除测试换取通过。确需临时禁用时，在注解里写明原因与恢复条件，并在交付说明中列出。
- 本机 macOS 通过不代表 CI 的 Linux 通过；只在一个平台验证过的结论要写明平台。

## 必跑命令

```bash
./gradlew clean test
```
