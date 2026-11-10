# Engine 图 worker readiness 修复计划与验收边界

问题：旧实现启动后固定返回 health up；heartbeat 一次失败会终止任务并取消 pump，manager 对象仍存在，图谱请求还能进入来源检查并在剩余 DB lease 期间排队。0.2.8 与0.2.9的相关三个源文件相同，不能把升级旧0.2.9当作此缺陷修复，也不能把过期租约解释为正常按需释放。

1. 先用真实异步 task 与 ASGI 请求复现 heartbeat/pump 结束后健康仍200，覆盖来源检查期间失活的排队窗口。
2. GraphManager 公开只读 readiness：启动成功且两 task 运行、未取消。停止/失租立即关闭 readiness；保留原 fail-stop、DB owner/lease fencing，不自动抢占或重启。
3. 开启图谱且不可用时 health503；图谱入口及来源 await 后的排队前再次拒绝，返回固定503错误。关闭图谱健康保持200，探针不查询数据库/模型。
4. 失租事件用ERROR及有限failure_class，只允许固定lease模板将分类写本地JSON/Engine OTLP，未知类别降为UnexpectedError，不导出异常原文或stack。
5. 同步Engine0.3.0版本/lock/resource、OpenAPI、当前文档和模型凭据归属；Java版本与物理模型/向量维度/缓存/图算法不变。
6. 跑定向与完整Python、evaluation、ruff、OpenAPI、secret/diff检查；主会话独立review后整合main/CI/固定镜像，再做受控实际验收。

回滚与部署：受控重新创建可重新获取租约，但旧版会再次隐瞒后台任务失活；不能回退健康语义后宣称已修复。实际部署需先核activeJobs=0、旧worker终态及知识数据基线，保留旧CID日志和原始state.before，按新镜像身份及新worker heartbeat读回。源码测试不会修改NAS或已有数据，不自动重跑任何付费任务；中央Collector保留failure_class另行验证。
