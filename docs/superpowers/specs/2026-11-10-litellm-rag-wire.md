# RAG 模型网关协议与向量完整性

本轮由主会话授权实施，Java1.2.4→1.2.5、Engine0.2.8→0.2.9，同批仅升一次；源码、镜像、NAS切流和真实付费效果分别验收，本阶段不提交推送或访问运行配置。

## 已证缺口

- Java QwenEmbeddingClient 用 asInt 将缺失索引当0，排序后仅校验数量；重复/越界索引可与错误切片配对，部分数值会零值回退，float溢出未被拒绝。
- Python 查询 embedding 用 get(index,0)，缺索引可成为正常向量；必须明确一个索引0及有限的Float32可表示数值。
- rerank目前固定将 compatible-mode 派生为 compatible-api，再拼复数reranks。它不能代表显式网关的单数端点。

## 最小实现

Settings新增 `rerank_endpoint`（默认空）与 `rerank_wire`（dashscope/cohere，默认dashscope）。空endpoint仅在dashscope时沿用既有派生；cohere必须显式填写完整绝对HTTP(S)端点，不追加路径、不跟随重定向，不从请求接收URL。沿用现有模型认证配置和原0.8秒rerank/3秒总预算。

旧dashscope载荷保持；cohere向完整端点发送model/query/documents/top_n/return_documents=false，只解析results中的index与relevance_score到既有Hit。结果数量、唯一索引、范围、有限0..1分数及原阈值保持。HTTP/协议失败沿用degraded=true，Java继续拒绝降级候选作为正常上下文；不自动重试，不记录端点、正文或原始异常。

Java按每个embedding请求校验data为数组、数量相同、每个index显式整数且恰好覆盖0..N-1；拒绝重复/越界/缺失、非数/布尔、NaN/Inf、Float32溢出和维度不符。任一批失败不返回整个文档向量集，现有摄取在全部embedding成功后才写向量。Python当前查询请求数量固定1，必须显式index=0，再作同维度/数值验证；失败为既有EMBEDDING_INVALID_RESPONSE，不能继续Qdrant召回。

不更改物理embedding模型、1024维默认、rerank物理模型、缓存键、切片/提取语义、图谱算法、索引或数据。既有向量无需因纯协议适配重嵌入；实际网关必须仍映射相同物理模型，运维切流后由主会话有界真实验证。

## 验证与来源

新增真实loopback HTTP替身：Spring客户端与摄取Bean确认坏向量不upsert，含前一批成功、后一批失败；FastAPI→HTTPX→本机服务确认旧/新wire、精确路径/载荷、无重试、typed失败及坏embedding后无召回。旧正常测试响应补显式index，不放宽新校验。

官方协议依据：[LiteLLM rerank](https://docs.litellm.ai/docs/rerank)声明遵循Cohere请求响应；[Cohere v1 rerank](https://docs.cohere.com/v1/reference/rerank)定义results索引/相关度及return_documents=false。不从这些文档推定目标NAS已部署或模型质量已验收。
