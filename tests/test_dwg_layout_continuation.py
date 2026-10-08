import pytest

from app.services.adapters.dwg_llm_layout import _groups_respect_geometry
from app.services.adapters.text_reconstruction import (
    BarrierIndex,
    BarrierLine,
    TextEntity,
    TextFlowGraph,
    estimate_text_width,
)


def entity(handle, text, x=0, y=0, scope="model"):
    return TextEntity(
        handle=handle, entity_type="TEXT", layer="notes", text=text,
        x=x, y=y, height=1, width=estimate_text_width(text, 1), scope=scope,
    )


def test_llm_cannot_take_only_numeric_tail_of_long_cjk_row():
    text = "消火栓的安装位置应确保其顶部离地面高度"
    left = entity("A", text)
    right = entity("B", "1.5m", x=len(text) * 0.8)
    assert TextFlowGraph()._compute_edge(left, right) is not None
    assert _groups_respect_geometry([["A", "B"]], [left, right], None)
    assert not _groups_respect_geometry([["B"]], [left, right], None)


def test_llm_cannot_split_wrapped_sentence_after_open_parenthesis():
    upper = entity("A", "按原规格重新砌筑损坏的砖砌阀门井（", y=1.5)
    lower = entity("B", "JZ3）")
    assert _groups_respect_geometry([["A", "B"]], [upper, lower], None)
    assert not _groups_respect_geometry([["A"], ["B"]], [upper, lower], None)


@pytest.mark.parametrize("first,second", [
    ("安装完成。", "检查压力"),
    ("供水说明", "排水说明"),
])
def test_independent_wrapped_annotations_can_remain_separate(first, second):
    entities = [entity("A", first, y=1.5), entity("B", second)]
    assert _groups_respect_geometry([["A"], ["B"]], entities, None)


def test_scope_boundary_is_not_treated_as_glued_text():
    entities = [entity("A", "安装在"), entity("B", "支架上", x=3, scope="block")]
    assert _groups_respect_geometry([["A"], ["B"]], entities, None)


def test_grid_barrier_allows_separate_groups():
    barriers = BarrierIndex()
    barriers.add(BarrierLine(axis="h", pos=1.25, range_min=-1, range_max=10, scope="model"))
    entities = [entity("A", "安装在", y=2), entity("B", "支架上")]
    assert _groups_respect_geometry([["A"], ["B"]], entities, barriers)


def test_actual_deep_overlap_still_rejects_geometry_edge():
    left = entity("A", "消火栓的安装位置应确保其顶部离地面高度")
    right = entity("B", "1.5m", x=5)
    assert TextFlowGraph()._compute_edge(left, right) is None
