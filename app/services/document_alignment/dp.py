from __future__ import annotations

import math
from dataclasses import dataclass, field

from .features import punctuation_features, unit_numbering
from .parser import AlignUnit

ALIGN_OPS = ((1, 1), (1, 2), (2, 1), (1, 0), (0, 1), (2, 2), (1, 3), (3, 1))
OP_PENALTIES = {(1, 1): 0.0, (1, 2): 0.3, (2, 1): 0.3, (1, 0): 1.5, (0, 1): 1.5, (2, 2): 2.5, (1, 3): 2.5, (3, 1): 2.5}


@dataclass
class AlignPair:
    src_indices: list[int]
    tgt_indices: list[int]
    confidence: float
    method: str = "dp"
    features: dict = field(default_factory=dict)

    @property
    def confidence_level(self) -> str:
        return "high" if self.confidence >= 0.75 else "medium" if self.confidence >= 0.45 else "low"


def _group_values(units: list[AlignUnit]) -> tuple[int, set[str], str, bool, dict]:
    length = sum(unit.char_len for unit in units)
    numbers = {number for unit in units for number in unit.numbers}
    numberings = {unit_numbering(unit) for unit in units if unit_numbering(unit)}
    numbering = next(iter(numberings)) if len(numberings) == 1 else "|".join(sorted(numberings))
    is_heading = any(unit.is_heading for unit in units)
    punctuation = punctuation_features(" ".join(unit.text for unit in units))
    return length, numbers, numbering, is_heading, punctuation


def _transition_cost(src: list[AlignUnit], tgt: list[AlignUnit], op: tuple[int, int], ratio: float) -> tuple[float, dict]:
    if not src or not tgt:
        cost = OP_PENALTIES[op] + 1.0
        return cost, {"op": f"{op[0]}-{op[1]}", "gap": True, "total_cost": cost}
    src_len, src_numbers, src_numbering, src_heading, src_punct = _group_values(src)
    tgt_len, tgt_numbers, tgt_numbering, tgt_heading, tgt_punct = _group_values(tgt)
    expected = max(1.0, src_len * ratio)
    delta = abs(tgt_len - expected) / math.sqrt(max(1.0, src_len * 6.8))
    # 稳定的 Gale-Church 尾概率近似；封顶避免极端长度支配全部证据。
    length_cost = min(8.0, -math.log(max(1e-6, math.erfc(delta / math.sqrt(2.0)))))
    if not src_numbers and not tgt_numbers:
        number_cost = 0.0
    else:
        union = src_numbers | tgt_numbers
        number_cost = 3.0 * (1.0 - len(src_numbers & tgt_numbers) / max(1, len(union)))
    numbering_cost = 5.0 if src_numbering and tgt_numbering and src_numbering != tgt_numbering else 0.0
    punct_cost = 0.0
    if src_punct["ending"] != tgt_punct["ending"]:
        punct_cost += 0.2
    punct_cost += min(0.2, abs(int(src_punct["brackets"]) - int(tgt_punct["brackets"])) * 0.1)
    structure_cost = 0.5 if src_heading != tgt_heading else 0.0
    total = length_cost + number_cost + numbering_cost + punct_cost + structure_cost + OP_PENALTIES[op]
    return total, {
        "op": f"{op[0]}-{op[1]}", "length_cost": round(length_cost, 4),
        "number_cost": round(number_cost, 4), "numbering_cost": numbering_cost,
        "structure_cost": structure_cost, "punctuation_cost": round(punct_cost, 4),
        "total_cost": round(total, 4),
    }


def _confidence(cost: float, op: tuple[int, int]) -> float:
    base = math.exp(-cost / 5.0)
    if op in {(1, 0), (0, 1)}:
        base = min(base, 0.35)
    elif op in {(2, 2), (1, 3), (3, 1)}:
        base = min(base, 0.44)
    return round(max(0.0, min(1.0, base)), 4)


def align_block(src: list[AlignUnit], tgt: list[AlignUnit], *, lang_ratio: float = 1.0) -> list[AlignPair]:
    """动态规划对齐，并保证两侧每个输入下标恰好出现一次。"""
    n, m = len(src), len(tgt)
    if not n:
        return [AlignPair([], [unit.index], 0.2, features={"op": "0-1", "gap": True}) for unit in tgt]
    if not m:
        return [AlignPair([unit.index], [], 0.2, features={"op": "1-0", "gap": True}) for unit in src]
    # 局部比例校正，既保留语言先验，也降低文档风格差异造成的漂移。
    observed = sum(unit.char_len for unit in tgt) / max(1, sum(unit.char_len for unit in src))
    ratio = max(0.2, min(5.0, lang_ratio * 0.35 + observed * 0.65))
    inf = float("inf")
    scores = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, tuple[int, int], dict] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    scores[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if scores[i][j] == inf:
                continue
            for a, b in ALIGN_OPS:
                ni, nj = i + a, j + b
                if ni > n or nj > m:
                    continue
                cost, features = _transition_cost(src[i:ni], tgt[j:nj], (a, b), ratio)
                candidate = scores[i][j] + cost
                if candidate < scores[ni][nj]:
                    scores[ni][nj] = candidate
                    back[ni][nj] = (i, j, (a, b), features)
    pairs: list[AlignPair] = []
    i, j = n, m
    while i or j:
        item = back[i][j]
        if item is None:
            raise RuntimeError("对齐回溯失败。")
        pi, pj, op, features = item
        pairs.append(AlignPair(
            [unit.index for unit in src[pi:i]], [unit.index for unit in tgt[pj:j]],
            _confidence(float(features["total_cost"]), op), features=features,
        ))
        i, j = pi, pj
    pairs.reverse()
    assert sorted(index for pair in pairs for index in pair.src_indices) == sorted(unit.index for unit in src)
    assert sorted(index for pair in pairs for index in pair.tgt_indices) == sorted(unit.index for unit in tgt)
    return pairs
