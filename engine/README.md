# RAG Python 引擎

`rag/engine` 与 Java `rag` 同仓管理。Java 继续拥有认证、租户、知识库、文档版本、幂等、Qdrant 写入、读回校验和图谱；本引擎只负责 PDF/图片解析与可选的检索计算。

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
- PDF 检查和 LlamaIndex 切片均在一次性子进程运行；取消/超时杀死进程，子进程有 CPU 限制，Linux 另有 768 MiB 虚拟内存限制。Docker 内存限制覆盖整个服务。ASGI 不运行 PDF/切片 CPU 任务。
- 任何页缺失、限额超出或文档无文字都拒绝整份文档。含空白页或栅格图片的 PDF 会进入 OCR 路径；OCR 页数必须与 PDF 一致，每页必须有可识别文字。完全空白页也会明确拒绝，调用方需移除空白页后重试，不做静默缺页导入。
- 解析无服务端无限等待队列；只允许配置数量的并发。请求体、上游响应也有硬大小上限；不信任 `Content-Length`。Uvicorn 启动限制 16 个并发连接，防止解析排队前累积过多大请求体。
- 上游地址只来自配置，禁用环境代理、重定向及 URL 认证字段。没有请求端点可改配置、执行迁移或写向量。
- 总检索预算最多 3 秒，Java HTTP 建议 3500ms，保留原 `ai` Dubbo 5 秒预算。长文档解析使用独立预算，建议引擎 55 秒、Java 60 秒。
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

默认测试使用现场合成的 PDF、图片、租户和随机凭据；PaddleOCR/千问/Qdrant 使用受控 HTTP 替身，不访问付费模型或真实知识库。`tests/test_qdrant_live.py` 可通过 `RAG_TEST_QDRANT_URL` 显式启用；只创建唯一 `rag_engine_acceptance_*` 临时 collection，并在 finally 删除，千问仍用替身。测试覆盖真实文件解析、页数/格式/加密边界、进程回收、OCR 协议、请求鉴权、错误脱敏、租户隔离、模型维度一致性和超时/降级。

开发时另跑过 localhost 真 HTTP：启动实际 Uvicorn，未认证解析得到 401，合成 PDF 解析得到 200，文本及页码读回匹配。另在 Qdrant 1.18.1 临时库验证了旧 unnamed dense 添加 sparse、140 条记录分 3 页完整回填、dense 值不变、跨租户隔离、sparse-only 召回、RRF 排序及旧单写使 readiness 失效。真实 OCR 推理质量、模型端点与跨主机 TLS 仍需部署环境验收；不能以这些测试声称检索质量提升或 OCR 已部署。

依赖来自 PyPI 官方源，直接依赖使用精确版本、传递依赖记录哈希。FastAPI/LlamaIndex/Pydantic 为 MIT；HTTPX 为 BSD-3-Clause；pypdf 为 BSD-3-Clause；Pillow 使用 MIT-CMU；Uvicorn 为 BSD-3-Clause。本轮尝试 OSV 锁文件漏洞查询时服务 TLS 连接失败，未把“未查询到”表述为“无漏洞”。

## 官方依据

- [LlamaIndex 文档与节点](https://developers.llamaindex.ai/python/framework/module_guides/loading/documents_and_nodes/)
- [PaddleOCR-VL 服务调用协议](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
- [Qdrant 向量 schema](https://qdrant.tech/documentation/manage-data/vectors/)
- [pypdf 文本提取与资源限制说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)

- [Qdrant 1.18 新增向量 schema](https://qdrant.tech/documentation/manage-data/collections/#update-vector-schema)
- [Qdrant has_vector 过滤](https://qdrant.tech/documentation/search/filtering/#has-vector)
