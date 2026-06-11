"""LLM辅助功能 - 使用翻译系统的独立参考分析 LLM 配置"""

import base64
import json
from typing import Optional, List

from .schema import FileType, StyleGuide


class ReferenceLLMHelper:
    """参考分析专用 LLM 辅助处理器
    
    使用独立的 REFERENCE_LLM_* 配置，与翻译用的 LLM 分开
    """

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemini-2.0-flash-001",
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = None

    @property
    def client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _call(self, system: str, user: str, temperature: float = 0.2) -> str:
        """调用 LLM API"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()

    def extract_pdf_text(self, pdf_path: str) -> str:
        """用 LLM 直接读取 PDF 文件提取完整文本"""
        # 读取 PDF 文件并转为 base64
        with open(pdf_path, "rb") as f:
            pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")
        
        system = "你是文档处理专家。请提取PDF文件中的所有文字内容，严格保持原有的段落和句子结构。"
        user_content = [
            {
                "type": "text",
                "text": """请提取这个PDF文件中的所有文字内容。

重要要求：
1. 保持每个段落、每个条目独立成行
2. 标题单独一行
3. 编号条目（如"1."、"第一条"、"（一）"等）每条单独一行
4. 不要合并相邻的段落或句子
5. 保持原文的完整性，不要省略任何内容

只输出提取的文字，不要添加任何解释或标记。"""
            },
            {
                "type": "file",
                "file": {
                    "filename": pdf_path.split("/")[-1].split("\\")[-1],
                    "file_data": f"data:application/pdf;base64,{pdf_data}"
                }
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=8000,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[ReferenceLLM] PDF提取失败: {e}")
            return ""

    def classify_document(self, text_sample: str) -> FileType:
        """当规则判断不了时，用LLM分类文件类型"""
        system = "你是翻译项目助手，负责判断参考文件的类型。只输出类型编号，不要解释。"
        user = f"""以下是用户提供的参考文件内容片段，请判断这份文件属于哪种类型：
1. terminology - 术语表（术语对照、词汇对照）
2. tm - 翻译记忆（双语句对、段落对照）
3. style_guide - 风格指南（语气、用词偏好、禁用词）
4. bilingual - 双语对照文件（整篇的原文和译文对照）
5. mixed - 混合型（包含多种类型）

文件内容：
\"\"\"
{text_sample[:1500]}
\"\"\"

请只输出类型英文标识（如 terminology、tm、style_guide 等）："""

        result = self._call(system, user)
        result = result.strip().lower().replace('"', '').replace("'", "")

        type_map = {
            "terminology": FileType.TERMINOLOGY,
            "tm": FileType.TRANSLATION_MEMORY,
            "style_guide": FileType.STYLE_GUIDE,
            "bilingual": FileType.BILINGUAL,
            "mixed": FileType.MIXED,
        }
        return type_map.get(result, FileType.UNKNOWN)

    def analyze_style_from_pairs(self, pairs: List[dict]) -> StyleGuide:
        """从双语句对中分析翻译风格特征"""
        system = "你是翻译质量分析专家。请分析以下翻译句对的风格特征。"

        pairs_text = ""
        for i, pair in enumerate(pairs[:15], 1):
            pairs_text += f"{i}. 原文：{pair['source']}\n   译文：{pair['target']}\n\n"

        user = f"""请分析以下翻译句对的风格特征，按以下格式输出JSON：
{{
    "tone": "formal/casual/neutral",
    "person": "first/third/none",
    "preferences": ["偏好描述1", "偏好描述2", ...],
    "avoid": ["应避免的表达1", ...]
}}

翻译句对：
{pairs_text}

请输出JSON（不要代码块标记）："""

        result = self._call(system, user)
        return self._parse_style_json(result)

    def _parse_style_json(self, text: str) -> StyleGuide:
        """解析LLM返回的风格JSON"""
        style = StyleGuide()
        try:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            data = json.loads(text)
            style.tone = data.get("tone")
            style.person = data.get("person")
            style.preferences = data.get("preferences", [])
            style.avoid = data.get("avoid", [])
        except (json.JSONDecodeError, KeyError):
            pass
        return style
