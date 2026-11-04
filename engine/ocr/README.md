# 极空间基础 CPU OCR

本服务使用 PaddleOCR 3.7.0、PaddlePaddle CPU 3.3.1 和官方 `PP-OCRv5_mobile_det` / `PP-OCRv5_mobile_rec`。模型在镜像构建时下载并保存 SHA-256 清单，容器运行时只使用镜像内模型；不下载任何 VL、公式、图表或大型版面模型。

它提供扫描 PDF、PNG、JPEG 的**基础文字识别和行坐标**。响应明确包含 `textRecognition=true`、`tableStructure=false`、`layoutAnalysis=false`，所有块标签为 `ocr-text`。它不恢复合并单元格、复杂阅读顺序、公式或表格语义；这些需求应把 `rag-engine` 的 OCR 地址改为经过实测的完整 PaddleOCR `/layout-parsing` 服务。不能把基础识别结果称为完整 PP-Structure / PaddleOCR-VL 验收。

## 资源与部署

采用单请求单子进程，空闲不常驻模型，避免 OCR 模型持续占用 NAS 内存。每次识别重新加载两个 mobile 模型；父服务同时只接受 1 次解析，超时或取消时杀死实际推理子进程。CPU 时间最多 45 秒，墙钟 50 秒。建议 Docker 限制 `cpus: 2.0`、`mem_limit: 2g`、`pids_limit: 64`；用 `read_only: true`、`tmpfs: /tmp:size=128m`、`cap_drop: [ALL]`、`no-new-privileges:true`，只接入引擎内网，不公开到互联网。

输入上限为 20 MiB、25 页、每页 800 万像素。PDF 按 120 DPI 逐页渲染，不一次性展开全部页。识别文本上限为 100 万字符。超过限制、任一页无可识别文字或模型失败均拒绝整份文档，不静默丢页。

```sh
docker build --platform linux/amd64 -t kellen-rag-ocr-cpu:0.1.0 engine/ocr
```

服务必须注入 `RAG_OCR_API_KEY`（至少 32 字符）；引擎对应 `RAG_ENGINE_OCR_API_KEY` 使用相同值，`RAG_ENGINE_OCR_URL` 指向该私有服务的根地址。密钥不写入镜像、Compose、Git 或日志。`GET /health` 只证明父进程可用；真实模型识别必须以 `POST /layout-parsing` 验收。

Docker 目标为 NAS x86_64 / `linux/amd64`，使用已锁定的 `uv.lock` 安装官方 CPU Paddle wheel。首次构建需要访问 PyPI、官方模型仓库及 Python/uv 镜像仓库。构建后逐项比对已审核的 `model-sha256.json`，同名模型变化会拒绝构建；`/opt/models/sha256.json` 可随镜像回读模型文件完整性。生产使用已验证镜像 digest。

## 验证

```sh
cd engine/ocr
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
# 构建/注入模型目录后，用真实模型识别仓库内合成中文样本：
RAG_OCR_MODEL_ROOT=.models uv run python scripts/acceptance.py
# 或在已启动容器内执行（密钥沿用容器环境，不打印到命令）：
docker exec rag-ocr-cpu python scripts/acceptance.py --url http://127.0.0.1:8092 --output /tmp/acceptance.json
```

pytest 验证鉴权、输入限制、能力标记和协议转换，不证明模型质量。`scripts/acceptance.py` 使用无个人信息的合成中文段落和表格图片，真正执行 Paddle 推理并校验中文、数字、坐标与 PNG/PDF 一致性。本机启动时采样父/子进程合计峰值 RSS，远端/容器测试另核 Docker 的内存和 OOM 状态。报告输出默认 `.acceptance/report.json`，被 Git 忽略。

2026-11-05 本机 Mac ARM 实测：PNG 2.301 秒、扫描 PDF 2.262 秒，均返回 11 个文字行块；合计峰值 RSS 1002.55 MiB。仅为两份合成图的点时结果，不能证明完整表格结构或所有文档质量。Mac ARM 通过也不能代替 NAS x86 容器内存与 CPU 验收；NAS 剩余内存不足约 3 GiB 或压测超限时，应停止本服务并保留外部 OCR 配置入口。

## 官方来源与依赖

- [PaddleOCR CPU / OCR pipeline](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html)：CPU 推理、mobile 模型和禁用预处理。
- [PaddleOCR pipeline 源码](https://github.com/PaddlePaddle/PaddleOCR/blob/main/paddleocr/_pipelines/ocr.py)：模型参数、输入和结果结构。
- [PP-StructureV3 官方基准](https://paddlepaddle.github.io/PaddleX/latest/pipeline_usage/tutorials/ocr_pipelines/PP-StructureV3.html)：完整流水线主机峰值内存可达约 5.8 GB；不据此声称它能在本服务 2 GB 限额下运行。
- PaddleOCR / PaddlePaddle / PaddleX 为 Apache-2.0；FastAPI 为 MIT，Uvicorn 为 BSD-3-Clause，pypdfium2 使用 Apache-2.0/BSD-3-Clause 及 PDFium 第三方许可证，Pillow 使用 MIT-CMU。锁文件固定传递依赖，镜像漏洞扫描和 NAS 实测仍需在实际构建/部署环境完成。
