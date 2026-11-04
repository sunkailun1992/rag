# RAG 引擎演进：需求、验收与开发手册

## 需求与兼容边界

Java `rag` 继续拥有认证、租户、文档版本、幂等、Qdrant 写入与读回校验、图谱和现有 HTTP/Dubbo 契约。`engine/` 是同仓库独立 Python 服务，负责 PDF/图片解析与可选检索。既有 XLSX/DOCX 结构化解析保持原样。

首批落地评估基准、PDF/OCR 适配、带来源信息的切片和可回退的 dense / TF-IDF sparse + RRF 混合检索引擎。真实 OCR 质量、混合检索提分和线上 5 秒预算必须分别验收，不以本机 stub 测试代替。查询改写、繁简归一和异步摄取仍按效果按需推进。

## 服务契约

- Python `/v1/*` 只接受 Java 门面调用，要求服务 Bearer 密钥和 `X-Tenant-Id`。密钥只来自环境/Nacos，租户只来自 Java 当前可信上下文；Python 不能公开给浏览器。
- `POST /v1/parses` 接收 `sourceName/title/contentBase64/maxChunkCharacters`，返回 `title/sourceName/sourceType/chunks`。每个切片含 `chunkType/sectionPath/locator/content/metadata`；metadata 保留页码、区块类型、坐标和来源名。请求不接受 URL 或本机路径。
- `POST /v1/retrievals` 接收 `query/knowledgeBase/topK/candidateCount/similarityThreshold/maxContextCharacters`，返回 `hits/mode/degraded`；每个 hit 含 `id/score/payload`。必须过滤当前租户、知识库与 ACTIVE 状态，Java 再次验证返回范围。
- 解析与检索开关独立，默认关闭。关闭检索开关即恢复现有 Java 链路；开启后的失败返回原有结构化不可用结果，不在超时后继续叠加一次长耗时回退查询。
- Python 整次检索有总超时，Java HTTP 检索上限 3500 毫秒，为上游 Dubbo 5000 毫秒留余量。超时、鉴权错误与无命中分别记录，日志不记录问题、文件正文或凭据。

## 验收标准

- [x] 既有 XLSX/DOCX、文档版本、幂等与图谱测试通过。
- [x] 文字 PDF 可在无 OCR、无付费模型的本机环境解析；扫描件没有 OCR 时明确失败，不返回缺页的成功结果。
- [x] 图片/PDF 检查真实类型、文件大小、页数、切片上限与处理超时；禁止路径穿越和任意 URL 获取。
- [x] OCR 桩服务验证官方响应转换，真实模型结果另行验收。
- [x] Java→Python 请求验证 Bearer 与租户；跨租户、跨知识库、非活动数据与损坏响应失败关闭。
- [x] 新引擎关闭时无额外 HTTP 调用；开启时不重复执行旧检索。
- [x] 50–100 题合成评估集可重复执行，Recall/MRR/nDCG/延迟可计算；合成集不代表真实业务效果。
- [x] 新依赖锁定，Python 虚拟环境、缓存、运行结果、真实密钥不进 Git。

## 开发入口与风险

Java 配置为 `rag.engine`，客户端负责服务身份和返回值校验，解析器只分流新增格式。引擎保留来源 metadata，文档服务以独立 `source_metadata` 保存，禁止 metadata 覆盖租户或状态。

Qdrant 保留 unnamed dense vector；Java `hybrid-write-enabled` 打开时原子双写 `lexical-v1` sparse（中英文无模型 TF 编码，Qdrant IDF），共享同一租户、文档和状态 payload。Python 使用 LlamaIndex 双路召回与 RRF 后重排；查询只使用倒排索引，不扫描截断语料。启用前运行显式 schema/完整回填 CLI；旧单写或缺失索引会使完整性检查拒绝 hybrid。新增库可以直接创建双向量 schema，旧库必须实际暂停写入再迁移。

## 本轮新增交付与部署判据

- `GET /api/rag/documents/{documentId}/chunks`：原管理权限、可信租户、有界游标分页；返回来源及页块 metadata，不返回向量和原始治理 payload。
- admin-web 提供真实切片 Drawer 和浏览器本地脱敏评估报告读取；报告不上传服务器。
- CPU OCR 位于 `engine/ocr/`，真实 PaddleOCR 轻量模型只提供文字及坐标，能力标记明确排除完整表格/版面分析；本机真实中文 PNG/扫描 PDF 验证通过，约 1 GiB 峰值。NAS 按 2 GiB/2 CPU 硬限额部署，x86 容器结果单独验收。
- rag-engine 限制 1.5 GiB/1 CPU。沿用 Qdrant 与千问；不为评估增加常驻 Langfuse 数据库等重服务。离线/真实评估输出可追溯指标，未生成答案时不声称已测 Ragas faithfulness。
- Java/Python sparse golden 与独立风险反例覆盖编码、分页、schema、凭据脱敏。Git/CI、NAS镜像、Nacos启用和实际质量评估分别记录，不能互相替代。

启用前先跑 `./gradlew test bootJar`、`engine/` 测试和 `evaluation/` 回归，再使用独立测试知识库做真实 PDF/OCR 与检索试验。上线只改个人 github-dev 配置并读回；美联、Yudao 不在范围。Python 配置由部署环境注入，Java 配置发布遵循 Nacos CAS/备份要求。

回滚先关闭 `rag.engine.retrieval-enabled`，保留旧 Java 检索；解析开关可独立关闭，已有向量和文档继续可读。无需回滚数据库 DDL或删除旧向量。检索改造不得复制公司业务数据。
