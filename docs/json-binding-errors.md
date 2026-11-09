# JSON 请求体绑定错误

RAG Java 门面的 JSON 语法错误和无法转换为请求 DTO 的 JSON 类型，应由
`GlobalExceptionHandler` 的 `HttpMessageNotReadableException` 映射返回 HTTP `400`、
统一业务码 `A0400` 和固定提示“请求体 JSON 格式不正确”。这些请求在进入检索业务服务前
拒绝，不调用 Engine、模型或图谱构建。

Spring MVC 的真实外层异常是 `HttpMessageNotReadableException`；Jackson 的
`UnexpectedEndOfInputException` 等只可能是内部 cause。安全日志中的最深异常类不能直接
替代 MVC 映射依据。该 handler 只记固定警告，不输出异常对象、消息、堆栈、请求正文或
解析位置；响应也不回显解析细节。

回归使用实际 Spring Boot MVC slice、生产 Controller、安全配置和 Jackson 转换链，
覆盖截断字符串、截断对象、顶层类型不匹配三种输入。测试先确认实际外层异常，再检查
状态、统一响应、日志不含正文/Throwable，以及检索服务没有被调用。原有权限拒绝、上游
`502` 和真正的未预期服务异常 `500` 继续保留；合法的大空白查询仍沿现有检索契约处理，
不会因本修复调用外部模型。

此修复不改变检索总预算、降级策略或 Engine。已知业务检索的 `200` 兼容降级仍需检查
`matched`、来源数量和下游 trace，不能把 HTTP 成功等同于有知识命中。真实运行态修复
仍需构建新 Jar、受控重启和重新验收，局部 MVC 测试不代表当前运行中的旧 Jar 已变化。
