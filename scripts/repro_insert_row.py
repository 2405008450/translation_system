"""最小复现：手动构造两个在同一 INSERT scope 内、world y 接近但 local y 差
一整个字高的 TextEntity，验证 _compute_edge 是否已按 local 坐标归行。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.adapters.text_reconstruction import (  # noqa: E402
    TextEntity,
    TextFlowGraph,
)


def make(handle: str, text: str, wx: float, wy: float, lx: float, ly: float,
         height: float = 250.0, width: float = 4000.0,
         scope: str = "layout:Model:insert:A$STAMP") -> TextEntity:
    return TextEntity(
        handle=handle,
        entity_type="TEXT",
        layer="0",
        text=text,
        x=wx,
        y=wy,
        height=height,
        width=width,
        rotation=0.0,
        style="Standard",
        scope=scope,
        local_x=lx,
        local_y=ly,
        local_width=width,
    )


def main() -> None:
    # 模拟 handle=100A 场景：world y 接近，local y 相差 2000 单位（block 内不同行）
    e_jiangxi_zh = make("A", "江西省环球建筑设计院有限公司", wx=-21247.62, wy=3095.24, lx=100.0, ly=2000.0, height=480.0, width=6720.0)
    e_jiangxi_en = make("B", "JIANGXI GLOBAL ARCHITECTURE", wx=-21883.97, wy=3118.34, lx=100.0, ly=4000.0, height=250.0, width=4050.0)
    e_national   = make("C", "NATIONAL ARCHITECTURAL DESIGN LICENSE No.A136003664", wx=-23645.43, wy=2938.45, lx=100.0, ly=6000.0, height=257.42, width=4332.39)

    graph = TextFlowGraph()
    for entity in (e_jiangxi_zh, e_jiangxi_en, e_national):
        graph.add_entity(entity)
    graph.build_edges()

    edge_ab = graph._compute_edge(e_jiangxi_zh, e_jiangxi_en)
    edge_bc = graph._compute_edge(e_jiangxi_en, e_national)
    edge_ac = graph._compute_edge(e_jiangxi_zh, e_national)
    print("edge(江西省 <-> JIANGXI):", edge_ab)
    print("edge(JIANGXI <-> NATIONAL):", edge_bc)
    print("edge(江西省 <-> NATIONAL):", edge_ac)
    print("reject reasons:", graph.reject_reasons)


if __name__ == "__main__":
    main()
