# GitHub 环境配置入口规范

个人项目与美联公司开发环境分别维护。代码使用 main 和短功能分支；环境由 Spring profile 与独立 Nacos namespace 表达，不沿用公司的 test/preprod 分支流程。

| profile | 默认 namespace | Nacos 地址变量 |
|---|---|---|
| dev | github-dev | GITHUB_NACOS_DEV_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → localhost |
| test | github-test | GITHUB_NACOS_TEST_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → localhost |
| prod | github-prod | GITHUB_NACOS_PROD_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → localhost |

各环境 namespace 仅由 GITHUB_NACOS_<ENV>_NAMESPACE 显式覆盖，不再接受旧 NACOS_NAMESPACE 回退。当前只建立 github-dev；github-test/github-prod 未创建、未部署，不得指向公司 dev 临时替代。

application.yml 仅保留端口、服务名、默认 APP_ENV=dev 与 GITHUB_NACOS_GROUP。application-*.yml 同时声明连接地址、namespace、config/discovery 绑定和本 profile 的 spring.config.import。真实中间件、业务开关和密钥仍在对应 Nacos；不将业务配置搬回本地 profile。

config 与 discovery 共用 custom.nacos-ip/custom.namespace。Dubbo、Nacos AI Registry 和 Prompt SDK 也必须显式引用它们；独立 SDK 的 username/password 不能假设继承 Spring Cloud 配置。Gateway 不承担业务认证。

个人环境的可提交模板、私有渲染、CAS 发布、数据库与统一 X File Storage 入口统一见同级 utils/ops/github-dev/README.md。私有配置只在 ~/.config/kellen-github/，不进 Git。前端不连接 Nacos，后端 base URL 沿用 http://localhost:7100。

只读校验：在 utils 运行 python3 ops/github-dev/scripts/verify_configuration.py。发布前读回旧配置，已有内容更新必须 CAS；发布后逐项读回，不能以命令成功代替运行验收。
