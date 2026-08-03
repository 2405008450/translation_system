"""
OpenRouter Web Search Server Tool 实现。
参考：https://openrouter.ai/docs/guides/features/server-tools/web-search（Beta）

启用方式：在请求体的 tools 数组加入 { "type": "openrouter:web_search" }。
具体参数（engine / max_results / max_uses 等）由 orchestrator._build_web_tools() 组装，
该实现仅提供 augment_messages() 接口以便未来扩展（如往 system 里注入查证背景）。
"""
from __future__ import annotations


class OpenRouterVerifier:
    key = "openrouter"

    def is_available(self) -> bool:
        from app.config import get_settings
        # 只有 openrouter provider 可用时才有意义
        settings = get_settings()
        return bool(settings.openrouter_api_key)

    async def augment_messages(
        self,
        messages: list[dict],
        *,
        queries: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        """
        web_search tool 由 orchestrator._build_web_tools() 构造后通过
        request_chat_completion 的 tools 参数传入，这里不做额外处理。
        只注入一条 system 提示，说明模型可以搜索查证。
        """
        system_hint = (
            "\n\n[联网查证已开启] 遇到专有名词、机构名、职位等需要核实官方英文译法时，"
            "可发起网络搜索。仅当确有必要时才搜索，控制搜索次数。"
        )
        augmented = list(messages)
        for i, msg in enumerate(augmented):
            if msg.get("role") == "system":
                augmented[i] = {**msg, "content": msg["content"] + system_hint}
                break
        return augmented, {}
