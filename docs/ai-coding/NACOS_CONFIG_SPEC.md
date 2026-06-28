# Nacos 配置中心规范（Fleet 统一）

> 本文件是**全 fleet 统一**的 Nacos 配置中心分层与拆分规范，各服务仓库内容一致。
> 网关路由等服务专属约定见各自 `GATEWAY_CODING_SPEC.md` / `NACOS_CONFIG_SPEC.md` 的服务段，不与本规范冲突。
> 本地环境入口、Spring profile 与 Nacos namespace 的映射详见 `ENVIRONMENT_CONFIG_SPEC.md`。
> Fleet 成员：`ai`、`ai-agent`、`user`、`message`、`rag`、`gateway`（公共能力由 `utils` 提供）。

## 1. 机制：只用官方主流方式，不自创

- **统一用 Spring Cloud Alibaba 的 `spring.config.import` 导入远程配置**（SCA 2025.x 官方文档主推的多配置导入方式）。
  - 写法：`- "optional:nacos:{dataId}?refreshEnabled=true"`。
  - 当前 fleet 的 import URL 不重复写 `group=DEFAULT_GROUP`；默认 group 由同一 profile 文件中的 `spring.cloud.nacos.config.group` / `custom.nacos-group` 控制。只有跨 group 特例才在单条 import URL 显式写 `group`。`spring.config.import` 必须与对应 profile 的 Nacos 地址同文件，避免 ConfigData 解析阶段拿不到 `custom.nacos-ip`。
  - **不使用** `bootstrap.yml` + `spring.cloud.nacos.config.shared-configs/extension-configs` 经典写法，全 fleet 不混用两套机制。
- **dataId 命名**：
  - 服务自身配置：`{spring.application.name}.yaml`（业务）与 `{spring.application.name}-spring.yaml`（Spring 框架/环境）。
  - 共享 / 横切模块：**显式命名 dataId**（如 `redis.yaml`、`qdrant.yaml`、`aliyun.yaml`、`a2a.yaml`）——显式命名 import 就是官方示例写法，合规。
- **一个 `@ConfigurationProperties` 前缀树只存在于唯一一个 dataId**。禁止把同一前缀树拆到多个 dataId、靠加载顺序“合并”——这是非主流技巧，全 fleet 禁止。
- **共享的“值”用标准 `${占位符}` 引用**（Spring 原生占位符）。禁止裸 IP / 裸密钥散落到各服务文件；基础设施地址一律引用 `reuse-configuration.yaml` 的 `custom.*`。

## 2. 分层：每个 dataId 归属唯一、可解释

| 层 | dataId | group | 内容 |
|---|---|---|---|
| **L0 本地引导** | 各仓库 `src/main/resources/application.yml` + `application-dev.yml` / `application-test.yml` / `application-prod.yml` | — | 连 Nacos 前必需的最小集：`application.yml` 放 `server.port`、`spring.application.name`、`spring.profiles.active`、`custom.nacos-group`；`application-*.yml` 放对应环境的 Nacos `server-addr`、`namespace`、Nacos config/discovery 绑定逻辑和本环境 `spring.config.import`。**不放任何业务/密钥**。6 服务骨架只差 `port` + `name` + 各自 profile import 列表 |
| **L1 共享基础设施** | `logging.yml` `reuse-configuration.yaml` `traffic-governance.yaml` `redis.yaml` `rabbitmq.yaml` `elasticsearch.yaml` `qdrant.yaml` `seata.yaml` `zipkin.yaml` `admin.yaml` `dubbo.yaml` `xxl-job.yaml` `mybatis-plus.yaml` `security-auth.yaml` `swagger.yaml` | DEFAULT_GROUP | fleet 公共基础设施 / 框架配置 |
| **L2 共享横切域** | `aliyun.yaml`（aliyun 账号+OSS+SMS+钉钉+直播+email）、`a2a.yaml`（A2A 共享值） | DEFAULT_GROUP | 多服务共享的第三方/领域配置 |
| **L3 服务业务** | `{svc}.yaml` | DEFAULT_GROUP | 本服务业务键 + 本服务**私有**的 `@ConfigurationProperties` 树（如 `ai` 的 `wechat`、`aliyun.oss` bucket） |
| **L4 服务框架/环境** | `{svc}-spring.yaml` | DEFAULT_GROUP | datasource、profile、discovery、`spring.ai` model、gateway 路由等 Spring/环境配置 |

> 当前 fleet 的服务业务配置和 `{svc}-spring.yaml` 已统一迁移到 `DEFAULT_GROUP`，不要再新增 `group=test` 引用。

## 3. 各 dataId 内容边界

- **`reuse-configuration.yaml`**：**只放 `custom.*` 公共变量**（基础设施地址 + Nacos 凭据变量，如 `custom.infra-*`、`custom.nacos-username/password/context-path`）。**不放任何业务块或第三方密钥块**。
- **本地 `application-dev/test/prod.yml`**：定义 `custom.nacos-ip`、`custom.namespace`、Nacos config/discovery 绑定逻辑和本环境 `spring.config.import`；当前 dev/test/prod 默认指向同一 Nacos 地址和 namespace，后期环境拆开时只改对应 profile 文件或部署环境变量。
- **`traffic-governance.yaml`**：统一放灰度发布请求头名、默认 `X-Release-Version`、默认 `X-Traffic-Lane`、实例 `release.version`/`traffic.lane`/`canary.tag`/`traffic.weight` 元数据，以及 Nacos Discovery 元数据。**权重是治理配置和实例元数据，不由公网前端随意决定**；公网前端默认只带发布版本和泳道，灰度 tag/权重必须由受控配置显式开启。
- **`logging/traffic-governance/redis/rabbitmq/elasticsearch/seata/zipkin/admin/dubbo/xxl-job/mybatis-plus/security-auth`**：各对应一组 utils 自动配置 / Spring 体系前缀，整组留在各自 dataId。
- **`qdrant.yaml`**：Qdrant 向量数据库连接配置，当前由 `ai` profile 导入作为备用向量库入口；保留连接能力不等于默认启用本地 RAG 或 Spring AI VectorStore 执行链。
- **`swagger.yaml`**：`swagger.enable` + `swagger.name=${spring.application.name}`；要自定义显示名的服务在自己的 `{svc}.yaml` 覆盖一行。
- **`aliyun.yaml`**：整棵 `aliyun`（account key + `oss` + `sms` + `dingding` + `liveStreaming`）+ 顶层 `email`。绑定方为 utils 的 `CommonAliyunProperties(prefix="aliyun")`。**凡使用任一 aliyun 能力、或可能触发 utils `@RequestRequired` 钉钉告警的服务都要 import**。
- **`a2a.yaml`**：A2A 共享值，统一放 `custom.a2a-*`（协议版本、context-path、tenant、provider-org、agent 名契约）+ Nacos 凭据引用。`ai`（消费端 `ai.agent-registry.*`）与 `ai-agent`（生产端 `ai.agent.registry.*`）各自的块**引用** `${custom.a2a-*}`，agent 名两端共用同一变量，保证契约一致。
- **`{svc}.yaml`**：本服务业务键 + 本服务私有 `@ConfigurationProperties` 树。`ai` 私有的 `wechat`（微信小程序，绑定 `CommonWechatProperties(prefix="wechat")`）与 `aliyun.oss` bucket 等收在这里，**不放共享层**。
- **`{svc}-spring.yaml`**：datasource、`spring.profiles`、discovery override、`spring.ai.*` model、gateway 路由。

## 4. import 顺序（所有服务照此排）

```
logging → reuse-configuration → traffic-governance → security-auth(仅鉴权服务) → swagger
→ {svc} → {svc}-spring → qdrant(ai 备用向量库)
→ mybatis-plus → redis → rabbitmq → elasticsearch → seata → zipkin → admin → dubbo → xxl-job
→ aliyun(用到 aliyun/钉钉告警的服务) → a2a(A2A 参与方)
```

后导入的同名键覆盖先导入的；服务私有 `{svc}` / `{svc}-spring` 排在共享基础设施之后，便于在本服务做最终覆盖。

## 5. 各服务 import 清单（按角色裁剪）

| dataId | ai | ai-agent | user | message | report | gateway |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| logging | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| reuse-configuration | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| traffic-governance | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| security-auth | ❌(自有微信鉴权) | ❌ | ✅ | ✅ | ✅ | ❌ |
| swagger | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| {svc} / {svc}-spring | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| qdrant | ✅(备用向量库) | ❌ | ❌ | ❌ | ❌ | ❌ |
| mybatis-plus | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| redis | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| rabbitmq | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| elasticsearch | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| seata | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| zipkin | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| admin | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| dubbo | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| xxl-job | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| aliyun | ✅(OSS) | ⬜验证@RequestRequired | ✅ | ✅ | ✅ | ⬜验证@RequestRequired |
| a2a | ✅(消费) | ✅(生产) | ❌ | ❌ | ❌ | ❌ |

> `ai` 故意不导入 `security-auth.yaml`：用自有微信 Bearer 鉴权，fleet JWT 保持关闭。
> `aliyun` 对 `ai-agent`/`gateway` 标“验证”：取决于该服务是否使用 `@RequestRequired`（utils AOP 触发钉钉告警）或任何 aliyun 能力；用到才 import。

## 6. 改配置铁律

1. **读回验证**：改前读回 Nacos 权威配置，改后整体发布并再次读回校验；不靠本地文件或发布命令判断成功。
2. **保值不改值**：本规范是布局搬迁，**逐键保留原值**。`reuse-configuration` 的既有 `custom.*` 只增不改不删；utils `@Value` 消费的 `mysql/mysql-gray` 等绝不动。
3. **AI 自主边界**：配置中心的**结构性调整允许 AI 自主完成**——新增/拆分/合并 dataId、调整 import 与顺序、改 `${}` 引用、调整 Nacos 接入地址 / namespace / group。但**禁止触碰真实密钥/凭证**（access-key/secret、app-secret、API key、DB 密码、token、license 等，疑似密钥只告警、由负责人处理），且布局搬迁必须**保值不改值**，不得擅自变更生产业务配置的实际取值。真实密钥/地址只在 Nacos，仓库与 Nacos 模板只放占位符。
4. **加法优先迁移**：先建新 dataId（与旧块双份共存，零影响）→ 各服务切 import + 改引用 → 逐个重启读回验证 → **最后一步**才从 `reuse-configuration` 删旧块。
5. **新增微服务**：复制 `application.yml`、`application-dev.yml`、`application-test.yml`、`application-prod.yml` 模板，仅改 `server.port` + `spring.application.name` + 按角色裁剪 import 列表；profile 文件只按环境声明 Nacos 地址和 namespace，禁止重写引导段结构。若新服务需要通过网关访问或进入 Swagger UI 聚合，必须同步读取并整体更新 Nacos `gateway-spring.yaml`（`DEFAULT_GROUP`）：补 `spring.cloud.gateway.server.webflux.routes` 业务路由和 `springdoc.swagger-ui.urls` 聚合项；发布后读回并验证对应网关文档路径（例如 `/<service>/v3/api-docs`）、`/v3/api-docs/swagger-config` 与 `/swagger-ui/index.html`。
