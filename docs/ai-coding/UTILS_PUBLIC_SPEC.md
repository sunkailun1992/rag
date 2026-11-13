# Utils 公共规范迁移说明

本文件是从 `utils` 公共 markdown 提炼出的 AI 阅读版规范，只保留项目编码会用到的公共规则。归档资料已保存在 `docs/ai-coding/archive/utils-markdown/`，需要完整错误码、公告原文或 Java 通用规范时再打开对应文档。

## 原始资料映射

| 原始文件 | 迁移后位置 | AI 阅读用途 |
| --- | --- | --- |
| `Announcement.md` | `archive/utils-markdown/Announcement.md` | 团队分支、环境、乐观锁、枚举、数据库和注释规则 |
| `ErrorCode.md` | `archive/utils-markdown/ErrorCode.md` | 统一错误码分类和错误信息 |
| `Specification.md` | `archive/utils-markdown/Specification.md` | 压缩版 Java 开发规范，已去掉图片和外部网站地址 |

## 环境规则

- profile、Nacos namespace 与私有引导配置按 `ENVIRONMENT_CONFIG_SPEC.md`；当前个人本地 dev 使用 github-dev，不沿用归档里的旧环境或组织工具约定。
- Git 分支（只保留 `main` 长期分支）不自动切换运行环境；正式环境不得制造测试数据，配置、数据修复和部署按明确授权与回滚计划执行。

## 分支规则

- 当前分支流程以 `AGENTS.md` / `BRANCHING_SPEC.md` 为准：核仓库、WIP、worktree 后 fetch，普通新目标从最新 origin/main 建短分支，承载完整功能及必需配套。
- 尚未进入 main 的源分支保留；完整功能及配套进入远端 main、读回包含关系后，按已有授权清理本地/远程功能分支。
- 保护 `main`、未合或其他 worktree 在用的分支；不强推、不按过期天数自动删除，不把释放 worktree 当作分支交付。
- archive 的 personal/dev 团队原文仅作历史资料，不作为当前操作指令；版本、精确公共包和外部发布授权按现行规范执行。

## 数据库规则

- 迭代版本涉及表结构变动时，同步权威 DDL、实体与测试 schema，并记录 migration 和回滚影响。
- 表结构变更 SQL 必须关联对应需求与交付记录，已执行脚本按现行 DDL 规则追加迁移。
- 每个数据库状态字段都必须有对应枚举类。
- 整数状态枚举必须实现 `IEnum<Integer>`，用于接口返回和中文说明输出。
- `type`、`state` 等业务字段由具体业务模块定义，不写入 `EntityBase`。
- 多租户字段 `tenant_id` 由框架自动处理，业务 SQL 不重复拼接。
- 逻辑删除字段 `is_delete` 由 MyBatis-Plus 逻辑删除能力处理，业务查询不重复拼接。

## 乐观锁规则

- 任何先查询后修改的操作，都必须把数据库记录的版本号写入修改对象的 `version` 字段。
- `version` 是 MyBatis-Plus 乐观锁字段，对应 `EntityBase.@Version`，不是 HTTP 请求头版本号。
- 更新逻辑不能绕过 `version` 直接覆盖数据。
- AI 生成 Service 更新方法时，必须检查入参或查询对象是否携带数据库记录的 `version`。
- 如果业务需要先加载实体再更新，更新前要保留数据库当前版本号，并在提交更新时交给 MyBatis-Plus 乐观锁插件校验。
- 常规更新优先使用 MyBatis-Plus 内置 `updateById(entity)`，不要生成只按 `id` 更新且不携带旧 `version` 的写法。

## 错误码规则

错误码完整表见 `archive/utils-markdown/ErrorCode.md`。AI 编码时先按错误来源选择分类：

| 分类 | 含义 | 常见场景 |
| --- | --- | --- |
| `A` | 用户端错误 | 参数错误、登录失败、无权限、用户版本异常、重复请求 |
| `B` | 当前系统错误 | 程序执行失败、超时、限流、资源耗尽、业务逻辑异常 |
| `C` | 第三方服务错误 | RPC、消息、缓存、配置、网络、数据库、通知服务异常 |

常用错误码优先级：

- 成功使用 `0 | 成功`，代码中优先复用 `ReturnCode.成功`。
- 登录密码错误使用用户侧登录错误，例如 `A0210 | 用户密码错误`。
- 未授权或权限不足使用访问权限异常，例如 `A0301 | 访问未授权` 或 `A0312 | 无权限使用 API`。
- 参数缺失使用 `A0410 | 请求必填参数为空`。
- 参数格式错误使用 `A0421 | 参数格式不匹配`。
- 重复请求使用 `A0506 | 用户重复请求`。
- 乐观锁更新失败时使用项目已有并发修改或业务失败返回码；不要再按 HTTP 请求头 API 版本校验处理。
- 数据库表、列、死锁、主键冲突等基础设施问题归入 `C03xx`。

## AI 编码前检查

- 是否已阅读 `PROJECT_CODING_SPEC.md`。
- 是否已阅读本文件。
- 是否需要打开 `archive/utils-markdown/ErrorCode.md` 查完整错误码。
- 是否需要打开 `archive/utils-markdown/Announcement.md` 核对历史原文；当前操作仍按 AGENTS/BRANCHING，不恢复旧分支流程。
- 是否需要打开 `archive/utils-markdown/Specification.md` 核对 Java 通用规范。
- 是否避免把公共规范重新放回运行时 `src/main/resources`。
