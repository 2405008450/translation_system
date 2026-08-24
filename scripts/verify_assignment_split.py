"""按字数拆分算法自检。

运行：.venv/Scripts/python scripts/verify_assignment_split.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.assignment_split import SplitUnit, split_units_by_weight


def unit(start: int, end: int, words: int) -> SplitUnit:
    return SplitUnit(start, end, end - start + 1, words)


def ranges(parts: list[list[SplitUnit]]) -> list[tuple[int, int]]:
    return [(part[0].range_start, part[-1].range_end) for part in parts]


def verify_equal_units() -> None:
    parts = split_units_by_weight([unit(i, i, 100) for i in range(1, 7)], 3)
    assert ranges(parts) == [(1, 2), (3, 4), (5, 6)]


def verify_uneven_units() -> None:
    parts = split_units_by_weight(
        [unit(1, 1, 20), unit(2, 2, 480), unit(3, 3, 30), unit(4, 4, 470)],
        2,
    )
    assert ranges(parts) == [(1, 2), (3, 4)]


def verify_part_count_is_limited_by_safe_units() -> None:
    parts = split_units_by_weight([unit(1, 2, 100), unit(3, 5, 100)], 8)
    assert ranges(parts) == [(1, 2), (3, 5)]


def verify_zero_words_use_segment_counts() -> None:
    parts = split_units_by_weight(
        [unit(1, 2, 0), unit(3, 3, 0), unit(4, 6, 0)],
        2,
        use_segment_weight=True,
    )
    assert ranges(parts) == [(1, 3), (4, 6)]


def verify_complete_blocks_are_never_cut() -> None:
    # 2–4 代表同一段落/表格单元格聚合出的不可拆分块。
    safe_units = [unit(1, 1, 40), unit(2, 4, 700), unit(5, 5, 40)]
    parts = split_units_by_weight(safe_units, 2)
    boundaries = {end for _, end in ranges(parts)[:-1]}
    assert not boundaries.intersection({2, 3})
    assert any(part[0].range_start <= 2 and part[-1].range_end >= 4 for part in parts)


def verify_words_per_part_equivalent_count() -> None:
    # 总字数 1000、每份 300 字时会生成 ceil(1000 / 300) == 4 份。
    parts = split_units_by_weight([unit(i, i, 100) for i in range(1, 11)], 4)
    assert len(parts) == 4
    assert ranges(parts)[0][0] == 1
    assert ranges(parts)[-1][1] == 10


def main() -> None:
    verify_equal_units()
    verify_uneven_units()
    verify_part_count_is_limited_by_safe_units()
    verify_zero_words_use_segment_counts()
    verify_complete_blocks_are_never_cut()
    verify_words_per_part_equivalent_count()
    print("assignment split verification passed")


if __name__ == "__main__":
    main()
