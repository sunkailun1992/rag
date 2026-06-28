# 分支管理规范

本规范用于 AI 和人类统一管理 Git 分支、发布分支、临时工作分支和分支清理。当前项目族采用 `main + 短生命周期分支 + PR/CI + tag 发布 + Nacos 环境隔离`，避免长期 `dev/test/prod` 代码分支导致代码、配置、数据库脚本和 RPC 契约漂移。

## 核心原则

- `main` 是唯一长期主干，必须始终可构建、可测试、可作为发布来源。
- 功能、修复、文档和运维改动都从 `main` 切短分支，完成后合回 `main` 并删除分支。
- 环境不通过长期分支表达；`dev`、`test`、`prod` 是 Nacos namespace 或部署环境，不是 Git 长期分支。
- 发布版本通过 `build.gradle version`、前端/小程序制品版本、Git tag 和发布记录表达，不通过分支名表达。
- 金丝雀、灰度、Dubbo tag、权重和注册元数据属于运行时治理，不用 Git 分支切换流量。
- 分支越短越好；长期存在的分支越少越好。

## 长期分支

长期保留的分支默认只有：

```text
main
```

`main` 的要求：

- 不能提交无法编译、无法启动或明显破坏核心流程的代码。
- 合入前必须完成与改动风险匹配的本地验证和 CI 验证。
- 合入生产代码、运行时配置、数据库脚本、构建脚本或公共示例时，必须同步遵守 `VERSIONING_SPEC.md`。
- 发布时从 `main` 的确定 commit 构建制品，并打不可变 Git tag。

不推荐长期保留：

```text
develop
dev
test
staging
prod
production
```

只有当外部平台强制要求或团队已有成熟流程时，才允许额外长期保护分支；新增前必须写清触发条件、合并方向、保护规则和清理策略。

## 临时分支类型

统一使用以下前缀：

```text
feature/<scope>-<short-desc>
fix/<scope>-<short-desc>
hotfix/<scope>-<short-desc>
docs/<scope>-<short-desc>
chore/<scope>-<short-desc>
refactor/<scope>-<short-desc>
test/<scope>-<short-desc>
release/<version>
```

含义：

- `feature/*`：新增用户可见功能、接口、页面或业务能力。
- `fix/*`：普通缺陷修复，尚未影响线上紧急发布。
- `hotfix/*`：线上紧急修复，必须优先验证、优先合并、优先发布。
- `docs/*`：README、AI 规范、接口说明、发布说明等纯文档。
- `chore/*`：CI、脚本、依赖维护、仓库配置、非业务构建调整。
- `refactor/*`：不改变外部行为的结构重构。
- `test/*`：测试补充、测试框架修正或测试数据调整。
- `release/*`：临时发版冻结分支，仅在多仓库联动、上线窗口冻结或正式发布验收时使用。

## 命名规则

分支名必须：

- 使用小写字母、数字和短横线。
- 使用 `/` 分隔分支类型和名称。
- 名称能看出模块和目标。
- 避免中文、空格、下划线、特殊符号和过长描述。
- 一个分支只做一个目标，不混入无关需求。

推荐：

```text
feature/user-tenant-switch
feature/ai-questionnaire-outbox
fix/gateway-swagger-routes
fix/report-unread-count
hotfix/ai-chat-timeout
docs/versioning-rules
chore/update-dubbo-config
refactor/utils-tenant-context
test/user-auth-controller
release/1.4.1
```

不推荐：

```text
dev
test
prod
mywork
new-feature
fix
temp
backup
feature/all
feature/update
feature/20260625
```

## 标准开发流程

普通功能和修复：

```text
1. 从 main 拉取最新代码。
2. 从 main 创建短分支。
3. 在短分支完成开发、测试和文档更新。
4. 推送短分支到远程。
5. 发起 PR 或等价 review。
6. CI 通过后合并回 main。
7. 合并后删除远程分支。
8. 本地执行 prune 并删除已合并本地分支。
```

命令示例：

```bash
git switch main
git pull --ff-only
git switch -c feature/user-tenant-switch
git push -u origin feature/user-tenant-switch
```

合并后清理：

```bash
git fetch --prune
git switch main
git pull --ff-only
git branch --merged main
git branch -d feature/user-tenant-switch
git push origin --delete feature/user-tenant-switch
```

## Release 分支

`release/<version>` 只在需要冻结发布时使用，不是长期环境分支。

适用场景：

- 多仓库需要同一窗口一起发版。
- 测试阶段需要只接收修复，不继续接收新功能。
- 正式发布前需要保留一个验收快照。

流程：

```text
1. 从 main 创建 release/<version>。
2. 只允许合入阻塞发版的 fix、docs 和 release chore。
3. 验证通过后从 release 分支或对应 main commit 打 tag。
4. 发布完成后把 release 修复回合 main。
5. 确认 main 包含所有 release 修复后删除 release 分支。
```

命令示例：

```bash
git switch main
git pull --ff-only
git switch -c release/1.4.1
git push -u origin release/1.4.1
```

发布完成：

```bash
git tag -a v1.4.1 -m "release: v1.4.1"
git push origin v1.4.1
git switch main
git merge --ff-only release/1.4.1
git push origin main
git push origin --delete release/1.4.1
git branch -d release/1.4.1
```

如果 release 分支上有无法快进合并的修复，必须通过 PR、merge 或 cherry-pick 回 `main`，不能让 release 修复只留在 release 分支。

## Hotfix 分支

`hotfix/*` 用于线上紧急修复。

流程：

```text
1. 从当前线上 tag 或 main 创建 hotfix 分支。
2. 只修改线上故障必需内容。
3. 执行最小但足够的验证。
4. 合并回 main。
5. 打新的 patch tag。
6. 删除 hotfix 分支。
```

规则：

- hotfix 不混入重构、格式化和非必要依赖升级。
- hotfix 必须说明线上影响、回滚方案和验证证据。
- 如果 hotfix 从旧 tag 切出，发布后必须把修复同步回 `main`。

## Tag 和版本

正式发布必须使用 tag 标记：

```text
v<major>.<minor>.<patch>
```

示例：

```text
v1.4.1
v1.4.2
```

规则：

- tag 指向实际发布的 commit。
- tag 不复用、不移动、不删除后重建，除非明确说明事故原因并通知所有消费者。
- tag 与 `VERSIONING_SPEC.md` 的制品版本一致。
- 多仓库联动发布时，每个仓库都在各自发布 commit 打对应 tag；发布说明写清跨仓库 commit 集合。

## 环境和 Nacos 边界

Git 分支不表达环境。

推荐环境模型：

```text
代码主干：main
制品版本：build.gradle version / package version / 小程序上传版本
发布标记：Git tag
环境隔离：Nacos namespace dev / test / prod
配置分组：DEFAULT_GROUP
灰度治理：Dubbo metadata / tag / weight / release version
```

禁止用这些分支表达环境：

```text
dev
test
prod
production
```

Nacos 规则：

- `dev`、`test`、`prod` 应该是 namespace 或部署环境。
- 配置 group 默认使用 `DEFAULT_GROUP`。
- 同名 dataId 在不同 namespace 中保存不同环境值。
- 只改 Nacos 远程配置且不提交仓库文件时，不创建发布分支，不提升仓库制品版本；交付说明必须写清 namespace、group、dataId 和读回验证。

## 多仓库协作

涉及多个仓库时：

- 每个仓库各自从 `main` 切同名或同主题短分支。
- 公共包先改、先发本地制品、先验证消费者。
- 消费者只依赖已发布的精确公共包版本。
- 多仓库联动发布时，使用发布清单记录每个仓库 commit、tag、制品版本和 CI 结果。
- 不允许把其它仓库源码复制进当前仓库。

示例：

```text
utils:   feature/traffic-context
gateway: feature/traffic-context
user:    feature/traffic-context
report: feature/traffic-context
ai:      feature/traffic-context
```

## 分支清理规则

远程分支清理：

- PR 合并后立即删除远程 `feature/*`、`fix/*`、`docs/*`、`chore/*`、`refactor/*`、`test/*` 分支。
- `hotfix/*` 发布并同步回 `main` 后立即删除。
- `release/*` 发布完成且修复同步回 `main` 后立即删除。
- 超过 7 天无更新的普通短分支需要 rebase/merge 最新 `main` 并确认是否继续。
- 超过 14 天无更新的普通短分支默认标记 stale，需要关闭、删除或重新立项。
- 超过 30 天仍未合并的短分支必须拆分、废弃或重新评审，不允许默默长期保留。

本地分支清理：

```bash
git fetch --prune
git branch --merged main
git branch -d <branch-name>
```

删除远程已合并分支：

```bash
git push origin --delete <branch-name>
```

查看远程已删除但本地仍记录的分支：

```bash
git remote prune origin --dry-run
git remote prune origin
```

查看长时间未更新分支：

```bash
git for-each-ref --sort=committerdate refs/heads refs/remotes \
  --format='%(committerdate:short) %(refname:short)'
```

清理前必须确认：

- 分支已经合并，或分支内容确认废弃。
- 分支上没有唯一存在的发布修复、数据库脚本或配置变更。
- 多仓库联动任务中其它仓库不再依赖该分支。

## AI 操作要求

AI 在修改代码或文档前：

- 先确认当前分支，不在未知分支上盲改。
- 如果用户未要求创建分支，默认在当前分支工作，但必须报告当前分支。
- 如果当前分支不是 `main`，必须理解该分支目的，避免把无关改动混入。
- 不自动删除用户分支；清理分支前必须确认该分支已合并或用户明确要求。
- 不自动移动、重建或删除 tag。

AI 在交付说明中必须写明：

- 当前工作分支。
- 是否创建了新分支。
- 是否涉及版本提升。
- 是否涉及 release/hotfix。
- 是否有需要清理的临时分支。
- 是否已推送，CI 状态如何。

## 禁止事项

- 禁止用长期 `dev/test/prod` 分支替代 Nacos namespace。
- 禁止一个分支混入多个无关需求。
- 禁止在未同步 `main` 的陈旧分支上继续堆积大改动。
- 禁止 release 分支长期存在并继续开发新功能。
- 禁止 hotfix 混入非必要重构。
- 禁止删除未合并且未确认废弃的远程分支。
- 禁止修改、移动或复用已发布 tag。
