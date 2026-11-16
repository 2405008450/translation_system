from io import BytesIO
from zipfile import ZipFile

import pytest
from lxml.etree import XMLSyntaxError
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from PIL import Image as PILImage

from app.services.adapters.xlsx_adapter import XlsxAdapter
from app.services.adapters.xlsx_compat import load_xlsx_workbook
from app.services.adapters.xlsx_exporter import XlsxExporter


def workbook_bytes(*, invalid_guid=False, broken_xml=False):
    workbook = Workbook()
    workbook.active.append(["原文", "译文"])
    workbook.active.append(["备件目录", "Parts catalog"])
    picture = BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(picture, format="PNG")
    picture.seek(0)
    workbook.active.add_image(Image(picture), "D1")
    output = BytesIO()
    workbook.save(output)
    result = BytesIO()
    with ZipFile(output) as source, ZipFile(result, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "xl/drawings/drawing1.xml":
                if invalid_guid:
                    content = content.replace(
                        b"<wsDr ",
                        b'<wsDr xmlns:id="{223f3ce9-fd47-4349-9dfb-a58901d7e918}" ',
                        1,
                    )
                if broken_xml:
                    content = content.replace(b"</wsDr>", b"</broken>")
            target.writestr(info, content)
    return result.getvalue()


def test_guid_namespace_preserves_text_and_images():
    original = workbook_bytes(invalid_guid=True)
    normal = XlsxAdapter().parse(workbook_bytes())
    parsed = XlsxAdapter().parse(original)
    assert [n.text_content for n in parsed.ast.nodes] == [n.text_content for n in normal.ast.nodes]
    workbook = load_xlsx_workbook(original)
    assert workbook.active["A2"].value == "备件目录"
    assert len(workbook.active._images) == 1
    workbook.close()
    assert XlsxAdapter().parse_for_tm(original).entries[0].source_text == "备件目录"


def test_export_preserves_original_drawing_and_image_parts():
    original = workbook_bytes(invalid_guid=True)
    parsed = XlsxAdapter().parse(original)
    segment = next(s for s in parsed.segments if s.display_text == "备件目录")
    exported = XlsxExporter().export(original, [
        {"sentence_id": segment.segment_id, "target_text": "Spare parts catalog"},
    ])
    workbook = load_xlsx_workbook(exported)
    assert workbook.active["A2"].value == "Spare parts catalog"
    assert len(workbook.active._images) == 1
    workbook.close()
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(exported)) as after:
        for name in before.namelist():
            if name.startswith(("xl/drawings/", "xl/media/")):
                assert before.read(name) == after.read(name)


def test_drawing_text_can_be_translated_with_guid_namespace():
    source = workbook_bytes(invalid_guid=True)
    output = BytesIO()
    with ZipFile(BytesIO(source)) as archive, ZipFile(output, "w") as target:
        for info in archive.infolist():
            content = archive.read(info)
            if info.filename == "xl/drawings/drawing1.xml":
                content = content.replace(
                    b"</wsDr>",
                    b'<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                    b'id:marker="test"><a:r><a:t>Drawing label</a:t></a:r></a:p></wsDr>',
                )
            target.writestr(info, content)
    source = output.getvalue()
    parsed = XlsxAdapter().parse(source)
    segment = next(s for s in parsed.segments if s.display_text == "Drawing label")
    exported = XlsxExporter().export(source, [
        {"sentence_id": segment.segment_id, "target_text": "Translated label"},
    ])
    assert any(n.text_content == "Translated label" for n in XlsxAdapter().parse(exported).ast.nodes)


@pytest.mark.parametrize("invalid_guid", [False, True])
def test_other_xml_errors_are_not_ignored(invalid_guid):
    with pytest.raises(XMLSyntaxError):
        load_xlsx_workbook(workbook_bytes(invalid_guid=invalid_guid, broken_xml=True))
