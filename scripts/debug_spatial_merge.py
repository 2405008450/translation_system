"""Ad-hoc 诊断：跑一份 DWG 的空间合并解析，重点看给定关键字所在句段的
metadata（group_width / merged_handles / scope / layer 等），用来回答
"为什么某块文字没有拿到矩形框"。

用法（在项目根目录、激活 .venv 后）：
    python scripts/debug_spatial_merge.py <dwg_path> [--pattern "JGADI|Jiangxi|drawings shall"]

参数说明：
    dwg_path  可以是 .dwg（会走 ODA -> DXF）或 .dxf。
    --pattern 单个正则，命中 sentence.text 才打详细信息，默认打印所有 group_width==0 的
              句段和前 30 条命中关键字的句段。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# 让 app 包可以直接被 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.adapters.dwg_adapter import DwgAdapter  # noqa: E402
from app.services.adapters.dxf_adapter import DxfAdapter  # noqa: E402


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[%(levelname)s %(name)s] %(message)s"))
        root.addHandler(handler)
    # 明确把 spatial-merge 相关模块的 debug 打出来
    logging.getLogger("app.services.adapters").setLevel(logging.INFO)


def _parse(path: Path):
    data = path.read_bytes()
    opts = {
        "skip_non_translatable": True,
        "enable_spatial_merge": True,
        # 本地诊断关掉 LLM 版面分析，避免真去调 openrouter
        "enable_llm_layout": False,
    }
    if path.suffix.lower() == ".dwg":
        adapter = DwgAdapter()
    else:
        adapter = DxfAdapter()
    return adapter.parse_with_options(data, filename=path.name, options=opts)


def _summarize(result, pattern: str | None) -> None:
    segments = result.segments
    nodes = result.ast.nodes
    print("=" * 80)
    print(f"总节点数: {len(nodes)}  总 segment 数: {len(segments)}")
    print(f"metadata: {json.dumps(result.metadata, ensure_ascii=False, default=str)[:500]}")
    print("=" * 80)

    zero_width = []
    matched = []
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None

    for node in nodes:
        meta = dict(node.metadata or {})
        # 反序列化 merged_handles / original_entities 展示更清晰
        if isinstance(meta.get("original_entities"), str):
            try:
                meta["original_entities"] = json.loads(meta["original_entities"])
            except Exception:
                pass
        text = (node.text_content or "").strip()
        gw = float(meta.get("group_width") or 0)
        merged = meta.get("is_merged", False)

        # 只关注 CAD 文本块
        if not meta.get("cad_text_block") and not merged:
            continue

        if regex and regex.search(text):
            matched.append((text, meta, gw))
        elif gw <= 0:
            zero_width.append((text, meta, gw))

    print(f"\n--- 命中正则的句段 (共 {len(matched)}，全部展示) ---")
    for i, (text, meta, gw) in enumerate(matched, 1):
        oe = meta.get("original_entities") or []
        print(f"\n[{i}] text = {text!r}")
        print(f"    handle={meta.get('handle')}  scope={meta.get('scope')}  layer={meta.get('layer')}")
        print(f"    is_merged={meta.get('is_merged')}  merged_count={meta.get('merged_count')}  "
              f"cad_table_cell={meta.get('cad_table_cell', False)}")
        print(f"    group_x={meta.get('group_x')}  group_y_top={meta.get('group_y_top')}  "
              f"group_width={gw}  group_height={meta.get('group_height')}")
        print(f"    primary=({meta.get('primary_x')}, {meta.get('primary_y')}, h={meta.get('primary_height')})")
        print(f"    merge_confidence={meta.get('merge_confidence')}  entity_count={len(oe)}")
        if oe:
            for e in oe[:6]:
                lx = e.get("local_x")
                ly = e.get("local_y")
                lx_str = f"{lx:.2f}" if isinstance(lx, (int, float)) else "None"
                ly_str = f"{ly:.2f}" if isinstance(ly, (int, float)) else "None"
                print(f"      - handle={e.get('handle')} type={e.get('entity_type')} "
                      f"x={e.get('x'):.2f} y={e.get('y'):.2f} local=({lx_str},{ly_str}) "
                      f"w={e.get('width'):.2f} h={e.get('height'):.2f} text={e.get('text')!r}")
            if len(oe) > 6:
                print(f"      ... 还有 {len(oe) - 6} 个实体省略")

    print(f"\n--- group_width<=0 的句段（前 20 个）---")
    for i, (text, meta, gw) in enumerate(zero_width[:20], 1):
        text_repr = repr(text)[:80]
        print(f"[{i}] text={text_repr}  scope={meta.get('scope')} layer={meta.get('layer')} "
              f"is_merged={meta.get('is_merged')} merged_count={meta.get('merged_count')}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", help="DWG 或 DXF 文件路径")
    p.add_argument(
        "--pattern",
        default=r"JGAD|Jiangxi|Global Architecture|Design Institute|GLOBAL ARCHITECTURE|"
                r"drawings shall|dimensions shall|Special Seal|Certificate",
        help="正则；命中 sentence.text 才详细打印",
    )
    args = p.parse_args()

    _configure_logging()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")
    result = _parse(path)
    _summarize(result, args.pattern)


if __name__ == "__main__":
    main()
