# RAG Python 引擎

`rag/engine` 与 Java `rag` 同仓管理。Java 继续拥有认证、租户、知识库、文档版本、幂等、Qdrant 写入与活动来源权威；本引擎负责 PDF/图片解析、可选检索计算，以及显式开启后的 LlamaIndex/Neo4j 派生图谱。

当前实现：

- 文字型 PDF：pypdf 提取逐页文本，LlamaIndex `SentenceSplitter` 按字符预算及重叠切片，保留页码。
- 扫描 PDF、PNG/JPEG：转给运维指定的 PaddleOCR `/layout-parsing`，保留区块类型、页码和坐标。没有 OCR 地址时返回 `OCR_NOT_CONFIGURED`，不把缺页结果当成功。
- 检索：保留 Qdrant unnamed dense；可显式启用 `lexical-v1` TF-IDF 稀疏倒排索引，使用 LlamaIndex Retriever 编排双路召回、RRF、去重与千问 rerank。每次查询有租户、知识库、ACTIVE 状态过滤，返回前再次检查；模型/维度 metadata 必须与配置一致。
- 相关性：成功重排只保留 `relevance_score >= RAG_ENGINE_RERANK_SCORE_THRESHOLD` 的候选，默认门槛 `0.5`；全部候选低于门槛时返回空命中，避免无答案问题也硬凑 topK。
- 降级：rerank 单独失败可使用本次向量排序，响应明确 `degraded=true`；这条降级路径缺少相关性门槛保证，调用方应将结果视为未完成重排的候选，不能宣称“无答案”质量门禁仍通过。embedding、Qdrant 或隔离验证失败则整个请求失败，不能放宽过滤。
- 混合检索默认关闭。显式 schema 准备与完整回填 CLI 已提供；旧 Java 单写会失去 sparse，必须配套新摄取双写。查询改写、异步摄取、图像语义描述不在本引擎本轮实现中。

## 启动

使用 Python 3.12 和 uv，依赖锁定在 `uv.lock`；从仓库根执行：

```sh
cd engine
uv sync --frozen
# 先在当前 shell 注入 RAG_ENGINE_API_KEY（至少 32 字符），其余见 .env.example。
uv run --frozen uvicorn rag_engine.app:create_app --factory --host 127.0.0.1 --port 8091 --limit-concurrency 16 --no-access-log
```

缺少服务密钥或密钥过短会拒绝启动。不要将 Java 用户 JWT 当服务密钥。生产通过私有环境文件、Secret 管理或 Nacos 私有渲染注入；`.env` 不进入 Git。`uvicorn` 不自动读取 `.env`，本机可用 `--env-file .env` 显式读取；Compose 的 `env_file` 会读取它。

`compose.yaml` 只启动轻量引擎，限制 1.5 GiB / 1 CPU / 64 进程；`.env.example` 建议解析并发 1。容器不包含 PaddleOCR 权重，不自动下载任何 OCR/embedding 模型。重型 OCR 单独运行，通过 `RAG_ENGINE_OCR_URL` 接入。Compose 默认仅映射 loopback；跨主机访问需明确配置内网监听、TLS/反向代理及防火墙，不能直接公开到互联网。

## 内部协议

契约为 [openapi.json](openapi.json)，由应用生成，使用 `uv run python scripts/export_openapi.py` 更新。该文件与真实应用的 schema 有一致性测试。

| 接口 | 请求 | 返回 |
|---|---|---|
| `GET /health` | 无认证，只检查本进程 | `{status:"up"}`，不证明 OCR、模型、Qdrant 可达 |
| `POST /v1/parses` | `sourceName/title/contentBase64/maxChunkCharacters` | `title/sourceName/sourceType/chunks` |
| `POST /v1/retrievals` | `query/knowledgeBase/topK/candidateCount/similarityThreshold/maxContextCharacters` | `hits/mode/degraded` |
| `GET /v1/openapi.json` | 服务凭据和租户头 | 实际运行的 OpenAPI schema |

所有 `/v1/*` 必须同时携带 `Authorization: Bearer <service-key>` 和 `X-Tenant-Id`。Java 必须从可信 `TenantContextHolder` 取租户，不能把前端传值直接转发。服务密钥代表可信 Java 门面，并不为不可信客户端授予任意租户身份；前端必须访问 Java 权限接口。

解析 chunk 格式：`chunkType/sectionPath/locator/content/metadata`；`metadata` 包含 `page`（从 1 开始）、`block_type`、`bbox`（没有坐标时为 `null`）和 `source_name`。坐标必须是有限非负数且右下角不小于左上角。当 OCR 返回能力说明时，`metadata.ocr_capabilities` 保留 `textRecognition/tableStructure/layoutAnalysis` 布尔值；普通文字识别不冒充表格结构解析。Office XLSX/DOCX 继续使用 Java 原有解析规则。

错误格式为 `{"error":{"code":"稳定错误码"}}`。401 表示服务认证失败，400 表示租户头非法，413/415/422 表示输入/文档限制，429 表示并发已满，502 表示上游协议/隔离验证失败，503 表示依赖未配置/不可用，504 表示总预算耗尽。响应、日志不包含上游正文、查询、文档、密钥或目标地址。

## 安全与兼容限制

- 文件以 base64 内存传递，不接受远程文件 URL 或磁盘路径。检查真实文件格式、大小、图片像素、PDF 加密、页数、展开体积、总文本和切片数。
- PDF 检查和 LlamaIndex 切片均在一次性子进程运行；取消/超时杀死并回收进程。各子阶段的墙钟等待使用解析预算，取消旧的固定 25 秒截断；`/v1/parses` 的外层总 deadline 仍覆盖检查、OCR、切片，阶段预算不累加。子进程 CPU 软/硬限仍为 20/21 秒，Linux 虚拟内存仍限 768 MiB，Docker 限额不变。ASGI 不运行 PDF/切片 CPU 任务。
- 任何页缺失、限额超出或文档无文字都拒绝整份文档。含空白页或栅格图片的 PDF 会进入 OCR 路径；OCR 页数必须与 PDF 一致，每页必须有可识别文字。完全空白页也会明确拒绝，调用方需移除空白页后重试，不做静默缺页导入。
- 解析无服务端无限等待队列；只允许配置数量的并发。请求体、上游响应也有硬大小上限；不信任 `Content-Length`。Uvicorn 启动限制 16 个并发连接，防止解析排队前累积过多大请求体。
- 上游地址只来自配置，禁用环境代理、重定向及 URL 认证字段。没有请求端点可改配置、执行迁移或写向量。
- worker 超时、取消和非零退出仅记录白名单阶段 `inspect/split`、受控原因、耗时及退出码；不记录文件名、正文、异常原文或凭据。外层总 deadline 触发的取消明确标为 `cancelled`，不误报为子阶段独立超时。
- 总检索预算最多 3 秒，Java HTTP 建议 3500ms，保留原 `ai` Dubbo 5 秒预算。长文档解析使用独立预算，建议引擎 55 秒、Java 60 秒。OCR HTTP 单独使用解析预算作为读取等待上限，避免 NAS CPU 推理被共享 HTTP client 的 10 秒默认读取超时提前中断；连接最多 3 秒、连接池最多 1 秒、写入最多 10 秒，且均不超过解析总预算。读取超时不是整次请求预算，外层总 deadline 仍覆盖文件检查、OCR 和切片，慢响应持续发送也不能延长它。其他 JSON 请求继续继承各自 client 默认超时。
- `RAG_ENGINE_COLLECTION`、embedding 模型和维度必须与 Java 摄取完全相同。服务请求不创建/迁移 collection，不重嵌入历史文档；CLI 只在显式 `--apply` 时增加 sparse schema 或值。
- 启用混合检索前须完成：Qdrant schema 兼容验证、显式全量 backfill、Java 新写入/换版/删除同步、完整性读回和评估。不能扫描前 N 条数据冒充全库关键词检索。

## 混合检索与完整索引

`RAG_ENGINE_HYBRID_ENABLED=false` 保持旧 dense 路径。设为 `true` 后，每次查询先检查 collection 的 unnamed dense 维度和 Cosine 距离、`lexical-v1` sparse 的 `idf` modifier，并以精确 count 检查当前 tenant/knowledgeBase 的每个 ACTIVE 点同时具备真实 sparse 向量和 `sparse_model=lexical-v1`。缺 schema 返回 `SPARSE_SCHEMA_NOT_READY`；缺值或版本不匹配返回 `SPARSE_INDEX_INCOMPLETE`，不静默假称 hybrid。失败不会自动修改任何 collection。

准备完成后，LlamaIndex `ScopedQdrantRetriever` 分别查真实 dense 与 sparse 索引，`ReciprocalRankRetriever` 并发编排、取消失败的伴随请求并按 RRF `1/(60+rank)` 融合，rank 从 1 起。dense 使用 `similarityThreshold`；TF-IDF 与 RRF 分数不同尺度，不套用 dense 阈值。融合后按 `candidateCount` 限候选、按内容去重、执行千问重排。重排分数必须为有限数且在 `[0,1]` 内；完整响应验证通过后，按独立的 `RAG_ENGINE_RERANK_SCORE_THRESHOLD`（默认 `0.5`，可配置范围 `[0,1]`）过滤。门槛包含边界，全部低分时返回空列表且不因此标记降级。默认 `0.5` 来自本轮 28 份合成文档、60 题、按 tenant/KB 真过滤后的千问重排校准：奇数题校准、偶数题验证，两半在该门槛下均无误接收或误拒绝。这是合成语料的校准证据，不能代替实际业务数据泛化验证；换语料或模型仍需用带无答案反例的数据集校准。重排失败保留本次 RRF 排序，`mode=hybrid,degraded=true`，不对 RRF 分数套 dense 或重排阈值；此时不保证拒绝不相关候选。无可编码查询词时使用 `mode=dense,degraded=true`。任何一路隔离验证失败都拒绝请求。总链路仍受 3 秒预算约束。

`lexical-v1` 编码与 Java 共用如下协议（固定测试向量在 `tests/fixtures/lexical-v1.json`）：

1. Unicode NFKC，随后小写；ASCII 词匹配 `[a-z0-9]+`，连续汉字匹配 `[\u3400-\u4dbf\u4e00-\u9fff]+`。
2. ASCII 词加 `w:` 前缀；汉字单字加 `c:`；每段相邻双字加 `b:`。不跨标点生成双字。
3. 每个 token 的 UTF-8 SHA-256 前 4 byte 按无符号大端转 uint32 index；哈希碰撞合并计数。有限的 32 位哈希存在碰撞可能，不是完整词典。
4. weight 为 `1+ln(count)`，全部权重 L2 归一化，indices 升序。Qdrant 使用 `idf` modifier；这是中英文 TF-IDF 稀疏检索，**不是 BM25**，不下载编码模型。
5. Java 写入 `vector={"": dense, "lexical-v1": sparse}`，payload 写 `sparse_model=lexical-v1`，二者在同一 upsert 中完成后才能激活。关闭双写回滚可恢复纯 dense；混合查询必须一并关闭。纯 emoji 等无可编码词的文档不可标为 sparse 完成。

迁移先暂停该知识库的摄取、删除和状态变更；以下命令只通过环境读取目标地址及秘密，不接受请求传入任意 URL：

```sh
# 现有 collection 必须存在；prepare 不新建 collection。首次空库先由 Java 初始化。
uv run --frozen python -m rag_engine.sparse_index prepare
uv run --frozen python -m rag_engine.sparse_index prepare --apply
# 默认 dry run 仍完整滚动全部 ACTIVE 点；不写任何向量，不声称 complete。
uv run --frozen python -m rag_engine.sparse_index backfill --tenant <TENANT> --knowledge-base <KB>
uv run --frozen python -m rag_engine.sparse_index backfill --tenant <TENANT> --knowledge-base <KB> --apply --writers-paused
uv run --frozen python -m rag_engine.sparse_index status --tenant <TENANT> --knowledge-base <KB>
```

Qdrant 1.18+ 的 `PUT /collections/{collection}/vectors/lexical-v1` 增加 sparse schema，保留旧 unnamed dense。回填以 64 点逐页 scroll 直到 `next_page_offset=null`，使用 UpdateVectors 只更新 sparse，再写版本标记；不会更改 dense、内容或文档状态。结束以全量 count 比较 scanned/total/missing，未完整就失败。中断后可重跑；没有“前 N 条已处理即完成”的上限。`--writers-paused` 是运维确认，不是跨 Java 的分布式锁；必须实际暂停写入再执行，以免覆盖同 ID 的并发更新。只有所有目标 tenant/KB 完成且 Java 双写已就绪才开启查询。

## 测试与证据边界

```sh
uv sync --frozen
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
uv run --frozen pytest -q
```

默认测试使用现场合成的 PDF、图片、租户和随机凭据；PaddleOCR/千问/Qdrant 使用受控 HTTP 替身，不访问付费模型或真实知识库。`tests/test_qdrant_live.py` 可通过 `RAG_TEST_QDRANT_URL` 显式启用；只创建唯一 `rag_engine_acceptance_*` 临时 collection，并在 finally 删除，千问仍用替身。测试覆盖真实文件解析、页数/格式/加密边界、进程回收、OCR 协议、请求鉴权、错误脱敏、租户隔离、模型维度一致性和超时/降级。worker 预算回归另让真实 split 子进程等待 26 秒后执行原 worker，验证它能在总 55 秒内成功；短总 deadline 则验证真实进程被 SIGKILL 并回收，日志不泄漏请求内容。OCR 超时回归另启动真实本地 HTTP 服务：延迟响应超过共享 client 短读取超时仍能解析；普通 JSON 调用保留原超时；超出解析总预算则返回 `PARSER_TIMEOUT`。

开发时另跑过 localhost 真 HTTP：启动实际 Uvicorn，未认证解析得到 401，合成 PDF 解析得到 200，文本及页码读回匹配。另在 Qdrant 1.18.1 临时库验证了旧 unnamed dense 添加 sparse、140 条记录分 3 页完整回填、dense 值不变、跨租户隔离、sparse-only 召回、RRF 排序及旧单写使 readiness 失效。真实 OCR 推理质量、模型端点与跨主机 TLS 仍需部署环境验收；不能以这些测试声称检索质量提升或 OCR 已部署。

依赖来自 PyPI 官方源，直接依赖使用精确版本、传递依赖记录哈希。FastAPI/LlamaIndex/Pydantic 为 MIT；HTTPX 为 BSD-3-Clause；pypdf 为 BSD-3-Clause；Pillow 使用 MIT-CMU；Uvicorn 为 BSD-3-Clause。本轮尝试 OSV 锁文件漏洞查询时服务 TLS 连接失败，未把“未查询到”表述为“无漏洞”。

## 官方依据

- [LlamaIndex 文档与节点](https://developers.llamaindex.ai/python/framework/module_guides/loading/documents_and_nodes/)
- [PaddleOCR-VL 服务调用协议](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
- [Qdrant 向量 schema](https://qdrant.tech/documentation/manage-data/vectors/)
- [pypdf 文本提取与资源限制说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)

## LlamaIndex 与 Neo4j 派生图谱

`RAG_ENGINE_GRAPH_ENABLED=false` 为默认状态，关闭时图接口明确返回 `GRAPH_NOT_CONFIGURED`，原解析/检索保持可用。Java 图开关另行控制路由，不能仅凭依赖已安装称已启用。新增直接依赖 `llama-index-graph-stores-neo4j==0.8.0` 与现有 core `0.14.25` 配套；要求 Neo4j 服务端至少 5.23（集成使用 `CALL (variables) {}`），部署建议已核验的 5.26 LTS 与对应 APOC。模型仍走配置的千问 compatible `/chat/completions`，使用 LlamaIndex `CustomLLM` 和原 bounded HTTP，无默认 OpenAI 凭据、embedding 或模型下载。

内部接口均要求 Service Bearer 与可信 `X-Tenant-Id`，JSON 不接受 tenant。`POST /v1/graphs/builds` 接收 `knowledgeBase/activeSources/reconcile`；`POST /v1/graphs/queries` 另接收 `searchText/nodeType/viewMode/limit`；`POST /v1/graphs/deactivations` 接收 `documentId/documentRevision`，返回删除屏障票据；`GET /v1/graphs/builds/{buildId}?knowledgeBase=...` 按 tenant+KB 查询任务状态。请求最多 8 MiB、10000 个来源身份、正文总量12000000字符，超限整次拒绝；每个身份包含 `pointId/documentId/documentCode/documentVersion/documentRevision/chunkId/contentSha256`。来源按64点批量从现有 Qdrant 回读，逐项检查身份、ACTIVE、正文与 Java 语义去重 SHA256，不接受客户端正文、文件路径或远程模型地址。Qdrant dense/sparse schema、向量、模型和维度不因图谱构建改变。

构建返回持久任务 `QUEUED`，不代表完成。单并发工作进程、有界持久队列、独立总 deadline，超时/取消杀死并回收进程；容器限制RSS，不使用易误杀NumPy映射的RLIMIT_AS。队列满返回429。全局Neo4j租约限制多个引擎同时调度；部署只启动一个Uvicorn worker。重启将中断RUNNING明确标为FAILED，未超时QUEUED可继续。单次任务使用独立staging generation，真实 `SimpleLLMPathExtractor` 后置scope变换后交给 `PropertyGraphIndex(embed_kg_nodes=False)`。每个实体出现的ID含tenant/KB/generation/pointId，实体名字不能跨来源MERGE；谓词使用安全hash类型身份，防同端点不同谓词互相覆盖。模型附加属性和控制字段全部丢弃，原文不会进入运行日志。

只有全量写入、计数/来源读回、ACTIVE Qdrant复核通过后才在事务中发布generation。发布核对当前请求、文档revision、提交序号、删除屏障epoch、任务deadline和worker租约。完整快照只被较新的完整快照替代，只退役自身提交前已请求且不在快照中的旧文档，不废弃更晚入队的新文档。删除先退役并记录blockedRevision、递增屏障；同revision普通sync即便暂时仍读到ACTIVE也不能复活。更高revision重传可重新构建；只有向量删除失败的Java补偿路径才携带 `restoreFence:{documentId,barrierEpoch,documentRevision}`，严格匹配当前票据后恢复，旧票据不能跨越新delete。失败staging不可查询。后台按tenant/KB分批清理无活动文档及无QUEUED/RUNNING引用的旧generation，每轮最多4×512节点，重启后继续；终态任务保留脱敏状态计数，移除来源快照JSON。Chunk明确设置LlamaIndex SOURCE关系，避免官方serializer把document_id覆盖为None；来源读回同时核对documentId/version/revision。

查询执行固定有界Cypher，读取当前文档激活generation，并检查所有端点、关系与来源的tenant/KB/point/版本身份。返回后再从Qdrant复核实际使用来源，Java还应再次检查当前活动快照。当前展示类型仅 `ENTITY/RELATED_TO`，同名实体在当前tenant+KB内聚合；旧结构类型过滤在新后端返回空，前端应隐藏不适用选项。所有节点和边有1至8条 `properties.sources`，并给出实际返回范围内的 `sourceCount/sourceTruncated`；来源保留identity、locator、按实体实际位置截取的证据及evidenceTruncated/evidenceNormalized标记，以及page/bbox/block_type/ocr_capabilities白名单与有限重复位置。properties.extractionMethod/model/models明确模型抽取身份；实体名字必须归一后实际出现于原文，不能把模型推测称为原文已证实关系。节点最多500、边最多min(配置,limit×4,2000)、响应最多8MiB，结果截断时不返回悬空端点。

`RAG_ENGINE_GRAPH_RETRIEVAL_ENABLED=false` 独立关闭图检索增强。显式开启后，以已通过隔离校验的Qdrant候选为种子，仅沿当前激活generation和授权scope扩展同名实体关联的原文切片；图查询与Qdrant二次回读合计最多0.4秒，包含在原三秒总检索预算。扩展原文继续参与rerank及原相关性门槛，不直接把三元组当作上下文。响应graphAugmented表示实际加入新候选，graphDegraded表示扩展不可用/超时降级；图扩展候选的隔离或来源身份校验失败时丢弃不可信扩展并标记 graphDegraded，已验证的原向量路径保留；原向量召回身份校验失败仍使整个检索请求失败。未命中/无新候选不宣称增强成功。真实图参与检索仍需端点与业务语料验收。

图模型使用支持严格JSON Schema的千问快照，默认 `qwen3.7-plus-2026-05-26`，不自动切换模型或退回自由格式。请求的schema由Pydantic生成，只允许 `triples` 数组及每项必填的 `subjectQuote/predicate/objectQuote` 字符串，长度为1至160，数组上限与配置保持一致，禁止额外属性。两个Quote字段明确要求复制单个连续原文片段，不能写成组装后的概念别名。适配层完整校验对象形状后，将每项的原值一一转成官方 `SimpleLLMPathExtractor` 所需三元组；不做类型转换、筛项或裁长。仍保留2048输出token与HTTP字节/超时预算，非stop或截断输出明确失败。结构约束不能代替原文匹配或关系语义验证。

抽取优先处理正文概念、条件、动作和标准之间的内容关系，不把文件标题、行号或通用列名当实体来复述字段表。名称按既有NFKC、casefold及空白归一规则逐项匹配原文；错误颜色/程度、改写、缺失限定语或非连续词拼接仍拒绝。单切片最多发出三个模型请求，包含局部修复请求。每个完整候选独立经过官方 `SimpleLLMPathExtractor` 和全部原文/作用域守卫；不剔除非法三元组，不以先前非空结果后的空数组掩盖失败。

首轮仅有名称错误且能可靠建立局部计划时，保留冻结完整三元组、只修正非法subject/object槽的快路径。动态严格schema绑定当前来源、完整计划、全部槽和候选；局部副本不共享可变修复状态。局部计划只适合可证实的同支持子句标点差异，不能代表通用实体纠错。若无法建立计划或局部修复失败且仍有请求预算，使用完整当前原文及有界错误反馈重新抽取完整候选，不把旧三元组冻结为真、不自动选取相似名称。新候选仍受相同结构、数量、长度与原文守卫约束；请求预算耗尽则任务明确失败。模型临时传输错误与局部修复、完整重抽共享这三个请求的总预算；任务总deadline不变。

动作和状态事实也必须形成完整三元组：当原文明示主体应执行某动作而没有单独受事对象时，把完整动作或状态短语复制到 `objectQuote`，谓词只表达简短关系，不把完整动作放进谓词后留下空对象。保留原文否定、程度、条件和模态，不自动补造受事对象，不把程序拆词当成模型提取结果。上游结构化输出仍可能返回不符合字段约束的内容，应用层非空及原文校验始终执行。

图谱模型调用在自身边界区分请求超时、临时网络中断、限流、服务暂不可用、鉴权失败、请求拒绝与非法响应。仅超时、可重试网络故障、429 和 500/502/503/504 可在剩余请求预算内进行短且可取消的退避后重发；401/403、其他拒绝、TLS/证书、URL/协议、超大或非JSON外层响应立即失败。传输重试重发同一个完整抽取或局部修复请求，不能因未收到响应而更换修复计划，也不把网络原因写成实体纠正反馈。取消和父任务deadline不被转成模型超时后重试。

错误诊断只在固定的抽取/模型错误码与各自reason白名单下记录64位小写hex的`source_hash`，worker与parent分别复核类型与格式，不记录正文、模型回包或可读point ID。该hash由tenant、KB、buildId和pointId计算，包含任务generation，不是contentSha256；排查时需要用同一失败buildId与已有来源快照重算。反馈、候选和支持原文不会进入日志或公开GraphBuild响应；失败仍不发布staging图，也不撤销原向量上传。

Java的Neo4j来源快照只读取tenant、KB、ACTIVE状态和七项来源身份所需的9个payload字段，不重复下载整库正文；count/scroll/count、重复点、版本/hash和查询前后来源集合比较保持。旧MySQL结构投影继续使用完整payload；Python构建与查询仍独立回读原文并验证正文指纹。

`RAG_ENGINE_GRAPH_EXTRACTION_WORKERS` 控制同一图任务内的模型请求并发，缺省1、范围1至4；HTTP连接数随之限制，全局仍只有一个图构建worker。增加并发前须实测模型限流与容器RSS，不能同时放宽容器资源或原文/租户校验。大文档需要按实际切片数和模型延迟选择构建总预算（允许1至1800秒，含排队），不能从小样本成功推断整份文件通过。当前失败重试仍会重新处理整个文件，不具备逐切片续跑检查点。

图配置的具体用途、单位和缺省值见 `.env.example`。Neo4j/千问真实调用、跨租户/KB、删除恢复、换版、网关与浏览器均需独立验收；本地替身测试不代表服务已部署。

- [Qdrant 1.18 新增向量 schema](https://qdrant.tech/documentation/manage-data/collections/#update-vector-schema)
- [Qdrant has_vector 过滤](https://qdrant.tech/documentation/search/filtering/#has-vector)

## NAS 双容器部署

使用 `compose.nas.yaml` 和 Git 外 `.env`，设置 `RAG_ENGINE_IMAGE` / `RAG_OCR_IMAGE` 为通过 CI 的 sha 标签或 digest、`RAG_ENGINE_BIND_IP` 为 NAS 的私有接口、两份不少于32字符的服务密钥。其余 `RAG_ENGINE_*` 按 `.env.example` 配置，尤其 collection/model/dimension 必须与 Java 完全一致。

NAS 宿主端口由 `RAG_ENGINE_HOST_PORT` 设置，默认 `18091`，避开已有 Seata 的宿主 `8091`；引擎容器内部仍使用 `8091`。个人 Nacos 的 `rag.engine.base-url` 应使用 `http://<NAS私有地址>:18091`；覆盖宿主端口时同步修改该 URL。引擎访问 OCR 继续使用容器内网地址 `http://rag-ocr:8092`，不受宿主端口调整影响。本机开发的 `compose.yaml` 仍映射 loopback `8091:8091`。

引擎上限1.5GiB/1CPU，OCR上限2GiB/2CPU；同时达到上限时合计3.5GiB。部署前复查 NAS 当前余量，至少另留2GiB给原有中间件及系统。OCR只在内部网络，权重已内置，不映射宿主端口、不访问公网；Java通过私有组网访问引擎。未配置外部TLS时依赖现有加密组网，不能做公网端口映射。

顺序：验证两个镜像健康及真实PDF/图片 → 创建新collection或暂停writer后显式迁移旧库 → 开启Java sparse双写 → 核验索引完整 → 用真实合成题验收引擎 → 最后启用个人Nacos解析/检索开关。只有这一顺序的读回证据齐全才能称部署完成；CI通过不能代替NAS实际安装。保留旧Java检索可通过关闭引擎开关回退。

## Neo4j 事务风险集成测试

`tests/test_graph_neo4j_live.py` 默认跳过，仅显式提供四个 `RAG_ENGINE_NEO4J_LIVE_{URL,USERNAME,PASSWORD,DATABASE}` 后连接。运行前须停止图 worker 并确认没有待处理任务；测试拒绝抢占有效租约，不认领既有任务，清理仅限自建随机 tenant/KB。使用 `uv run --frozen pytest -q tests/test_graph_neo4j_live.py` 验证真实提交顺序、全库/增量交错、删除票据、重传和租约；不把 repository 层控制测试当作模型或 Qdrant 真实性验收。

图谱批量存储使用 `RAG_ENGINE_GRAPH_STORAGE_TIMEOUT_SECONDS`（默认30秒、范围1–60）限制构建子进程的单个 Neo4j 事务，避免大批量写库共用交互查询的三秒预算。该参数不改变查询、提交或总构建deadline，也不改变1CPU／1.5GiB容器上限。内部失败诊断只输出固定阶段、异常类别及受限代码栈，不输出正文、异常原文或凭据；清理错误不覆盖构建主异常。
