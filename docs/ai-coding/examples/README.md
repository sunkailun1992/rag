# 公共 AI 示例模板

本目录用于存放 Java 微服务 AI 编码示例模板；在 `utils` 项目中是公共源头，在业务微服务中是本地可读副本。

## 同步规则

- 公共示例模板必须优先在 `utils/docs/ai-coding/examples` 修改。
- `user/docs/ai-coding/examples`、`message/docs/ai-coding/examples` 和 `report/docs/ai-coding/examples` 只保留本地可读副本，方便 AI 在单个业务项目内快速学习。
- 修改公共示例模板后，必须从 `utils/docs/ai-coding/examples` 同步到所有使用该模板的业务微服务。
- 业务微服务不得单独修改公共 `Example*` 模板；确实有业务专属示例时，放到业务项目自己的 `docs/ai-coding/project-examples` 或 `docs/ai-coding/business-examples`。
- 同步后必须检查业务项目 AI 规范入口，确认仍然说明 examples 是本地副本而不是业务私有模板。

## 当前同步目标

```text
user/docs/ai-coding/examples
message/docs/ai-coding/examples
report/docs/ai-coding/examples
```
