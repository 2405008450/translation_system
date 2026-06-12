"""相似度匹配 - 基于Embedding的TM匹配"""

from typing import List, Optional
import numpy as np

from .schema import SentencePair


class EmbeddingMatcher:
    """基于Embedding向量的相似度匹配器"""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: Optional[str] = None):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self._cache: dict = {}  # 缓存embedding结果

    def get_embedding(self, text: str) -> List[float]:
        """获取文本的embedding向量"""
        if text in self._cache:
            return self._cache[text]

        response = self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        embedding = response.data[0].embedding
        self._cache[text] = embedding
        return embedding

    def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """批量获取embedding"""
        # 过滤已缓存的
        uncached = [t for t in texts if t not in self._cache]

        if uncached:
            # 批量请求（每批最多100条）
            for i in range(0, len(uncached), 100):
                batch = uncached[i:i+100]
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
                for text, data in zip(batch, response.data):
                    self._cache[text] = data.embedding

        return [self._cache[t] for t in texts]

    def find_similar_pairs(
        self,
        query_text: str,
        tm_pairs: List[SentencePair],
        top_k: int = 5,
        threshold: float = 0.5,
    ) -> List[SentencePair]:
        """找出与query_text最相似的TM句对"""
        if not tm_pairs:
            return []

        # 获取query的embedding
        query_emb = self.get_embedding(query_text)

        # 获取所有TM source的embedding
        source_texts = [pair.source for pair in tm_pairs]
        source_embs = self.get_embeddings_batch(source_texts)

        # 计算余弦相似度
        similarities = []
        for i, source_emb in enumerate(source_embs):
            sim = self._cosine_similarity(query_emb, source_emb)
            if sim >= threshold:
                similarities.append((sim, tm_pairs[i]))

        # 按相似度排序
        similarities.sort(key=lambda x: x[0], reverse=True)

        # 返回top_k并设置相似度
        results = []
        for sim, pair in similarities[:top_k]:
            result_pair = SentencePair(
                source=pair.source,
                target=pair.target,
                similarity=sim,
            )
            results.append(result_pair)

        return results

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """余弦相似度"""
        a_arr = np.array(a)
        b_arr = np.array(b)
        dot = np.dot(a_arr, b_arr)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))


class SimpleMatcher:
    """简单匹配器 - 不依赖embedding API，基于词汇重合度"""

    def find_similar_pairs(
        self,
        query_text: str,
        tm_pairs: List[SentencePair],
        top_k: int = 5,
        threshold: float = 0.2,
    ) -> List[SentencePair]:
        """基于词汇重合度的简单匹配"""
        if not tm_pairs:
            return []

        query_words = set(query_text.lower().split())
        scored = []

        for pair in tm_pairs:
            source_words = set(pair.source.lower().split())
            if not source_words:
                continue
            overlap = len(query_words & source_words)
            score = overlap / len(source_words)
            if score >= threshold:
                scored.append((score, pair))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, pair in scored[:top_k]:
            results.append(SentencePair(
                source=pair.source,
                target=pair.target,
                similarity=score,
            ))
        return results
