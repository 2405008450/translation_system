from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.document_exporter import export_translated_docx
from app.services.document_workspace import parse_docx_workspace


def test_docx_export_keeps_sentence_mapping_inside_one_paragraph():
    raw_docx = _build_minimal_docx("\u7b2c\u4e00\u53e5\u3002\u7b2c\u4e8c\u53e5\uff1f")
    workspace = parse_docx_workspace(raw_docx)

    assert [segment["source_text"] for segment in workspace["segments"]] == [
        "\u7b2c\u4e00\u53e5\u3002",
        "\u7b2c\u4e8c\u53e5\uff1f",
    ]

    export_segments = []
    translated_texts = ["\u5df2\u8bd1\u7b2c\u4e00\u53e5\u3002", "\u5df2\u8bd1\u7b2c\u4e8c\u53e5\uff1f"]
    for segment, target_text in zip(workspace["segments"], translated_texts, strict=True):
        updated = dict(segment)
        updated["target_text"] = target_text
        export_segments.append(updated)

    exported_docx = export_translated_docx(raw_docx, export_segments)
    exported_workspace = parse_docx_workspace(exported_docx)

    assert [segment["source_text"] for segment in exported_workspace["segments"]] == translated_texts


def _build_minimal_docx(paragraph_text: str) -> bytes:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>{paragraph_text}</w:t></w:r>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", document_rels_xml)
    return output.getvalue()
