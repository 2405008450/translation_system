"""
自研联网插件预留接口。

协议：POST {CUSTOM_URL} 发送 {"query": str, "max_results": int}
返回 {"results": [{"title": str, "url": str, "snippet": str}]}

本模块仅提供框架；业务团队实现具体 HTTP 调用后替换 _call_search_api。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CustomVerifier:
    key = "custom"

    def is_available(self) -> bool:
        from app.config import get_settings
        settings = get_settings()
        return bool(settings.translation_review_web_search_custom_url)

    async def augment_messages(
        self,
        messages: list[dict],
        *,
        queries: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        if not queries:
            return messages, {}
        results = await self._search(queries)
        if not results:
            return messages, {}
        ctx_lines = [f"- [{r['title']}]({r['url']})\n  {r['snippet']}" for r in results[:10]]
        context = (
            "\n\n[联网查证结果]\n" + "\n".join(ctx_lines) + "\n\n请参考上述查证结果辅助判断专有名词译法。"
        )
        augmented = list(messages)
        for i, msg in enumerate(augmented):
            if msg.get("role") == "system":
                augmented[i] = {**msg, "content": msg["content"] + context}
                break
        return augmented, {}

    async def _search(self, queries: list[str]) -> list[dict]:
        from app.config import get_settings
        settings = get_settings()
        url = settings.translation_review_web_search_custom_url
        api_key = settings.translation_review_web_search_custom_api_key
        if not url:
            return []
        try:
            import httpx
            all_results: list[dict] = []
            async with httpx.AsyncClient(timeout=10) as client:
                for q in queries[:3]:
                    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                    resp = await client.post(url, json={"query": q, "max_results": 5}, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    all_results.extend(data.get("results") or [])
            return all_results
        except Exception as exc:  # noqa: BLE001
            logger.warning("custom web search failed: %s", exc)
            return []
