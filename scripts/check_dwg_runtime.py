"""在部署容器内验证 DWG 导入、译文回写及 DWG/DXF 导出，不写入业务数据。"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ezdxf

from app.config import get_settings
from app.services.adapters.dwg_adapter import DwgAdapter
from app.services.adapters.dwg_converter import dxf_to_dwg
from app.services.adapters.dwg_exporter import DwgExporter
from app.services.adapters.dxf_adapter import DxfAdapter


OPTIONS = {"enable_llm_layout": False, "enable_spatial_merge": False,
           "skip_non_translatable": False}


def check(data: bytes, name: str, translations: dict[str, str]) -> dict:
    adapter = DwgAdapter()
    parsed = adapter.parse_with_options(data, filename=name, options=OPTIONS)
    assert parsed.segments, f"{name}: 未提取到文本"
    sources = {segment.source_text for segment in parsed.segments}
    assert set(translations).issubset(sources), f"{name}: 测试原文提取失败"
    outputs = []
    for prefer_dwg in (False, True):
        result = DwgExporter().export_with_extension(
            data, translations, prefer_dwg=prefer_dwg,
        )
        expected = ".dwg" if prefer_dwg else ".dxf"
        assert result.extension == expected, f"{name}: 输出格式错误"
        assert not result.fallback_used, f"{name}: 发生 DXF 降级"
        assert result.content, f"{name}: 输出为空"
        if prefer_dwg:
            assert result.content.startswith(b"AC10"), f"{name}: DWG 文件头错误"
            reparsed = adapter.parse_with_options(result.content, filename=name, options=OPTIONS)
        else:
            # DWG 解压后的 DXF 可能远大于原件；这里只验证转换内容。
            settings = get_settings()
            reparsed = DxfAdapter(max_file_size=len(result.content)).parse_with_options(
                result.content, filename="check.dxf", options={
                    **OPTIONS,
                    "extract_extra_entities": settings.dwg_handle_extra_entities,
                    "skip_dimension_like": settings.dwg_skip_dimension_like,
                },
            )
        texts = {segment.source_text for segment in reparsed.segments}
        assert texts, f"{name}: 导出文件无法重新提取文本"
        assert set(translations.values()).issubset(texts), f"{name}: 译文回写验证失败"
        if not translations:
            assert texts == sources, (
                f"{name} ({expected}): 往返转换后文本发生变化，"
                f"missing={sorted(sources - texts)[:5]!r}, added={sorted(texts - sources)[:5]!r}"
            )
        outputs.append({"extension": expected, "bytes": len(result.content),
                        "segments": len(reparsed.segments), "fallback_used": False})
    return {"file": name, "input_bytes": len(data), "segments": len(parsed.segments),
            "translation_verified": bool(translations), "outputs": outputs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="额外验证一份现有 DWG，只读且不覆盖原件")
    args = parser.parse_args()
    document = ezdxf.new("R2018")
    space = document.modelspace()
    space.add_text("振动给料机", dxfattribs={"height": 2.5, "insert": (0, 0)})
    space.add_mtext("除铁器", dxfattribs={"char_height": 2.5, "insert": (0, 20), "width": 100})
    space.add_line((0, -5), (100, -5))
    buffer = io.StringIO()
    document.write(buffer)
    dwg = dxf_to_dwg(buffer.getvalue().encode("utf-8"))
    checks = [check(dwg, "generated-chinese-text.dwg",
                    {"振动给料机": "Vibrating feeder", "除铁器": "Magnetic separator"})]
    if args.input:
        checks.append(check(args.input.read_bytes(), args.input.name, {}))
    print(json.dumps({"status": "passed", "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
