"""将合成语料导出为逐文档 DOCX；不联网、不自动上传，不包含真实业务资料。"""
import argparse
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from evaluation.runner import read_jsonl, indexed, EvaluationError


def export(corpus: list[dict], target: Path) -> int:
    indexed(corpus, "corpus")
    for document in corpus:
        name = document["sourceName"]
        if Path(name).name != name or not name.endswith(".docx") or "/" in name or "\\" in name:
            raise EvaluationError("invalid source filename")
        tenant, base = document["tenantId"], document["knowledgeBase"]
        if any(not item or any(not (char.isalnum() or char in "-_") for char in item)
               for item in (tenant, base)):
            raise EvaluationError("invalid corpus scope")
        directory = target / tenant / base
        directory.mkdir(parents=True, exist_ok=True)
        paragraphs = [document["title"], *document["text"].split("\n")]
        body = "".join('<w:p><w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'
                       for text in paragraphs)
        with ZipFile(directory / name, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
            archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
            archive.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?>'
                             '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body>' + body + '</w:body></w:document>')
    return len(corpus)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("evaluation/corpus.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("build/evaluation-corpus"))
    args = parser.parse_args()
    print(f"exported {export(read_jsonl(args.corpus), args.output)} synthetic DOCX documents")


if __name__ == "__main__":
    main()
