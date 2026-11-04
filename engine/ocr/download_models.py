"""构建时下载官方 CPU 小模型；运行时只读已固定到镜像内的文件。"""

import hashlib
import json
import os
import shutil
from pathlib import Path

from paddleocr import PaddleOCR

from ocr_worker import MODEL_NAMES

PaddleOCR(
    text_detection_model_name=MODEL_NAMES[0],
    text_recognition_model_name=MODEL_NAMES[1],
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    device="cpu",
    enable_mkldnn=False,
    cpu_threads=2,
)
target = Path(os.environ.get("RAG_OCR_MODEL_ROOT", "/opt/models"))
target.mkdir(parents=True, exist_ok=True)
for name in MODEL_NAMES:
    cache = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", str(Path.home() / ".paddlex")))
    source = cache / "official_models" / name
    if not (source / "inference.json").is_file():
        raise RuntimeError("official model download did not complete")
    shutil.copytree(source, target / name, dirs_exist_ok=True)
manifest = {
    str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
    for name in MODEL_NAMES
    for p in sorted((target / name).rglob("*"))
    if p.is_file()
}
expected = json.loads(Path("model-sha256.json").read_text())
if manifest != expected:
    raise RuntimeError("official model contents do not match the reviewed model lock")
(target / "sha256.json").write_text(json.dumps(manifest, indent=2))
