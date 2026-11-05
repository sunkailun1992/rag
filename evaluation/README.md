# RAG 检索评估基准

本目录实现升级计划的阶段 0：在迁移解析和检索引擎前，用同一语料、问题和指标保留可比较的基线。评估器只用 Python 标准库，默认离线，不调用任何模型或服务。

## 数据与适用范围

- `corpus.jsonl`：28 篇人工编写的合成技术文档，其中 `eval-a` 26 篇、`eval-b` 2 篇；知识库均为 `synthetic-tech`。
- `questions.jsonl`：60 道题，包含中文/英文、版本比较、同词异义、52 道可回答题、4 道无答案题与 4 道租户隔离题。
- Atlas 系列名称、阈值、编号和协议均为**虚构测试规则**，不描述任何真实产品、人员、客户或业务。语料无需从外部下载；仅在专用测试租户/知识库导入。
- 这套小语料用于工程回归，不能证明真实用户问题、扫描件 OCR、表格解析或大规模检索质量。真实验收还需要经授权的脱敏样本与人工相关性标注；不能把合成答案或自己构造的完美响应当作模型评测结果。
- 每题 `relevance` 以稳定语料 `id` 为键、1–3 为相关性等级。运行时 Java document/chunk UUID 会变化，因此默认在**来源文档级**评估；从 Java `source` 或引擎 `payload.source_name` 匹配完整 `sourceName`。不是切片级准确度。

## 导出并准备语料

在仓库根目录运行：

```sh
python3 -m evaluation.export_corpus --output build/evaluation-corpus
```

命令生成 `eval-a/synthetic-tech/` 与 `eval-b/synthetic-tech/` 下的 DOCX，不联网、不上传、不修改数据库。通过现有文档上传入口分别以两个测试租户身份导入；使用文件名原样上传，不能把两个租户的资料一起导入同一租户。不要将这些文件导入真实业务知识库。

## 离线评估

保存的 JSONL 每行对应一题，包含 `id`、可选 `latencyMs`，以及 `response` 原始检索响应。支持 Java HTTP 的 `ApiResponse.data.chunks`、RPC DTO 的 `chunks` 或 Python 引擎的 `hits`：

```json
{"id":"q001","latencyMs":120,"response":{"success":true,"data":{"enabled":true,"matched":true,"chunks":[{"source":"cache-v1.docx / 段落1","chunkId":"runtime-chunk-id"}]}}}
```

也接受 HTTP 采集器保存的精简格式：

```json
{"id":"q001","latencyMs":120,"httpStatus":200,"sources":[{"sourceId":"cache-v1","tenantId":"eval-a","knowledgeBase":"synthetic-tech"}]}
```

必须提供所选题目的完整结果，缺失或重复题号不能当作成功。原始响应可能包含正文，应保存在 `build/` 等 Git 忽略目录；不要提交实际服务返回的数据。

```sh
python3 scripts/evaluate-rag.py \
  --predictions build/rag-predictions.jsonl \
  --output build/rag-evaluation.json \
  --k 6 --min-recall 0.90 --min-mrr 0.80 --max-p95-ms 5000
```

上述质量阈值是**命令示例**，实际门槛应先用当前 Java 链路测基准，再定新引擎不可退步的条件。报告包含逐题 ID、来源 ID 和统计，不包含查询与文档正文。

## 显式 HTTP 采集

只有传 `--endpoint` 才联网。它会触发所连服务的 embedding/rerank，可能产生模型费用。`RAG_EVAL_TOKEN` 从本机私有环境提供；不要把 Token 写在命令行、文件或 Git 中。Java 的 Token 必须属于 `--tenant` 指定测试租户；`X-Tenant-Id` 不替代登录授权。

```sh
python3 scripts/evaluate-rag.py \
  --endpoint http://localhost:7700/api/rag/retrievals --api java \
  --tenant eval-a --save-predictions build/java-predictions.jsonl \
  --output build/java-evaluation.json --k 6

python3 scripts/evaluate-rag.py \
  --endpoint http://localhost:8091/v1/retrievals --api engine \
  --tenant eval-a --save-predictions build/engine-predictions.jsonl \
  --output build/engine-evaluation.json --k 6
```

端口和地址仅为本机示例，使用已配置服务的实际地址。引擎模式使用服务间 Token；不要把该凭据给浏览器。采集器拒绝重定向，不向跳转目标转发 Token；不保存正文、查询、原始错误或凭据。所有请求都有超时与响应大小限制。跨租户测试要求 `eval-b` 语料已导入，否则“无泄漏”没有判别力。

## 指标与门禁

- **Recall@k**：每道可回答题在前 k 个返回位置命中的不同相关文档数 / 标注相关文档总数，再宏平均。
- **MRR@k**：前 k 位第一篇相关文档排名倒数，再对可回答题宏平均；未命中为 0。
- **nDCG@k**：增益 `2^grade - 1`，折损 `log2(rank + 1)`，用理想排序归一，再宏平均。重复来源占用排名但只在第一次命中计增益。
- 无答案题不混入正向召回均值，单独统计 `correctEmptyCount`、`falsePositiveCount`。服务禁用、HTTP 错误、返回 unavailable 或缺预测都不算正确空召回。
- `falseNegativeCount` 是可回答题没有相关命中的数量；`errorCount` 包含服务/契约错误与缺失响应。
- `scopeLeakCount` 检查全部返回来源的语料归属、禁止来源和引擎 payload 租户/知识库。Java DTO 没有租户字段，靠已标注的来源归属判断；该小语料检查不能代替服务权限测试。
- p50/p95 使用线性插值；失败请求耗时也计算。启用延迟门槛时每题都必须有计时，不接受用少数成功样本冒充完整时延。
- 缺失响应、未知来源、隔离泄漏、错误空召回导致门禁失败；`--min-recall`、`--min-mrr`、`--max-p95-ms` 可显式增加质量与时延门槛。
- 退出码：0 门禁通过；1 评估完成但门禁失败；2 输入、文件或参数错误。

## 可复现验证与后续评估平台

```sh
python3 -m unittest discover -s evaluation/tests -v
```

测试覆盖手工算出的排序指标、重复结果、跨租户泄漏、未命中、接口失败、空集、DOCX 生成以及真实本机 HTTP 采集和重定向阻断。它们证明评估器行为，不表示检索器已经达到某个分数。

后续可把 question ID、人工标注和实验版本接入 Ragas/Langfuse。当前没有安装或调用这两个平台，没有向云端上传问题与正文，也不把纯检索指标包装成回答忠实度或 OCR 准确度。需要保留模型、维度、语料哈希、服务 Git SHA、配置摘要和运行日期，再比较不同实验。

## 真实 dense / hybrid 对比

`live_compare.py` 复用现有 28 篇合成文档与 60 题，调用 `rag_engine.retrieval.retrieve` 的真实实现：Qwen query embedding → LlamaIndex dense 或 dense + `lexical-v1` RRF → Qwen rerank。两种模式共用一套文档向量和一个唯一临时 Qdrant collection，逐题交替先执行 dense / hybrid，减少固定先后顺序对网络延迟的影响。

此入口评估的是 **Python 引擎内检索链路**，不经过 Java 门面、HTTP 服务身份认证、RPC 或浏览器。3 秒预算与 FastAPI 入口一致，涵盖 query embedding、hybrid readiness 检查、召回、RRF、rerank；Java 端到端 5 秒仍需另外验收。它不上传 DOCX、不测解析/OCR、不调用回答生成模型，因此不计算 Ragas faithfulness / answer correctness。没有生成答案时不能声称“回答忠实度已通过”。

先在仓库根目录执行不联网的预览：

```sh
python3 -m evaluation.live_compare
```

默认输出 `build/evaluation-live/report.json`（状态 `planned`）和 `build/evaluation-live/langfuse-dataset.csv`，不读取凭据、不创建集合、不调用模型。真实运行复用 `engine/.venv` 的已有依赖，不安装全局包或 Ragas/Langfuse 服务。

真实运行的最小环境变量由私有环境注入：

| 变量 | 用途 |
|---|---|
| `RAG_ENGINE_QDRANT_URL` | 实际 Qdrant HTTP 地址；不得嵌入用户名、密码或查询串 |
| `RAG_ENGINE_QWEN_BASE_URL` | 现有引擎使用的 Qwen compatible-mode API 基地址 |
| `RAG_ENGINE_QWEN_API_KEY` | 模型凭据，只从环境读取，不写入 CLI 参数或 Git |
| `RAG_ENGINE_QDRANT_API_KEY` | Qdrant 开启认证时才需要 |
| `RAG_ENGINE_EMBEDDING_MODEL` | 可选，默认 `text-embedding-v4`；摄取与查询共用 |
| `RAG_ENGINE_EMBEDDING_DIMENSION` | 可选，默认 `1024`；临时集合与模型维度一致 |
| `RAG_ENGINE_RERANK_MODEL` | 可选，默认 `qwen3-rerank` |
| `RAG_ENGINE_RERANK_SCORE_THRESHOLD` | 可选，默认 `0.5`；两种模式使用同一 rerank 相关度阈值，实值记录到报告 |

不需要现有引擎服务的 `RAG_ENGINE_API_KEY`；脚本不监听端口。既有 `RAG_ENGINE_COLLECTION` 不会被采用，临时名称只由脚本生成且必须匹配 `rag_eval_<32位随机十六进制>`，没有指定任意 collection 的命令参数。不会访问、修改或删除 `ai_chat_files`，也不调整 Nacos。

注入上述环境后，显式确认真实调用与临时写入：

```sh
engine/.venv/bin/python -m evaluation.live_compare --apply \
  --output build/evaluation-live/report.json \
  --langfuse-csv build/evaluation-live/langfuse-dataset.csv
```

一次完整运行的费用边界是：文档 embedding **28 篇，仅构建一次，分 3 批**；两种模式各 60 次 query embedding，共 120 次；最多 120 次 rerank。不会自动重试、不会自动调参后再跑。总计最多 123 次 embedding 请求及 120 次 rerank 请求，实际计费按模型输入量及账户价格确定。分批上限 10 来自 [Qwen embedding 官方文档](https://www.alibabacloud.com/help/en/model-studio/embedding)。默认候选数 30、topK 6、dense 相似度阈值 0.2；可显式传 `--candidates`、`--k`、`--threshold`，值写入报告，不根据本批答案自动优化。

执行流程及证据：

1. GET 核对临时名称不存在，再生成真实文档 embedding；任何已有同名集合都立即拒绝写入和清理。
2. 创建临时 dense + `lexical-v1`（IDF）集合，写入两租户数据，读回各租户点数并检查完整 sparse readiness。语料中 `eval-b` 的两篇隔离文档必须实际存在。
3. 依次执行 60 对真实查询；每题结果只保存题号、来源 ID、作用域、耗时、实际 mode、降级标志和稳定错误码。报告不保存查询、正文、向量、服务 URL 或凭据。
4. `finally` 删除本次唯一集合，并 GET 读回 **404**。创建响应丢失、后续写入失败、单题超时、普通中断均会走清理。报告里的 `cleanup.verifiedAbsent` 必须为 `true`；删除请求返回成功但 GET 仍为 200 不算成功。强制 `kill -9`、主机断电或网络持续不可达不能保证即时清理；报告在写入前已记录唯一临时名称，需恢复连接后核对残留，不能批量删除其他集合。

报告区分 `status=completed`（比较已执行）和 `acceptance.passed`（门禁通过）。其中包含两组 Recall@k / MRR@k / nDCG@k、全部请求 p50/p95、正向漏召回、负向误召回、泄漏、被引擎拒绝的作用域错误、3 秒超时、rerank 降级，以及 hybrid 减 dense 的指标差值。成功返回的空集、错误和缺失结果分别统计。默认门禁要求完整结果、无泄漏、来源已知、负向题正确为空、没有降级、实际 mode 正确、hybrid 三项排序指标不退步以及清理成功；它不会把 8 道负向题的误召回包装成通过。

报告保留语料、问题集、引擎源码和 lockfile SHA-256、Git HEAD、依赖版本、模型和参数。工作树未提交时以源码哈希补足 Git HEAD。单次 60 题合成结果只适合作为回归证据，即使 hybrid 分数更高，也不能据此证明真实用户质量提升或统计显著性；性能结论同样不是并发压测。

`predictions.<mode>[].sources[].score` 保留每个实际返回命中的数值分数；`results.<mode>.topScoreDistribution.groups` 分正向、全部负向、无答案、租户隔离四组统计第一条命中的 min / p05 / p25 / p50 / p75 / p95 / max / mean。非降级与降级结果分开，避免把 dense/RRF 分数和 rerank 相关度放在同一尺度上。统计的是**引擎过滤后实际返回的首条分数**，不是过滤前候选最高分；空召回、失败和旧报告缺分数分别计数，不以 0 填补，不据此自动调阈值、改标签或放宽门禁。保留失败基线再重跑新配置，才能比较修复前后。

退出码：0 为预览完成或真实门禁通过；1 为已执行但结果、清理或门禁失败；2 为参数、依赖、环境或输出准备错误。完整诊断看脱敏报告，不需要回显密钥。

### Langfuse 数据集导入

CSV 有 `input`、`expected_output`、`metadata` 三列，每格为 JSON。它**有意包含本仓库公开的合成问题文本**，与不保存查询正文的运行报告分开；不导出实际用户问题或服务响应正文。新建空数据集后在 Langfuse UI 选择 Upload CSV，依次映射这三列；`expected_output` 是来源相关性等级、空召回期望和禁止来源，**不是生成答案**。格式依据 [Langfuse 官方数据集导入说明](https://langfuse.com/docs/evaluation/experiments/datasets)。脚本只产出本机文件，不创建云端数据集，不读取 Langfuse 凭据。

### 脚本回归

```sh
engine/.venv/bin/python -m unittest discover -s evaluation/tests -v
```

新测试通过可控 HTTP transport 调用真实 engine 检索实现，验证 dense/hybrid 路由、批次乱序对齐、两租户读回、报告脱敏、超时、创建响应丢失、写入失败、中断和删除后 404；所有模型返回均为测试替身，**这些单元/协议测试不产生真实模型效果结论或费用**。使用普通 Python 时仅依赖标准库的测试照常运行，依赖 engine 的协议测试会明确 skipped。
