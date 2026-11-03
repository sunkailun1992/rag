# Office 文档通用摄取

知识摄取支持 XLSX 与 DOCX。DOCX 正文按标题和段落切片，表格重复完整表头，并保留段落/表格行定位；知识图谱显示对应章节、段落和表格记录。继续执行压缩包展开上限、条目上限、记录数和切片长度校验，伪装为 DOCX 的 XLSX 被拒绝。

现有 `rag:document:manage` / `rag:retrieval:use` / 图谱权限和租户过滤保持独立边界。个人通用 `general-knowledge`、`knowledge-vectors` 和 rerank 指令继续使用现有值，没有迁移专有医疗知识、医院标签或测试数据。

依赖固定为 `com:utils:1.5.1` 与 `com:rpc-api:1.0.7`。验证：`./gradlew test --no-daemon`、`./gradlew bootJar --no-daemon`、`bash scripts/check-secrets.sh`。普通测试不接真实模型或向量库；真实摄取/检索另行验收。

交付验证执行完整 `./gradlew clean build --rerun-tasks --no-daemon`、凭据扫描与差异检查。公共库固定为仓库声明的精确版本；GitHub CI 从相应公共库的 main 构建 Maven Local。Git 提交、远端 CI、制品发布与实际部署分别以各自回执为准。
