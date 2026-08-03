"""联网查证关闭时的空实现。"""
from __future__ import annotations


class NoopVerifier:
    key = "none"

    def is_available(self) -> bool:
        return True

    async def augment_messages(
        self,
        messages: list[dict],
        *,
        queries: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        return messages, {}
