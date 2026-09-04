from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

try:
    import numpy as np
except ImportError:  # 保留无 NumPy 环境下的兼容路径。
    np = None

from app.config import Settings

from .parser import AlignUnit

logger = logging.getLogger(__name__)


class AlignmentSemanticScorer:
    """可选的跨语言 embedding 打分器；失败时调用方应直接降级到确定性 DP。"""

    def __init__(self, settings: Settings):
        from openai import OpenAI

        self.model = settings.alignment_embedding_model
        self.dimensions = settings.alignment_embedding_dimensions
        self.batch_size = max(1, settings.alignment_embedding_batch_size)
        self.concurrency = max(1, settings.alignment_embedding_concurrency)
        self.client = OpenAI(
            api_key=settings.alignment_embedding_api_key,
            base_url=settings.alignment_embedding_base_url or None,
            timeout=settings.alignment_embedding_timeout_seconds,
        )
        self._vectors: dict[tuple[str, int], Any] = {}
        self._group_vectors: dict[tuple[str, tuple[int, ...]], Any] = {}
        self._similarities: dict[tuple[tuple[int, ...], tuple[int, ...]], float] = {}

    def prepare(self, src: Sequence[AlignUnit], tgt: Sequence[AlignUnit]) -> None:
        entries = [
            ("source", unit.index, unit.text) for unit in src
            if ("source", unit.index) not in self._vectors
        ] + [
            ("target", unit.index, unit.text) for unit in tgt
            if ("target", unit.index) not in self._vectors
        ]
        chunks = [entries[offset:offset + self.batch_size] for offset in range(0, len(entries), self.batch_size)]

        def embed(chunk: list[tuple[str, int, str]]):
            kwargs = {
                "model": self.model,
                "input": [item[2] for item in chunk],
                "encoding_format": "float",
            }
            if self.dimensions:
                kwargs["dimensions"] = self.dimensions
            return chunk, self.client.embeddings.create(**kwargs)

        with ThreadPoolExecutor(max_workers=min(self.concurrency, max(1, len(chunks)))) as executor:
            responses = list(executor.map(embed, chunks))
        for chunk, response in responses:
            if len(response.data) != len(chunk):
                raise ValueError("embedding 返回数量与请求不一致。")
            for item, data in zip(chunk, response.data):
                vector = list(data.embedding)
                self._vectors[(item[0], item[1])] = (
                    np.asarray(vector, dtype=np.float32) if np is not None else vector
                )

    def clear(self) -> None:
        self._vectors.clear()
        self.clear_derived()

    def clear_derived(self) -> None:
        """保留单元向量，只释放可随时重算的组合向量和相似度缓存。"""
        self._group_vectors.clear()
        self._similarities.clear()

    @staticmethod
    def _mean(vectors: list[Any]) -> Any | None:
        if not vectors:
            return None
        if np is not None:
            return np.mean(np.stack(vectors), axis=0)
        dimensions = len(vectors[0])
        if not dimensions or any(len(vector) != dimensions for vector in vectors):
            return None
        return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimensions)]

    def _group_mean(self, side: str, units: list[AlignUnit]) -> Any | None:
        key = (side, tuple(unit.index for unit in units))
        if key not in self._group_vectors:
            self._group_vectors[key] = self._mean([
                self._vectors[(side, unit.index)]
                for unit in units if (side, unit.index) in self._vectors
            ])
        return self._group_vectors[key]

    def similarity(self, src: list[AlignUnit], tgt: list[AlignUnit]) -> float | None:
        cache_key = (
            tuple(unit.index for unit in src),
            tuple(unit.index for unit in tgt),
        )
        cached = self._similarities.get(cache_key)
        if cached is not None:
            return cached
        source = self._group_mean("source", src)
        target = self._group_mean("target", tgt)
        if source is None or target is None:
            return None
        if np is not None:
            dot = float(np.dot(source, target))
            norm = float(np.linalg.norm(source) * np.linalg.norm(target))
        else:
            dot = sum(a * b for a, b in zip(source, target))
            norm = math.sqrt(sum(value * value for value in source)) * math.sqrt(
                sum(value * value for value in target)
            )
        score = max(-1.0, min(1.0, dot / norm)) if norm else None
        if score is not None:
            self._similarities[cache_key] = score
        return score


def build_semantic_scorer(settings: Settings) -> AlignmentSemanticScorer | None:
    if not settings.alignment_embedding_enabled:
        return None
    api_key = settings.alignment_embedding_api_key or settings.openrouter_api_key
    if not api_key:
        return None
    effective_settings = settings.model_copy(update={
        "alignment_embedding_api_key": api_key,
        "alignment_embedding_base_url": (
            settings.alignment_embedding_base_url or settings.openrouter_base_url
        ),
    })
    return AlignmentSemanticScorer(effective_settings)
