# 环境配置入口规范

本规范用于区分代码分支、部署环境、Nacos namespace 和运行时灰度治理。`main` 不是环境，Java 服务通过 Spring profile 选择 `dev`、`test`、`prod`，再由对应 profile 文件映射到 Nacos 地址和 namespace。

## Java 服务文件结构

Java / Spring Boot 服务只保留以下本地入口文件：

```text
src/main/resources/application.yml
src/main/resources/application-dev.yml
src/main/resources/application-test.yml
src/main/resources/application-prod.yml
```

职责划分：

- `application.yml`：通用启动骨架，放 `server.port`、`spring.application.name`、`spring.profiles.active` 和 `custom.nacos-group`；不放 Nacos 地址、namespace 或远程业务配置。
- `application-dev.yml`：放 dev 环境 Nacos `server-addr`、`namespace`、Nacos config/discovery 绑定逻辑和本环境 `spring.config.import`。
- `application-test.yml`：放 test 环境 Nacos `server-addr`、`namespace`、Nacos config/discovery 绑定逻辑和本环境 `spring.config.import`。
- `application-prod.yml`：放 prod 环境 Nacos `server-addr`、`namespace`、Nacos config/discovery 绑定逻辑和本环境 `spring.config.import`。

禁止在 `application-dev.yml`、`application-test.yml`、`application-prod.yml` 中放数据库、Redis、MQ、OSS、模型密钥、业务参数、Dubbo 参数或业务路由规则；这些配置必须继续放 Nacos 对应 namespace 的 `DEFAULT_GROUP` dataId。profile 文件只允许保存连接 Nacos 和导入 dataId 所需的启动入口。

## 启动方式

启动时只需要传一个环境：

```bash
APP_ENV=test java -jar app.jar
```

或：

```bash
java -jar app.jar --spring.profiles.active=test
```

允许值：

```text
dev
test
prod
```

默认值为 `dev`，由 `application.yml` 中的 `${APP_ENV:dev}` 控制。

## Nacos 映射规则

当前 dev/test/prod 暂时共用同一套 Nacos 地址和 namespace；后期环境拆开时，只改对应 profile 文件或部署环境变量。

环境变量优先级：

```text
dev:
  NACOS_DEV_SERVER_ADDR -> NACOS_SERVER_ADDR -> 当前默认地址
  NACOS_DEV_NAMESPACE   -> NACOS_NAMESPACE   -> 当前默认 namespace

test:
  NACOS_TEST_SERVER_ADDR -> NACOS_SERVER_ADDR -> 当前默认地址
  NACOS_TEST_NAMESPACE   -> NACOS_NAMESPACE   -> 当前默认 namespace

prod:
  NACOS_PROD_SERVER_ADDR -> NACOS_SERVER_ADDR -> 当前默认地址
  NACOS_PROD_NAMESPACE   -> NACOS_NAMESPACE   -> 当前默认 namespace
```

规则：

- `spring.cloud.nacos.config.namespace` 和 `spring.cloud.nacos.discovery.namespace` 必须使用同一个 `custom.namespace`。
- `spring.cloud.nacos.config.server-addr` 和 `spring.cloud.nacos.discovery.server-addr` 必须使用同一个 `custom.nacos-ip`。
- Nacos group 默认统一为 `DEFAULT_GROUP`；`spring.config.import` URL 不重复携带 `group=DEFAULT_GROUP`，由同 profile 文件中的 `spring.cloud.nacos.config.group` / `custom.nacos-group` 控制。只有跨 group 特例才在单条 import URL 显式写 `group`。import 必须与该 profile 的 Nacos 地址放在同一个环境文件中，避免 ConfigData 解析阶段拿不到 `custom.nacos-ip`。
- 不再用 `group=test` 表达环境。
- 同名 dataId 在不同 namespace 保存不同环境值。

## Prod 注意事项

正式上线前必须确认：

- `application-prod.yml` 或部署 Secret 已指向生产 Nacos 地址。
- `application-prod.yml` 或部署 Secret 已指向 prod namespace。
- prod namespace 中存在完整 dataId。
- prod 的数据库、Redis、MQ、OSS、Seata、Dubbo、日志和告警配置与 test 隔离。
- 不能让 prod 进程读取 test namespace，也不能让 test 进程注册到 prod discovery namespace。

## 前端和小程序

前端和小程序没有 Spring profile，不直接连接 Nacos。

- 前端只配置网关地址、构建模式和请求头默认值。
- 小程序只配置 API base URL、上传配置和请求头默认值。
- 前端/小程序不能保存 Nacos 地址、namespace、数据库、Redis、MQ 或生产密钥。

## 禁止事项

- 禁止把 `spring.config.import` 放回通用 `application.yml` 再引用 profile 文件里的 Nacos 地址；ConfigData 解析阶段拿不到 profile 中的 `custom.nacos-ip`，会触发 `illegal URI`。
- 禁止把业务配置从 Nacos 搬回本地 profile 文件。
- 禁止用 Git 分支名区分 dev/test/prod。
- 禁止 config namespace 和 discovery namespace 不一致。
- 禁止在 prod profile 中默认指向 test Nacos 后仍声称已完成正式环境隔离；当前共用只允许作为上线前临时状态。
