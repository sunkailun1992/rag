# 分支管理规范

> 个人 GitHub 当前仅保留 main 长期分支；下文 test/preprod 的通用流程不适用于本仓库，不得据此重建这两条分支。短功能分支验证交付后按授权清理。
本项目使用 `main`、`test`、`preprod` 三个长期分支与短功能分支。Git 集成、CI、制品发布、环境部署和实际验收分别取证；创建或合并分支不会自动切换 Spring profile 或 Nacos namespace。

## 长期分支与环境边界

| 分支 | 职责 | 保护要求 |
|---|---|---|
| `main` | 正式主干与完整交付基线 | 不删除、不强推；从确定 commit 构建和记录正式制品 |
| `test` | 多功能整合与测试验证 | 不删除、不强推；记录实际集成范围与验证 commit |
| `preprod` | 完整候选的预发验证 | 不删除、不强推；候选必须包含必需跨仓配套并可回滚 |

- 缺少长期分支时，只在用户授权下从确定的已验证 main 创建；已有分支的历史保持，不通过 force/reset 重建。
- 分支名不等于运行环境。当前个人本地入口仍默认 `dev` / `github-dev`，服务根目录 `config/application-dev.properties`、环境变量优先级和 base `application.yml` 保持现行规则。
- 不因新增 test/preprod 分支复制公司的自动部署脚本、私有化分支、医疗业务资源或生产配置。是否触发 CI/部署以仓库实际 workflow 和单独授权为准。
- 用户、其他会话明确保留的长期分支也受保护，不因为未列在示例中就删除。

## 任务起点与完整功能

1. 修改前核对实际仓库、分支、HEAD、dirty、其他会话和 worktree；先 `git fetch origin`，默认从最新 `origin/main` 的确定 SHA 创建同主题短分支。同一功能可核对归属和基线后续用原分支；只读任务不要求建分支，用户明确指定的基线按当次授权记录。
2. 短分支只承载一个完整目标，包括必要实现、测试、文档、migration、公共契约和依赖。不得夹带他人 dirty 或为方便而整仓复制。
3. 跨仓功能在各仓记录固定 HEAD、接口/制品精确版本、配置与 migration；公共库先验证，再验证消费者，缺少必需配套不能称为完整交付。
4. 远端 main 前进时先同步最新基线并重新执行风险匹配验证；不强推改写已共享历史，不 stash/reset/clean 他人工作来制造干净状态。

短分支可使用 `feature/`、`fix/`、`docs/`、`chore/`、`refactor/`、`test/`、`release/`、`hotfix/` 或本任务使用的 `codex/` 前缀，名称使用小写英文与短横线。

## 集成、候选与主干交付

- 经授权把完整功能集成到 test，在确定 commit/制品上验证；需要预发时，以完整功能及必需配套组成 preprod 候选并记录范围。
- test 可能同时包含多个目标，不默认整条合入 preprod 或 main；只有用户确认整个分支就是本批候选时才整体交付。不得用零散 cherry-pick 拼装普通功能来绕过依赖验证。
- main 候选基于最新主干与同一完整功能，验证候选本身后整体 merge/快进合入 main。只进入 test/preprod 尚不代表正式主干已交付，源分支继续保留。
- Review 可按需补充，但不增加已授权提交、合并或推送的人工批准门禁。认证、授权、租户、数据库、公共契约等仍须完成专项验证；CI 状态、未验收项和失败不能隐瞒。
- 会触发自动化的跨仓 push/merge 按用户指定顺序执行并读回实际状态，不把 push 成功当作 CI 或部署完成，不通过跳过 CI 修改规则来规避观察。
- 编辑或建分支不等于 commit/push/merge 授权；Git 集成也不等于 tag、Maven 发布、部署或回滚授权。已给出的当前任务授权持续有效，不机械重复索要。

## 合并后的分支清理

用户已授权本任务合入 main 并清理功能分支时，满足下列条件后执行远程与本地清理，无需重复确认：

1. 完整功能和必需跨仓配套已进入各自 main，风险匹配验证有明确结果。
2. fetch 并读回远端 main，核对功能固定 HEAD 的完整包含关系；不能只看本地 merge 或部分提交等价。
3. 远程功能分支仍指向本次核对的 HEAD；若被其他人推进，先保留并查明，不能删除新工作。
4. 分支不属于 main/test/preprod 或其他明确保留的长期分支，没有未交付的唯一提交、WIP 或在途依赖。
5. `git worktree list --porcelain` 确认没有其他 worktree/会话使用该分支；占用、dirty 或用途不明时保留并说明，不用 `-D` 绕过。

```bash
git fetch origin
git merge-base --is-ancestor <功能固定SHA> origin/main
git worktree list --porcelain
# 以上核对均满足，且本任务已有清理授权后：
git push origin --delete <本任务功能分支>
git switch main
git branch -d <本任务功能分支>
git fetch --prune origin
```

- 本地/远程分支不存在时按实际状态记录，不为了凑清理动作重建。
- 只进 test/preprod、未合并、包含关系不清或被其他 worktree 使用的功能分支不自动删除。过期只能提示复核，不能按天数直接删除。
- worktree 目录与分支分开管理；释放干净且有远端保存的本任务 worktree 不等于删除尚未交付的源分支。不清理其他会话或唯一未推送提交所在目录。
- squash/等价交付需保留完整差异与验证证据，不能仅凭 `git cherry` 或部分 patch 判定可删；`git branch -d` 拒绝时停止核对，不默认改用强删。

## Release、Hotfix、版本与交付回执

- release/hotfix 都是短分支，只容纳候选冻结或故障所需内容；修复必须回到完整 main，再按实际集成需要同步 test/preprod。
- tag 指向实际发布 commit，使用不可变 `v<major>.<minor>.<patch>`；不移动、复用或覆盖已有 tag/同版制品。
- 版本按 `VERSIONING_SPEC.md`，公共库保留个人 `com` 坐标与精确依赖；纯规则/文档变化不额外提升业务版本。
- 交付说明列仓库、分支/固定 HEAD、main/test/preprod 实际集成状态、跨仓配套、验证、推送/CI、制品/部署、分支及 worktree 清理结果和未验证项，不能相互代替。
