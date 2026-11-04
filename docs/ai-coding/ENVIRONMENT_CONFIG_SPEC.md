# GitHub 环境配置入口规范

个人项目与美联公司开发环境分别维护。代码使用 main（正式主干）、test（集成测试）、preprod（发布候选）和短功能分支；Git 阶段不自动切换 Spring profile 或 Nacos namespace，也不代表环境已部署。

| profile | 默认 namespace | Nacos 地址变量 |
|---|---|---|
| dev | github-dev | GITHUB_NACOS_DEV_SERVER_ADDR → GITHUB_NACOS_SERVER_ADDR → 当前开发服务默认地址 |
| test | github-dev | 同 dev |
| prod | github-dev | 同 dev |

按用户当前决定，三个 profile 均连接个人 `github-dev`，统一接受 `GITHUB_NACOS_DEV_NAMESPACE` 显式覆盖，不再接受旧 `NACOS_NAMESPACE` 回退。`test`、`prod` profile 名称不代表已经建立或部署对应环境；不得指向公司 dev 临时替代。未来建立独立环境时，需同时调整 profile、契约测试与本规范。

application.yml 仅保留端口、服务名、默认 APP_ENV=dev 与 GITHUB_NACOS_GROUP。application-*.yml 同时声明连接地址、namespace、config/discovery 绑定和本 profile 的 spring.config.import。真实中间件、业务开关和密钥仍在对应 Nacos；不将业务配置搬回本地 profile。

config 与 discovery 共用 custom.nacos-ip/custom.namespace。Dubbo、Nacos AI Registry 和 Prompt SDK 也必须显式引用它们；独立 SDK 的 username/password 不能假设继承 Spring Cloud 配置。Gateway 不承担业务认证。

个人环境的可提交模板、私有渲染、CAS 发布、数据库与统一 X File Storage 入口统一见同级 utils/ops/github-dev/README.md。私有配置保存在 ~/.config/kellen-github/，本机 dev 引导文件为各服务根目录的 config/application-dev.properties，已加入 .gitignore，权限必须为 0600；两者都不进 Git。前端不连接 Nacos，后端 base URL 沿用 http://localhost:7100。

本机直接启动时，Spring Boot 自动读取 config/application-dev.properties 中的 GITHUB_NACOS_DEV_SERVER_ADDR、GITHUB_NACOS_DEV_NAMESPACE、GITHUB_NACOS_GROUP、GITHUB_NACOS_USERNAME、GITHUB_NACOS_PASSWORD。IDE working directory 必须为当前服务仓库根；bootRun 默认使用该目录，java -jar 也从该目录执行。无需修改 application.yml 或预先 export；OS 环境变量仍可覆盖私有文件。显式启用 test/prod 时不读取 dev 文件，但仍沿用个人 dev 的 Nacos 连接变量与默认值。地址未覆盖时使用 profile 中既有开发服务默认地址，不回落到 localhost。

当前个人 Nacos 的控制台使用 18080，Spring Cloud/SDK server-addr 使用 8848，客户端 gRPC 使用 9848；控制台 URL 不能直接作为 server-addr。命名空间 ID 为 github-dev，与显示名一致。真实地址及账号密码只填入上述本机私有文件。

只读校验：在 utils 运行 python3 ops/github-dev/scripts/verify_configuration.py。发布前读回旧配置，已有内容更新必须 CAS；发布后逐项读回，不能以命令成功代替运行验收。
