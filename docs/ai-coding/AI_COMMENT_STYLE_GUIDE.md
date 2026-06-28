# AI 注释规范

本规范约束 AI 在 `rag` 服务中新增或修改注释的方式。项目是RAG 知识检索服务后端，注释必须服务于报告归属、访问权限、租户隔离、文件链路、MQ、导出脱敏和失败处理维护。

## 0. AI 执行流程

- 修改注释前先识别文件类型和上下文，例如 Java/Spring、MyBatis XML、SQL、YAML/Nacos、Gradle、Shell 或 Markdown。
- 优先阅读 `AGENTS.md`、`docs/ai-coding/AI_CODING_GUIDE.md`、本文件、`PROJECT_CODING_SPEC.md`、`UTILS_PUBLIC_SPEC.md`、`SECURITY_CODING_SPEC.md`。
- 本规范未覆盖的文件类型，先查官方或主流规范，补充规范来源和RAG 知识检索服务落地规则后再改代码。
- 不为了统一风格批量重排生产配置、SQL 历史脚本、密钥所在行或已发布 DDL。

## 1. 总原则

- 自解释优先：能用清晰类名、方法名、BO/VO/Query 类型、报告状态常量和小方法表达的意图，先重构代码，不用注释补救。
- 注释只解释代码看不出的内容：报告归属、访问权限、租户隔离、幂等、MQ 重试、文件访问、导出脱敏和失败策略。
- 不给 package、import、普通注解、简单赋值、普通 getter/setter 或显而易见的链式调用逐行加注释。
- 禁止逐行翻译式注释，例如“获取消息”“返回结果”“调用 mapper”。
- 禁止用注释保留废弃实现、调试 main、临时 SQL 或整块旧代码；历史版本交给 Git。
- 注释必须随代码同步更新，过时注释必须删除或修正。
- 作者、创建时间、邮箱和修改历史交给 Git 记录；Javadoc 不写 `@DateTime`、`@email`、`@ClassName`、`@explain` 等非标准标签。

## 2. Java、Mapper 和 SQL 注释

- Controller、Service、ServiceQuery、Mapper、Entity、BO、VO、MQ 处理和报告文件逻辑应使用类级或方法级 Javadoc 说明职责、边界和安全约束。
- 报告查询、管理侧权限、个人侧权限、租户隔离、文件访问、审核状态和重复生成相关注释必须说明为什么这样做，以及不能绕过什么保护。
- MyBatis XML 注释只解释复杂 SQL、权限条件、租户条件和性能边界，不解释普通标签语法。
- SQL 迁移注释解释目的、影响范围、幂等条件和回滚风险；已执行脚本不因注释风格改原文件。

## 3. YAML、Gradle、Shell 和 Markdown 注释

- YAML/Nacos 注释解释环境差异、MQ/基础设施引用、安全边界和误改风险，不批量重排生产配置。
- 发现 RabbitMQ、Nacos、数据库连接、默认账号或其它敏感配置时，只报告文件行号和风险，不自动替换、删除或移动。
- Gradle 注释解释依赖、插件、版本和任务的项目原因，不解释 DSL 语法。
- Shell 注释解释安全边界、错误处理、密钥脱敏和退出码，不解释普通命令。

## 4. 格式和美观度

- 维持当前文件缩进、空行、换行宽度和段落风格，不在同一文件混用多套注释风格。
- JavaDoc 段落短而完整，长句按语义换行，不写超长单行。
- 行尾注释只用于短枚举、短单位或既有对齐风格；造成列宽混乱或超长行时改为块上方注释。
- 不为了“看起来整齐”改动密钥、生产地址、RabbitMQ 地址、数据库凭据、token 或 Nacos 配置所在行。
- 提交前从 diff 视觉检查一次：注释应让报告权限和失败边界更容易扫读，而不是更乱。

## 5. 检查清单

- 注释是否解释了报告归属、访问权限、租户隔离、MQ 或文件安全边界？
- 是否可以用更好的命名、类型、状态常量或小方法替代注释？
- 是否存在注释掉的旧实现、调试代码、临时 SQL 或整块废弃实现？
- 是否泄露密钥、生产地址、RabbitMQ 凭据、数据库凭据或 token？
- 缩进、换行、对齐和段落是否与当前文件风格一致？

## 6. 参考依据

- [Google Java Style Guide - Javadoc](https://google.github.io/styleguide/javaguide.html#s7-javadoc)
- [Oracle JDK Documentation Comment Specification](https://docs.oracle.com/en/java/javase/21/docs/specs/javadoc/doc-comment-spec.html)
- [YAML 1.2.2 Specification - Comments](https://yaml.org/spec/1.2.2/#comments)
- [Spring Boot Externalized Configuration](https://docs.spring.io/spring-boot/reference/features/external-config.html)
- [MyBatis Mapper XML](https://mybatis.org/mybatis-3/sqlmap-xml.html)
- [MySQL Reference Manual - Comments](https://dev.mysql.com/doc/refman/8.4/en/comments.html)
- [Gradle Build Language Reference](https://docs.gradle.org/current/dsl/)
- Robert C. Martin《Clean Code》第 4 章 Comments：注释是次优手段，优先让代码自解释；注释掉的代码应删除。
