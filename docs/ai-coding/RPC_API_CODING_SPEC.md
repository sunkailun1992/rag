# RPC API 协作规范

## 契约归属

- 跨服务 Dubbo RPC 接口、DTO、枚举和值对象统一维护在同级 `../rpc-api`。
- 当前服务只实现或调用 `rpc-api` 中的契约，不在本仓库复制接口和 DTO。
- `utils` 只提供 Dubbo 上下文透传、公共配置、公共工具和中间件适配，不维护业务 RPC 契约。

## 当前服务角色

- `rag` 是 RAG 知识检索服务 provider，使用 `@DubboService` 发布 `rpc-api` 中的 RAG 检索契约；后续如需调用其它服务，也可以作为 consumer 使用 `@DubboReference`。
- consumer 不依赖 provider 服务源码，不复制 provider 内部实体。
- 本地验证接口必须标明测试用途，避免误认为正式业务 API。

## 依赖和 CI

- Gradle 依赖使用 `implementation "com:rpc-api:${rpcApiVersion}"`。
- 本地联调前先在 `../rpc-api` 执行 `./gradlew publishToMavenLocal`。
- CI 必须先 checkout `sunkailun1992/rpc-api` 并 `publishToMavenLocal`，再编译本服务。
- 修改 RPC 契约时，至少同步编译 `../rpc-api`、provider 和当前 consumer。

## 上下文

- 登录用户、租户、数据源、Seata XID、请求 ID、版本号、流量泳道等横切上下文优先由 Dubbo attachment/filter 透传。
- 确实作为业务查询条件的字段可以保留在接口参数中，例如显式查询指定租户用户。

## 幂等声明（utils 2.1.1 起）

- utils 的 Dubbo 调用端统一重试只对提供方声明了幂等的方法，在请求发出后的连接类失败时重试；没声明的方法只在请求没发出时重试。
- 只在读实现确认不改业务状态（为执行本身写的日志、审计、评估记录不算）后，在实现类的 `@DubboService(methods = @Method(name = "<方法名>", parameters = {"idempotent", "true"}))` 上逐个方法声明；写方法不声明，拿不准就不声明。
- 不要写在服务级 `@DubboService(parameters = ...)` 上：服务级的 `idempotent` 对全部方法生效，包括以后新增的写方法。
- 本仓库已声明：`RagRetrievalRpcService.retrieve`（只查检索引擎；重试会多调一次模型）。
- 本仓库已声明的方法与护栏由 `DubboIdempotentDeclarationTest` 锁定；规则见 utils `docs/ai-coding/RPC_API_CODING_SPEC.md`「调用端统一重试」。
