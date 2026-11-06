# Nacos 配置中心规范（GitHub 个人项目）

> 配置注释必须遵循 [Nacos 注释与归类规范](NACOS_COMMENT_SPEC.md)，覆盖可提交模板、私有渲染产物、引导配置和发布工具。

## 当前环境与配置来源

个人服务为 `gateway`、`user`、`message`、`report`、`ai`、`ai-agent`、`ai-memory`、`rag`、`mcp`；`utils` 提供公共配置绑定与唯一模板目录，本身不作为 Nacos 服务启动。

`dev`、`test`、`prod` 三个 profile 当前均连接个人 `github-dev / DEFAULT_GROUP`，统一使用 `GITHUB_NACOS_DEV_*` 引导变量，Git 分支不切换环境，也不回落美联 dev。具体地址、控制台和 SDK 端口的区别见 [个人环境说明](../../../utils/ops/github-dev/README.md)。Yudao 与美联实际配置不在个人目录维护范围。

当前 27 个 dataId 的职责和消费者清单见 [配置分类索引](../../../utils/ops/github-dev/nacos/README.md)。它来自九个服务的实际 `application-dev.yml` import，不能继续沿用早期五服务清单或公司 `aliyun.yaml` 配置清单。远端生效值仍以目标 Nacos 的当前读回为准，仓库只保存默认值和占位符。

## 1. 引导与导入机制

- 使用 Spring Cloud Alibaba `spring.config.import`，不混用 `bootstrap.yml` 与 `shared-configs/extension-configs`。
- `application.yml` 维护服务名、端口、profile 入口和默认 group；`application-dev.yml`、`application-test.yml`、`application-prod.yml` 维护 Nacos 接入与 import。地址必须与对应 import 位于同一 profile 文件，不能依赖尚未加载的远端配置提供连接信息。
- 当前 import 同时存在 `optional:nacos:` 与必需的 `nacos:` 项。是否 optional、`refreshEnabled`、group 和顺序均属于运行契约，注释整理不得改变。
- 默认 group 由 `custom.nacos-group` 与 `spring.cloud.nacos.config.group` 控制，跨 group 特例才在 import URL 显式声明。
- 共享值通过 Spring `${custom.*}` 引用；`GITHUB_*` 是私有渲染/本地引导输入。不要把两类占位符相互替换，不将真实凭据复制进模板。

## 2. 职责分类

| 分类 | dataId / 入口 | 维护边界 |
|---|---|---|
| 本地引导 | `application*.yml`，私有 `config/application-dev.properties` | 连接 Nacos 前需要的服务与环境入口；私有文件不进 Git |
| 共享变量 | `reuse-configuration.yaml` | 跨文件引用的中间件地址、Dubbo 分组等 `custom.*` 变量 |
| 共享基础设施 | `redis.yaml`、`rabbitmq.yaml`、`elasticsearch.yaml`、`qdrant.yaml`、`dubbo.yaml`、`mybatis-plus.yaml`、`seata.yaml`、`xxl-job.yaml`、`storage.yaml` | 中间件连接、隔离边界及模块开关；存储使用 X File Storage / MinIO |
| 安全与可观测性 | `security-auth.yaml`、`security-jwt.yaml`、`actuator.yaml`、`logging.yml`、`traffic-governance.yaml`、`swagger.yaml` | 认证/租户、人员 JWT、管理端点、日志、流量治理及监控 |
| 共享 AI 协议 | `a2a.yaml` | A2A 租户、协议入口、提供方和注册契约 |
| 共享 AI 模型 | `ai-model-secrets.yaml` | 模型选择及私有凭据占位符，按实际消费者导入 |
| 服务配置 | `{service}-spring.yaml` | 同一服务的数据源、模型选择、路由及服务专属参数；共享配置仍独立维护 |

分类是维护索引，不是新的目录或 group。当前保留 27 个 dataId 和 `DEFAULT_GROUP`；经单独授权并验证后，5 个服务专属文件合并到相邻的 `{service}-spring.yaml`，3 个空服务文件移除。不得在纯注释任务中擅自再次调整结构。

## 3. 配置归属与隔离

- 相同配置职责和完整键的归属必须明确，不能靠未说明的重复键覆盖完成迁移；绑定类、消费者、引用关系与必要覆盖顺序一并记录。
- `reuse-configuration.yaml` 只承载共享变量；Nacos 登录配置来自本地引导，数据库与模型凭据通过私有环境渲染到对应 dataId，不能为省事集中到公开文档。
- `storage.yaml` 维护通用存储；个人服务不导入公司的 OSS/SMS/钉钉/医疗业务配置。`security-jwt.yaml` 与 `security-auth.yaml` 分别维护签名参数和认证/租户规则，不能因为一个服务不导入后者而宣称它不使用 JWT。
- 命名空间隔离之外，继续保持现有数据库、Redis 库号、RabbitMQ vhost、Dubbo group、Qdrant collection、ES 索引和对象存储前缀边界。注释不得暗改这些实际值。
- `ai-memory.yaml`、`message.yaml`、`report.yaml` 的空映射已从当前模板与 imports 移除；`ai.yaml`、`ai-agent.yaml`、`mcp.yaml`、`rag.yaml`、`user.yaml` 的参数统一由对应 `{service}-spring.yaml` 提供。旧远端项须在消费者切换验证后清理，不能先删导致旧服务刷新缺配置。
- `catalog.json` 维护 dataId 描述、分类和消费者，`retired-dataids.json` 维护明确的旧项与替代清单；二者位于唯一模板目录的上层。Dubbo 自动 metadata/mapping 不属于人工配置，不在清理清单内。

## 4. import 顺序与消费者

各服务当前 import 清单及顺序以其三个 profile 文件为准。分类索引只说明谁导入，不要求按表格顺序重排。重复键覆盖、optional 与动态刷新语义均须单独评估，不能只用 YAML 可解析证明重排安全。

在同级 `utils` 中运行 `python3 ops/github-dev/scripts/verify_configuration.py`，检查九个服务的 27 个 profile、27 个 dataId、环境隔离和网关路径契约；新增或删除 import 时同步更新分类索引和受影响消费者。

## 5. 修改与发布

1. 先读 [注释与归类规范](NACOS_COMMENT_SPEC.md)，确认目标环境与排除清单。注释任务保持有效键、类型、值、占位符、列表/import 顺序不变。
2. 读取 Nacos 当前完整内容及 metadata，私有快照保存在 Git 外；远端不可达时只整理本地模板，不以旧渲染结果覆盖线上。
3. 比较整理前后的解析结果和有序列表，验证渲染保留注释与 Spring 引用。凭据特殊字符不能改变 YAML 结构。
4. 通过既有官方 SDK/CAS 发布流程更新 `github-dev / DEFAULT_GROUP`；并发漂移时停止。发布后逐项读回内容及 metadata，核对服务健康，分别报告 Git、本地检查、实际配置发布和运行态结论。
5. 真正需要拆分或合并配置时另行验证完整迁移链：读回当前真实值并备份、逐键/类型/有序列表和有效覆盖结果比对、新旧并存、消费者切换、读回和运行验证后才清理旧项。原始导入不相邻、optional/刷新语义不同或有冲突时停止自动合并。回滚先恢复旧 dataId，再回退消费者 imports；不能把模板删除或配置并存称为运行验收。
6. 私有渲染使用新的空输出目录，发布前将文件集合与 catalog 核对，避免已移除的旧文件残留后被再次发布。所有参数和文件说明必须保留；配置描述不包含任何真实凭据。
7. 新服务按既有引导结构接入。需要通过网关访问时同步检查路由与后端 Controller 原生路径；需要 Swagger 聚合时再显式启用并验证对应文档入口，不因新增服务默认开放管理或文档端点。
