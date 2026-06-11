"""规则提取器 - 从文档中提取翻译规格"""

import re
from typing import List, Optional
from .schema import (
    TranslationProfile, Constraints, References,
    TermEntry, SentencePair, StyleGuide, FileType,
)
from .parser.base import Document, Table
from .classifier import classify_document


def extract_profile(doc: Document, file_type: Optional[FileType] = None) -> TranslationProfile:
    """从文档中提取翻译规格"""
    if file_type is None:
        file_type = classify_document(doc)

    profile = TranslationProfile(source_files=[doc.filename])

    if file_type == FileType.TERMINOLOGY:
        profile.constraints.terminology = _extract_terminology(doc)

    elif file_type == FileType.TRANSLATION_MEMORY:
        profile.references.translation_memory = _extract_tm_from_paragraphs(doc)

    elif file_type == FileType.STYLE_GUIDE:
        profile.references.style = _extract_style_guide(doc)

    elif file_type == FileType.BILINGUAL:
        # 双语对照：短的存术语，长的存TM
        terms, tm = _extract_bilingual(doc)
        profile.constraints.terminology = terms
        profile.references.translation_memory = tm

    elif file_type == FileType.MIXED:
        # 混合型：尝试提取所有
        profile.constraints.terminology = _extract_terminology(doc)
        profile.references.style = _extract_style_guide(doc)

    return profile


def _extract_terminology(doc: Document) -> List[TermEntry]:
    """从表格中提取术语（带去重）"""
    terms = []
    seen = set()  # 用于去重
    
    for table in doc.tables:
        if len(table.headers) < 2:
            continue
        for row in table.rows:
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                source = row[0].strip()
                target = row[1].strip()
                # 去重键
                pair_key = (source.lower(), target.lower())
                if pair_key not in seen:
                    entry = TermEntry(
                        source=source,
                        target=target,
                        context=row[2].strip() if len(row) > 2 and row[2].strip() else None,
                    )
                    terms.append(entry)
                    seen.add(pair_key)
    return terms


def _extract_tm_from_paragraphs(doc: Document) -> List[SentencePair]:
    """从交替段落中提取翻译记忆句对"""
    cn_pattern = re.compile(r"[\u4e00-\u9fff]")
    pairs = []
    paragraphs = doc.paragraphs

    i = 0
    while i < len(paragraphs) - 1:
        curr = paragraphs[i].text
        next_p = paragraphs[i + 1].text

        curr_is_en = len(cn_pattern.findall(curr)) / max(len(curr), 1) < 0.1
        next_is_zh = len(cn_pattern.findall(next_p)) / max(len(next_p), 1) > 0.3

        if curr_is_en and next_is_zh:
            pairs.append(SentencePair(source=curr, target=next_p))
            i += 2
        else:
            i += 1

    return pairs


def _extract_style_guide(doc: Document) -> StyleGuide:
    """从文本中提取风格指南"""
    text = doc.raw_text
    style = StyleGuide()

    # 语气
    if "正式" in text or "书面" in text:
        style.tone = "formal"
    elif "口语" in text or "轻松" in text:
        style.tone = "casual"

    # 人称
    if "第三人称" in text:
        style.person = "third"
    elif "第一人称" in text:
        style.person = "first"

    # 禁用词/禁用表达
    avoid_match = re.findall(r"[不禁]得?[用使]用[\"「](.+?)[\"」]", text)
    style.avoid = avoid_match

    # 整体偏好描述 - 提取含"应当""建议""请"的句子
    pref_patterns = re.findall(r"[应建请].{5,30}[。\n]", text)
    style.preferences = [p.strip().rstrip("。") for p in pref_patterns[:10]]

    return style


def _extract_bilingual(doc: Document):
    """从双语对照文件中提取术语和TM（带去重）"""
    terms = []
    tm = []
    seen_terms = set()
    seen_tm = set()

    # 先尝试从表格提取
    if doc.tables:
        for table in doc.tables:
            for row in table.rows:
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    source = row[0].strip()
                    target = row[1].strip()
                    pair_key = (source.lower(), target.lower())
                    
                    # 使用统一的术语判断逻辑
                    if _is_term_level(source, target):
                        if pair_key not in seen_terms:
                            terms.append(TermEntry(source=source, target=target))
                            seen_terms.add(pair_key)
                    else:
                        if pair_key not in seen_tm:
                            tm.append(SentencePair(source=source, target=target))
                            seen_tm.add(pair_key)

    # 再从段落提取
    if not terms and not tm:
        tm = _extract_tm_from_paragraphs(doc)
        # 从TM中分离出短条目作为术语，并去重
        real_tm = []
        for pair in tm:
            pair_key = (pair.source.lower(), pair.target.lower())
            if _is_term_level(pair.source, pair.target):
                if pair_key not in seen_terms:
                    terms.append(TermEntry(source=pair.source, target=pair.target))
                    seen_terms.add(pair_key)
            else:
                if pair_key not in seen_tm:
                    real_tm.append(pair)
                    seen_tm.add(pair_key)
        tm = real_tm

    return terms, tm


def _is_term_level(source: str, target: str) -> bool:
    """判断是否为术语级别
    
    术语标准：
    1. 原文较短（通常是词组或短语，而非完整句子）
    2. 原文不包含句子结束标点
    3. 原文单词数较少
    """
    # 句子结束标点（中英文）
    sentence_end_marks = '.。!！?？;；'
    
    # 如果包含句子结束标点，很可能是完整句子，归类为TM
    if any(mark in source for mark in sentence_end_marks):
        return False
    
    # 计算原文单词数（英文按空格分，中文按字符估算）
    source_stripped = source.strip()
    
    # 判断是否主要是中文
    chinese_chars = len([c for c in source_stripped if '\u4e00' <= c <= '\u9fff'])
    is_mostly_chinese = chinese_chars > len(source_stripped) * 0.3
    
    if is_mostly_chinese:
        # 中文：按字符数判断（术语通常较短）
        return len(source_stripped) <= 15
    else:
        # 英文：按单词数判断
        word_count = len(source_stripped.split())
        return word_count <= 5 and len(source_stripped) <= 50
