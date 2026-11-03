# GitHub 环境配置入口规范

个人项目与美联公司开发环境分别维护。代码使用 main/test/preprod 三个长期分支与短功能分支，分别承担正式主干、整合测试和候选预发；运行环境仍由 Spring profile 与独立 Nacos namespace 表达，分支不自动切换 profile，也不复制公司自动部署规则。

| profile | 默认 namespace | Nacos 地址变量 |
|---|---|---|
| dev | github-dev | GITHUB_NACOS_DEV_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR；均缺失则失败 |
| test | github-test | GITHUB_NACOS_TEST_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → localhost |
| prod | github-prod | GITHUB_NACOS_PROD_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → localhost |

各环境 namespace 仅由 GITHUB_NACOS_<ENV>_NAMESPACE 显式覆盖，不再接受旧 NACOS_NAMESPACE 回退。当前只建立 github-dev；github-test/github-prod 未创建、未部署，不得指向公司 dev 临时替代。

application.yml 原内容保持不变，仅保留端口、服务名、默认 APP_ENV=dev 与 GITHUB_NACOS_GROUP。application-*.yml 同时声明连接地址、namespace、config/discovery 绑定和本 profile 的 spring.config.import。真实中间件、业务开关和密钥仍在对应 Nacos；不将业务配置搬回本地 profile。

本地 dev 引导使用 Spring 标准自动搜索：以当前服务仓库根目录为 working directory，读取已忽略的 config/application-dev.properties。私有文件权限必须为 0600，仅包含 Nacos 连接所需五个引导键；模型和数据库密钥继续从远端配置获取。IDE main、bootRun 与 java -jar 使用同一机制，不依赖启动前手工 export dev.env；从其它目录启动需显式给出 spring.config.additional-location。

OS 环境变量优先于私有文件；显式 spring.profiles.active 优先于 APP_ENV。单独激活 test/prod 时不会读取 application-dev.properties，不得用 dev 私有文件替代其它环境入口。dev 缺少两个地址变量时保留未解析占位符并拒绝配置，不静默连接 localhost；保留 github-dev 的 namespace ID，不改到公司 dev。

Nacos 控制台端口为 18080，客户端/SDK server-addr 使用 8848，客户端 gRPC 使用 9848。控制台可登录不能代替 SDK getConfig 读回验证；不能把 18080 写入服务的 Nacos server-addr。

config 与 discovery 共用 custom.nacos-ip/custom.namespace。Dubbo、Nacos AI Registry 和 Prompt SDK 也必须显式引用它们；独立 SDK 的 username/password 不能假设继承 Spring Cloud 配置。Gateway 不承担业务认证。

个人环境的可提交模板、私有渲染、CAS 发布、数据库与统一 X File Storage 入口统一见同级 utils/ops/github-dev/README.md。私有配置只在 ~/.config/kellen-github/，不进 Git。前端不连接 Nacos，后端 base URL 沿用 http://localhost:7100。

只读校验：在 utils 运行 python3 ops/github-dev/scripts/verify_configuration.py。发布前读回旧配置，已有内容更新必须 CAS；发布后逐项读回，不能以命令成功代替运行验收。
