"""内容分类器 - 判断参考文件类型"""

import re
from typing import List
from .schema import FileType
from .parser.base import Document


# 风格指南关键词
STYLE_KEYWORDS = [
    "语气", "风格", "避免", "不得使用", "应当", "请勿",
    "tone", "style", "avoid", "formal", "casual",
    "第一人称", "第三人称", "正式", "口语",
]


def classify_document(doc: Document) -> FileType:
    """基于规则的文件类型判断"""

    # 1. 有表格且表格看起来是术语对照
    if doc.tables:
        for table in doc.tables:
            if _is_terminology_table(table):
                return FileType.TERMINOLOGY

    # 2. 检查是否有风格指南关键词
    text_lower = doc.raw_text.lower()
    style_hits = sum(1 for kw in STYLE_KEYWORDS if kw in text_lower)

    if style_hits >= 3:
        return FileType.STYLE_GUIDE

    # 3. 检查是否为双语段落对照（段落中中英交替出现）
    if _has_bilingual_pattern(doc.paragraphs):
        return FileType.TRANSLATION_MEMORY

    return FileType.UNKNOWN


def _is_terminology_table(table) -> bool:
    """判断表格是否为术语表"""
    if not table.headers:
        return False

    headers_lower = [h.lower() for h in table.headers]

    # 表头包含常见术语表标识
    term_headers = [
        ("source", "target"), ("原文", "译文"), ("english", "chinese"),
        ("en", "zh"), ("en", "cn"), ("术语", "翻译"), ("term", "translation"),
    ]
    for h1, h2 in term_headers:
        if any(h1 in h for h in headers_lower) and any(h2 in h for h in headers_lower):
            return True

    # 只有2-3列且内容较短（术语通常短）
    if len(table.headers) <= 3 and table.rows:
        avg_len = sum(
            len(cell) for row in table.rows[:10] for cell in row
        ) / max(len(table.rows[:10]) * len(table.headers), 1)
        if avg_len < 30:
            return True

    return False


def _has_bilingual_pattern(paragraphs) -> bool:
    """判断段落是否呈现双语交替模式"""
    if len(paragraphs) < 4:
        return False

    cn_pattern = re.compile(r"[\u4e00-\u9fff]")
    lang_sequence = []
    for p in paragraphs[:20]:
        has_cn = bool(cn_pattern.search(p.text))
        cn_ratio = len(cn_pattern.findall(p.text)) / max(len(p.text), 1)
        if cn_ratio > 0.3:
            lang_sequence.append("zh")
        elif cn_ratio < 0.1:
            lang_sequence.append("en")
        else:
            lang_sequence.append("mixed")

    # 中英交替出现
    alternating = 0
    for i in range(len(lang_sequence) - 1):
        if lang_sequence[i] != lang_sequence[i + 1] and "mixed" not in (
            lang_sequence[i], lang_sequence[i + 1]
        ):
            alternating += 1

    return alternating >= len(lang_sequence) * 0.3
