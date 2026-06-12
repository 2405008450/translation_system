"""双语文件对齐 - 处理用户标注的原文/译文文件对"""

import re
import json
from typing import List, Tuple, Optional, Callable
from .schema import TermEntry, SentencePair
from .parser.base import Document


def align_bilingual_files(
    source_doc: Document,
    target_doc: Document,
    llm_helper=None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[List[TermEntry], List[SentencePair]]:
    """
    对齐两个标注好的原文/译文文件，提取术语和翻译记忆。
    
    策略：直接把全文交给大模型，让它自己切分和匹配
    """
    source_text = _clean_text(source_doc.raw_text)
    target_text = _clean_text(target_doc.raw_text)
    
    print(f"[ReferenceAnalyzer] 原文长度: {len(source_text)}, 译文长度: {len(target_text)}")
    
    if progress_callback:
        progress_callback(10, 100, "开始双语对齐...")
    
    pairs = []
    
    if llm_helper:
        # 直接让 LLM 处理全文对齐
        print(f"[ReferenceAnalyzer] 使用 LLM 全文对齐")
        pairs = _llm_full_text_align(source_text, target_text, llm_helper, progress_callback)
        print(f"[ReferenceAnalyzer] 对齐完成: {len(pairs)} 对")
    else:
        # 无 LLM，简单按段落顺序对齐
        source_paras = [p.strip() for p in source_text.split('\n') if p.strip()]
        target_paras = [p.strip() for p in target_text.split('\n') if p.strip()]
        min_len = min(len(source_paras), len(target_paras))
        pairs = list(zip(source_paras[:min_len], target_paras[:min_len]))
        print(f"[ReferenceAnalyzer] 顺序对齐完成: {len(pairs)} 对")
    
    if progress_callback:
        progress_callback(90, 100, f"对齐完成: {len(pairs)} 对")

    # 分类：术语 vs 翻译记忆
    terms = []
    tm = []
    seen_terms = set()
    seen_tm = set()
    
    for source, target in pairs:
        source = source.strip()
        target = target.strip()
        if not source or not target:
            continue
        
        # 修复：如果 target 只是编号（如 i. ii. I. II. 1.），这是无效配对，直接跳过
        # 因为无法确定它应该对应哪个完整的英文句子
        if len(target) < 15 and re.match(r'^[ivxIVX]+\.?\s*$|^\d+\.?\s*$', target.strip()):
            print(f"[ReferenceAnalyzer] 跳过无效配对（译文只有编号）: source='{source[:30]}' target='{target}'")
            continue  # 跳过纯编号配对
        
        pair_key = (source.lower(), target.lower())
        
        if _is_term_level(source, target):
            if pair_key not in seen_terms:
                terms.append(TermEntry(source=source, target=target))
                seen_terms.add(pair_key)
        else:
            if pair_key not in seen_tm:
                tm.append(SentencePair(source=source, target=target))
                seen_tm.add(pair_key)
    
    print(f"[ReferenceAnalyzer] 去重后 - 术语: {len(terms)} 条, TM: {len(tm)} 条")
    
    if progress_callback:
        progress_callback(100, 100, "完成")

    return terms, tm


def _llm_full_text_align(
    source_text: str,
    target_text: str,
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """让 LLM 直接处理全文，自动切分和对齐
    
    新策略：使用滑动窗口法确保完整覆盖所有内容
    """
    # 估算 token 数（中文约1字=1token，英文约4字=1token）
    source_tokens = len(source_text)
    target_tokens = len(target_text) // 3
    total_tokens = source_tokens + target_tokens
    
    print(f"[ReferenceAnalyzer] 原文 tokens: {source_tokens}, 译文 tokens: {target_tokens}")
    
    # 如果文本不太长，一次性处理（提高阈值到 10000）
    if total_tokens < 10000:
        if progress_callback:
            progress_callback(30, 100, "LLM 全文对齐中...")
        return _llm_align_full_text_once(source_text, target_text, llm_helper)
    
    # 文本太长，使用智能分块策略
    print(f"[ReferenceAnalyzer] 文本较长 ({total_tokens} tokens)，使用智能分块")
    return _llm_align_smart_chunked(source_text, target_text, llm_helper, progress_callback)


def _llm_align_full_text_once(
    source_text: str,
    target_text: str,
    llm_helper,
) -> List[Tuple[str, str]]:
    """一次性让 LLM 对齐全文 - 完整传递文本，不截断"""
    
    system = """你是文档对齐工具，只做复制粘贴，绝对禁止修改任何文字。

【你的唯一任务】
从给定的中文和英文文本中，找出对应的段落/句子，原样复制出来配对。

【铁律 - 违反即失败】
1. 中文：必须从【中文原文】中一字不差地复制
2. 英文：必须从【英文译文】中一字不差地复制
3. 禁止改写、禁止意译、禁止调整语序
4. 找不到就跳过，绝对不能自己编"""

    user = f"""从以下文本中找出中英对应的段落/句子，原样复制配对。

【中文原文】
{source_text}

【英文译文】
{target_text}

=== 重要规则 ===
1. 如果中文是标题/编号行（如"（二）简化统一授信额度架构。"），英文必须是完整的对应标题（如"ii. Simplify the structure of unified credit limits."），不能只配对编号"ii."
2. 英文中的编号如 "i." "ii." "1." "Article 1." 后面通常还有内容，必须一起复制，不能单独拆开
3. 按段落/语义单元配对，不要机械地按句号切分

=== 严禁以下行为 ===
❌ 把英文编号（如ii.）和后面的内容拆开
❌ 修改原文的任何文字
❌ 自己翻译或编造

=== 正确示例 ===
中文: "（二）简化统一授信额度架构。"
英文: "ii. Simplify the structure of unified credit limits." （完整复制，不是只有"ii."）

输出JSON数组：
[
  {{"zh": "从中文原文原样复制", "en": "从英文译文原样复制（编号和内容一起）"}},
  ...
]

只输出JSON："""

    try:
        print(f"[ReferenceAnalyzer] 调用LLM，原文长度: {len(source_text)}, 译文长度: {len(target_text)}")
        result = llm_helper._call(system, user, temperature=0.0)  # 降到0，减少随机性
        result = result.strip()
        print(f"[ReferenceAnalyzer] LLM返回长度: {len(result)}")
        
        # 清理 markdown
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        json_start = result.find('[')
        json_end = result.rfind(']')
        if json_start != -1 and json_end > json_start:
            result = result[json_start:json_end+1]
        
        mappings = json.loads(result)
        
        pairs = []
        for m in mappings:
            zh = m.get("zh", "").strip()
            en = m.get("en", "").strip()
            if zh and en:
                # 修复：如果英文只是编号（如 i. ii. I. II. 1.），从原文找完整句子
                if len(en) < 15 and re.match(r'^[ivxIVX]+\.?\s*$|^\d+\.?\s*$', en.strip()):
                    fixed_en = _find_full_sentence_by_number(en, target_text)
                    if fixed_en:
                        print(f"[ReferenceAnalyzer] 修复编号: '{en}' -> '{fixed_en[:50]}...'")
                        en = fixed_en
                
                # 验证中文是否真实存在于原文中
                verified_zh = _verify_text_exists(zh, source_text)
                # 验证英文是否真实存在于原文中
                verified_en = _verify_text_exists(en, target_text)
                
                if verified_zh and verified_en:
                    pairs.append((verified_zh, verified_en))
                else:
                    # 记录被过滤的内容
                    if not verified_zh:
                        print(f"[ReferenceAnalyzer] 过滤编造的中文: {zh[:50]}...")
                    if not verified_en:
                        print(f"[ReferenceAnalyzer] 过滤编造的英文: {en[:50]}...")
        
        # 后处理：检查是否有未拆分的长句，尝试拆分
        pairs = _post_split_long_pairs(pairs)
        
        print(f"[ReferenceAnalyzer] 全文对齐: {len(pairs)} 对")
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] 全文对齐失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def _llm_align_full_text_chunked(
    source_text: str,
    target_text: str,
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """分块处理长文本（旧方法，保留作为备用）"""
    # 调用新的智能分块方法
    return _llm_align_smart_chunked(source_text, target_text, llm_helper, progress_callback)


def _post_split_long_pairs(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """后处理：拆分 LLM 返回的长句对
    
    如果 LLM 把多个句子合并成一对，尝试按句号拆分
    """
    result = []
    
    for zh, en in pairs:
        # 检查中文是否包含多个句子（多个句号）
        zh_sentences = re.split(r'(?<=[。！？])', zh)
        zh_sentences = [s.strip() for s in zh_sentences if s.strip()]
        
        # 检查英文是否包含多个句子
        # 重要：先保护编号格式，避免把 "ii. Simplify..." 错误分割成 "ii." 和 "Simplify..."
        en_protected = en
        # 保护罗马数字编号 (i. ii. iii. iv. v. vi. vii. viii. ix. x. xi. xii. I. II. III. IV. V. 等)
        en_protected = re.sub(r'\b([ivxIVX]{1,4})\.\s+', r'\1<<DOT>> ', en_protected)
        # 保护阿拉伯数字编号 (1. 2. 10. 等)
        en_protected = re.sub(r'\b(\d+)\.\s+', r'\1<<DOT>> ', en_protected)
        # 保护 Article/Chapter/Section 等编号
        en_protected = re.sub(r'\b(Article|Chapter|Section|Clause|Item)\s+(\d+)\.\s+', r'\1 \2<<DOT>> ', en_protected, flags=re.IGNORECASE)
        # 保护常见缩写 (Mr. Mrs. Dr. etc.)
        abbrs = ['Mr', 'Mrs', 'Dr', 'Prof', 'Inc', 'Ltd', 'Co', 'Corp', 'etc', 'vs', 'No', 'Art', 'Sec']
        for abbr in abbrs:
            en_protected = re.sub(rf'\b{abbr}\.\s+', f'{abbr}<<DOT>> ', en_protected, flags=re.IGNORECASE)
        
        # 分割句子
        en_sentences = re.split(r'(?<=[.!?])\s+', en_protected)
        # 恢复被保护的点号
        en_sentences = [s.replace('<<DOT>>', '.').strip() for s in en_sentences if s.strip()]
        
        # 如果中英文句子数相同且大于1，拆分配对
        if len(zh_sentences) > 1 and len(zh_sentences) == len(en_sentences):
            print(f"[ReferenceAnalyzer] 拆分长句对: {len(zh_sentences)} 句")
            for z, e in zip(zh_sentences, en_sentences):
                result.append((z, e))
        elif len(zh_sentences) > 1 and len(en_sentences) > 1:
            # 数量不完全匹配，但都有多句，尝试按比例配对
            print(f"[ReferenceAnalyzer] 尝试按比例拆分: 中{len(zh_sentences)}句 vs 英{len(en_sentences)}句")
            min_len = min(len(zh_sentences), len(en_sentences))
            for i in range(min_len):
                result.append((zh_sentences[i], en_sentences[i]))
            # 剩余的合并到最后一对
            if len(zh_sentences) > min_len:
                remaining_zh = ''.join(zh_sentences[min_len:])
                if result:
                    last_zh, last_en = result[-1]
                    result[-1] = (last_zh + remaining_zh, last_en)
            if len(en_sentences) > min_len:
                remaining_en = ' '.join(en_sentences[min_len:])
                if result:
                    last_zh, last_en = result[-1]
                    result[-1] = (last_zh, last_en + ' ' + remaining_en)
        else:
            # 无法拆分，保持原样
            result.append((zh, en))
    
    return result


def _verify_text_exists(text: str, original: str) -> Optional[str]:
    """验证文本是否真实存在于原文中
    
    如果完全匹配返回原文中的版本，否则尝试模糊匹配，
    如果相似度太低（LLM编造的）返回None
    """
    if not text or not original:
        return None
    
    text = text.strip()
    
    # 1. 完全匹配（忽略空格差异）
    text_normalized = re.sub(r'\s+', ' ', text).strip()
    original_normalized = re.sub(r'\s+', ' ', original).strip()
    
    if text_normalized in original_normalized:
        # 完全匹配，直接返回原文本
        return text
    
    # 2. 去掉所有空格后检查是否包含
    text_no_space = re.sub(r'\s+', '', text)
    original_no_space = re.sub(r'\s+', '', original)
    
    if text_no_space in original_no_space:
        # 核心内容存在，只是空格差异，返回原文本
        return text
    
    # 3. 检查是否是轻微变体（允许少量标点差异）
    text_no_punct = re.sub(r'[，。、；：""''！？,.;:!?\s]+', '', text)
    original_no_punct = re.sub(r'[，。、；：""''！？,.;:!?\s]+', '', original)
    
    if len(text_no_punct) > 10 and text_no_punct in original_no_punct:
        # 核心内容存在，直接返回原文本（不要去分句查找，会破坏完整性）
        return text
    
    # 4. 计算相似度，如果太低就是编造的
    similarity = _calculate_similarity(text, original)
    if similarity < 0.5:
        # 相似度太低，判定为编造
        return None
    
    # 5. 相似度够高，返回原文本（信任 LLM 的输出）
    # 不再调用 _find_original_sentence，避免破坏完整句子
    return text


def _find_original_sentence(text: str, original: str) -> Optional[str]:
    """在原文中找到与给定文本最相似的句子"""
    # 判断语言
    chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    is_chinese = chinese_chars > len(text) * 0.3
    
    # 按句子分割原文
    if is_chinese:
        sentences = re.split(r'(?<=[。！？])', original)
    else:
        # 英文分句：先保护编号格式，避免在 "i." "ii." "1." "Article 1." 等后面错误分割
        protected = original
        # 保护罗马数字编号 (i. ii. iii. iv. v. vi. vii. viii. ix. x. xi. xii.)
        protected = re.sub(r'\b(i{1,3}|iv|vi{0,3}|ix|xi{0,2})\.\s+', r'\1<<DOT>> ', protected, flags=re.IGNORECASE)
        # 保护阿拉伯数字编号 (1. 2. 10. 等)
        protected = re.sub(r'\b(\d+)\.\s+', r'\1<<DOT>> ', protected)
        # 保护常见缩写
        abbrs = ['Mr.', 'Mrs.', 'Dr.', 'Prof.', 'Inc.', 'Ltd.', 'Co.', 'Corp.',
                 'etc.', 'e.g.', 'i.e.', 'vs.', 'No.', 'Art.', 'Sec.']
        for abbr in abbrs:
            protected = protected.replace(abbr, abbr.replace('.', '<<DOT>>'))
        
        sentences = re.split(r'(?<=[.!?])\s+', protected)
        # 恢复被保护的点号
        sentences = [s.replace('<<DOT>>', '.') for s in sentences]
    
    sentences = [s.strip() for s in sentences if s.strip()]
    
    # 找最相似的句子
    best_match = None
    best_score = 0
    
    text_chars = set(re.sub(r'\s+', '', text))
    
    for sent in sentences:
        sent_chars = set(re.sub(r'\s+', '', sent))
        
        # 计算字符级重叠度
        if not text_chars or not sent_chars:
            continue
        
        overlap = len(text_chars & sent_chars) / max(len(text_chars), len(sent_chars))
        
        if overlap > best_score and overlap > 0.7:
            best_score = overlap
            best_match = sent
    
    return best_match


def _find_full_sentence_by_number(number_only: str, original_text: str) -> Optional[str]:
    """当 LLM 只返回编号（如 i. ii. 1.）时，从原文找到以该编号开头的完整句子
    
    Args:
        number_only: 只有编号的文本，如 "i." "ii." "1."
        original_text: 原始英文全文
    
    Returns:
        以该编号开头的完整句子（到第一个句号结束），或 None
    """
    if not number_only or not original_text:
        return None
    
    number_only = number_only.strip().rstrip('.')
    
    # 先按换行分割文本，找到以该编号开头的行
    lines = original_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 检查是否以该编号开头（忽略大小写）
        line_lower = line.lower()
        number_lower = number_only.lower()
        if line_lower.startswith(f"{number_lower}. ") or line_lower.startswith(f"{number_lower}."):
            # 找到了以该编号开头的行
            # 只取到第一个句号（句子结束）
            # 但要跳过编号本身的点，找真正的句子结束点
            # 先跳过编号部分
            after_number = line[len(number_only)+1:].strip()  # +1 跳过编号后的点
            
            # 在剩余内容中找第一个句号
            end_match = re.search(r'[.!?]', after_number)
            if end_match:
                sentence = f"{number_only}. {after_number[:end_match.end()]}"
                return sentence.strip()
            else:
                # 没找到句号，返回整行
                result = f"{number_only}. {after_number}"
                return result.strip()
    
    # 如果按行找不到，尝试正则匹配
    # 匹配编号开头，到第一个句号结束
    pattern = rf'(?:^|\n|\s)({re.escape(number_only)}\.\s+[^.!?\n]*[.!?])'
    match = re.search(pattern, original_text, re.IGNORECASE | re.MULTILINE)
    if match:
        result = match.group(1).strip()
        return result
    
    return None


def _calculate_similarity(text1: str, text2: str) -> float:
    """计算两个文本的相似度（字符级）"""
    if not text1 or not text2:
        return 0.0
    
    # 简单的字符重叠计算
    chars1 = set(re.sub(r'\s+', '', text1))
    chars2 = set(re.sub(r'\s+', '', text2))
    
    if not chars1:
        return 0.0
    
    overlap = len(chars1 & chars2)
    return overlap / len(chars1)


def _verify_and_fix_english(llm_english: str, original_target_text: str) -> Optional[str]:
    """验证 LLM 返回的英文是否存在于原始译文中，如果有缺失则尝试修正
    
    Args:
        llm_english: LLM 返回的英文句子
        original_target_text: 原始的完整英文译文
        
    Returns:
        修正后的英文，如果完全不匹配则返回 None
    """
    if not llm_english or not original_target_text:
        return None
    
    # 标准化文本用于比较
    target_lower = original_target_text.lower()
    llm_lower = llm_english.lower().strip()
    
    # 1. 如果完全匹配，直接返回
    if llm_lower in target_lower:
        # 尝试从原文中找到原始大小写的版本
        idx = target_lower.find(llm_lower)
        if idx != -1:
            return original_target_text[idx:idx+len(llm_english)].strip()
        return llm_english
    
    # 2. 检查是否是缺失数字的情况（如 "Article Post-credit" 应该是 "Article 30 Post-credit"）
    # 常见的编号模式
    number_patterns = [
        (r'article\s+post', r'article\s+\d+\s+post'),  # Article XX Post
        (r'article\s+(\w)', r'article\s+\d+\s+\1'),     # Article XX <word>
        (r'chapter\s+(\w)', r'chapter\s+\d+\s+\1'),     # Chapter XX <word>
        (r'section\s+(\w)', r'section\s+\d+\s+\1'),     # Section XX <word>
        (r'clause\s+(\w)', r'clause\s+\d+\s+\1'),       # Clause XX <word>
        (r'item\s+(\w)', r'item\s+\d+\s+\1'),           # Item XX <word>
    ]
    
    for incomplete_pattern, complete_pattern in number_patterns:
        if re.search(incomplete_pattern, llm_lower, re.IGNORECASE):
            # 在原文中搜索完整的版本
            match = re.search(complete_pattern, target_lower, re.IGNORECASE)
            if match:
                # 尝试提取完整的句子
                start_idx = match.start()
                # 找到句子结束位置
                end_markers = ['. ', '.\n', '.\t']
                end_idx = len(original_target_text)
                for marker in end_markers:
                    pos = original_target_text.lower().find(marker, start_idx)
                    if pos != -1 and pos < end_idx:
                        end_idx = pos + 1
                
                # 提取完整句子
                full_sentence = original_target_text[start_idx:end_idx].strip()
                if full_sentence:
                    print(f"[ReferenceAnalyzer] 修正英文: '{llm_english[:50]}...' -> '{full_sentence[:50]}...'")
                    return full_sentence
    
    # 3. 尝试模糊匹配：去掉空格和标点后比较
    def normalize(s):
        return re.sub(r'[\s\d]+', '', s.lower())
    
    llm_normalized = normalize(llm_english)
    
    # 在原文中寻找相似的句子
    # 按句号分割原文
    target_sentences = re.split(r'(?<=[.!?])\s+', original_target_text)
    
    best_match = None
    best_score = 0
    
    for sent in target_sentences:
        sent = sent.strip()
        if not sent:
            continue
        
        sent_normalized = normalize(sent)
        
        # 计算相似度（简单的包含关系检查）
        if llm_normalized in sent_normalized or sent_normalized in llm_normalized:
            # 如果 LLM 版本是原文的子集或超集，可能是漏字了
            if len(sent) > len(llm_english) * 0.8:  # 原文更长，可能是正确的
                score = len(set(llm_normalized) & set(sent_normalized)) / max(len(llm_normalized), 1)
                if score > best_score:
                    best_score = score
                    best_match = sent
    
    if best_match and best_score > 0.7:
        print(f"[ReferenceAnalyzer] 模糊修正: '{llm_english[:40]}...' -> '{best_match[:40]}...'")
        return best_match
    
    # 4. 无法修正，返回原始内容
    return llm_english


def _llm_align_smart_chunked(
    source_text: str,
    target_text: str,
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """智能分块对齐 - 确保完整覆盖所有内容
    
    策略：
    1. 按自然段落边界分块，不在句子中间切断
    2. 每块保持上下文连续性（有重叠）
    3. 对齐后去重合并
    """
    # 按自然段落分割（双换行或单换行）
    source_paras = _split_into_natural_paragraphs(source_text)
    target_paras = _split_into_natural_paragraphs(target_text)
    
    print(f"[ReferenceAnalyzer] 智能分块: 原文 {len(source_paras)} 段, 译文 {len(target_paras)} 段")
    
    # 如果段落数很少，可能是格式问题，尝试按句号分割
    if len(source_paras) < 5:
        source_paras = _split_by_sentence_boundary(source_text)
        print(f"[ReferenceAnalyzer] 原文按句子重分: {len(source_paras)} 段")
    if len(target_paras) < 5:
        target_paras = _split_by_sentence_boundary(target_text)
        print(f"[ReferenceAnalyzer] 译文按句子重分: {len(target_paras)} 段")
    
    all_pairs = []
    seen_pairs = set()  # 用于去重
    
    # 计算分块参数
    chunk_size = 15  # 每次处理15个段落
    overlap = 3      # 块之间重叠3个段落，确保边界内容不丢失
    
    total_chunks = max(1, (len(source_paras) - overlap) // (chunk_size - overlap) + 1)
    
    # 估算原文/译文段落比例
    ratio = len(target_paras) / max(len(source_paras), 1)
    
    for chunk_idx in range(total_chunks):
        start = chunk_idx * (chunk_size - overlap)
        end = min(start + chunk_size, len(source_paras))
        
        if start >= len(source_paras):
            break
            
        chunk_sources = source_paras[start:end]
        
        if progress_callback:
            progress = 20 + int(60 * (chunk_idx + 1) / total_chunks)
            progress_callback(progress, 100, f"对齐块 {chunk_idx + 1}/{total_chunks}")
        
        # 计算对应的译文范围（扩大范围以确保找到匹配）
        tgt_start = max(0, int(start * ratio) - 5)
        tgt_end = min(len(target_paras), int(end * ratio) + 10)
        chunk_targets = target_paras[tgt_start:tgt_end]
        
        if not chunk_sources or not chunk_targets:
            continue
        
        # 合并段落为文本块
        source_block = "\n".join(chunk_sources)
        target_block = "\n".join(chunk_targets)
        
        # 调用 LLM 对齐这一块
        chunk_pairs = _llm_align_single_chunk(source_block, target_block, llm_helper, chunk_idx + 1, total_chunks)
        
        # 去重后添加
        for pair in chunk_pairs:
            pair_key = (pair[0].strip()[:100], pair[1].strip()[:100])  # 用前100字符作为key
            if pair_key not in seen_pairs:
                all_pairs.append(pair)
                seen_pairs.add(pair_key)
        
        print(f"[ReferenceAnalyzer] 块 {chunk_idx + 1}: {len(chunk_pairs)} 对, 累计: {len(all_pairs)} 对")
    
    if progress_callback:
        progress_callback(90, 100, f"对齐完成: {len(all_pairs)} 对")
    
    print(f"[ReferenceAnalyzer] 智能分块对齐完成: {len(all_pairs)} 对")
    return all_pairs


def _split_into_natural_paragraphs(text: str) -> List[str]:
    """按自然段落边界分割文本"""
    if not text:
        return []
    
    # 先尝试按双换行分割
    paras = re.split(r'\n\s*\n', text)
    if len(paras) > 3:
        return [p.strip() for p in paras if p.strip()]
    
    # 如果段落太少，按单换行分割
    paras = text.split('\n')
    return [p.strip() for p in paras if p.strip()]


def _split_by_sentence_boundary(text: str) -> List[str]:
    """按句子边界分割文本（用于段落分割失败的情况）"""
    if not text:
        return []
    
    # 判断是否主要是中文
    chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    is_chinese = chinese_chars > len(text) * 0.3
    
    if is_chinese:
        # 中文按"。"分割
        parts = re.split(r'(?<=[。！？])', text)
    else:
        # 英文按". "分割
        parts = re.split(r'(?<=[.!?])\s+', text)
    
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


def _llm_align_single_chunk(
    source_block: str,
    target_block: str,
    llm_helper,
    chunk_num: int,
    total_chunks: int,
) -> List[Tuple[str, str]]:
    """对齐单个文本块"""
    
    system = """你是文档对齐工具，只做复制粘贴。
禁止修改任何文字，中文和英文都必须从原文原样复制。
英文编号如 i. ii. 1. 等必须和后面的内容一起复制，不能拆开。"""

    user = f"""从以下文本中找出对应的中英段落/句子，原样复制配对（第{chunk_num}/{total_chunks}块）。

【中文】
{source_block}

【英文】
{target_block}

规则：
1. 中文必须从上面【中文】原样复制
2. 英文必须从上面【英文】原样复制
3. 英文编号（如 ii. 1. Article 1.）必须和后面的内容一起复制，不能只复制编号
4. 一个字都不能改
5. 找不到就跳过

输出JSON：[{{"zh": "原样复制", "en": "原样复制（编号+内容完整）"}}, ...]

只输出JSON："""

    try:
        result = llm_helper._call(system, user, temperature=0.0)
        result = result.strip()
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        json_start = result.find('[')
        json_end = result.rfind(']')
        if json_start != -1 and json_end > json_start:
            result = result[json_start:json_end+1]
        
        mappings = json.loads(result)
        
        pairs = []
        for m in mappings:
            zh = m.get("zh", "").strip()
            en = m.get("en", "").strip()
            if zh and en:
                # 修复：如果英文只是编号（如 i. ii. I. II. 1.），从原文找完整句子
                en_lower = en.lower().strip()
                if len(en) < 15 and re.match(r'^[ivxIVX]+\.?\s*$|^\d+\.?\s*$', en.strip()):
                    print(f"[ReferenceAnalyzer] 块{chunk_num} 检测到纯编号 en='{en}'，尝试修复...")
                    fixed_en = _find_full_sentence_by_number(en, target_block)
                    if fixed_en:
                        print(f"[ReferenceAnalyzer] 修复成功: '{en}' -> '{fixed_en[:50]}...'")
                        en = fixed_en
                
                # 验证中英文都真实存在
                verified_zh = _verify_text_exists(zh, source_block)
                verified_en = _verify_text_exists(en, target_block)
                
                if verified_zh and verified_en:
                    pairs.append((verified_zh, verified_en))
        
        # 后处理拆分
        pairs = _post_split_long_pairs(pairs)
        
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] 块 {chunk_num} 对齐失败: {e}")
        return []


def _llm_match_sentences(
    source_sents: List[str],
    target_sents: List[str],
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """用 LLM 做句子级语义匹配
    
    参考 文件参考分析 项目的实现：
    1. LLM 直接返回匹配的译文内容（不是索引）
    2. 支持一行译文拆分成多个匹配
    3. 数量少时一次性处理，多时分批
    """
    
    # 如果数量不多，一次性让LLM处理
    if len(source_sents) <= 80 and len(target_sents) <= 80:
        if progress_callback:
            progress_callback(30, 100, "LLM 对齐中...")
        return _llm_align_all(source_sents, target_sents, llm_helper)
    
    # 数量太多，分批处理
    all_pairs = []
    batch_size = 15
    total_batches = (len(source_sents) + batch_size - 1) // batch_size
    
    print(f"[ReferenceAnalyzer] 分批处理: {len(source_sents)} 原文, {total_batches} 批")
    
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(source_sents))
        batch_sources = source_sents[start:end]
        
        if progress_callback:
            progress = 20 + int(60 * (batch_idx + 1) / total_batches)
            progress_callback(progress, 100, f"对齐批次 {batch_idx + 1}/{total_batches}")
        
        # 计算对应的译文范围
        ratio = len(target_sents) / len(source_sents) if source_sents else 1
        tgt_start = max(0, int(start * ratio) - 10)
        tgt_end = min(len(target_sents), int(end * ratio) + 10)
        
        candidate_targets = [(j, target_sents[j]) for j in range(tgt_start, tgt_end)]
        
        if not candidate_targets:
            continue
        
        batch_pairs = _llm_align_batch(batch_sources, candidate_targets, llm_helper)
        all_pairs.extend(batch_pairs)
        
        print(f"[ReferenceAnalyzer] 批次{batch_idx + 1}: {len(batch_pairs)} 对")
    
    print(f"[ReferenceAnalyzer] 句子匹配完成: {len(all_pairs)} 对")
    return all_pairs


def _llm_align_all(
    source_sents: List[str], 
    target_sents: List[str], 
    llm_helper
) -> List[Tuple[str, str]]:
    """一次性让LLM对齐所有内容"""
    
    # 构建原文列表（截断过长的）
    sources_text = "\n".join([f"{i+1}. {s[:200]}" for i, s in enumerate(source_sents)])
    targets_text = "\n".join([f"{i+1}. {t[:200]}" for i, t in enumerate(target_sents)])
    
    system = "你是翻译对齐专家。"
    user = f"""将原文与译文逐条配对。

原文（共{len(source_sents)}条）：
{sources_text}

译文（共{len(target_sents)}条）：
{targets_text}

任务：为每条原文找到对应的译文。

重要规则：
1. 如果一行译文包含多个字段（如 "Signature: ___ Date: ___"），需要拆分，只提取对应部分
2. 每条原文必须找到对应翻译
3. 直接返回匹配的译文内容

输出JSON数组：[{{"s": 1, "t": "对应的译文内容"}}, {{"s": 2, "t": "对应的译文内容"}}, ...]
s是原文序号，t是对应的译文内容（只包含匹配的部分）。
只输出JSON："""

    try:
        result = llm_helper._call(system, user, temperature=0.1)
        result = result.strip()
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        json_start = result.find('[')
        json_end = result.rfind(']')
        if json_start != -1 and json_end > json_start:
            result = result[json_start:json_end+1]
        
        mappings = json.loads(result)
        
        pairs = []
        for m in mappings:
            s_idx = m.get("s", 0) - 1
            t_text = m.get("t", "")
            
            if 0 <= s_idx < len(source_sents) and t_text:
                pairs.append((source_sents[s_idx], t_text.strip()))
        
        print(f"[ReferenceAnalyzer] 一次性对齐: {len(pairs)} 对")
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] LLM对齐失败: {e}")
        # 回退到顺序对齐
        min_len = min(len(source_sents), len(target_sents))
        return list(zip(source_sents[:min_len], target_sents[:min_len]))


def _llm_align_batch(
    sources: List[str],
    candidate_targets: List[Tuple[int, str]],
    llm_helper
) -> List[Tuple[str, str]]:
    """LLM 对齐一批"""
    
    sources_text = "\n".join([f"S{i+1}: {s[:200]}" for i, s in enumerate(sources)])
    targets_text = "\n".join([f"T{idx+1}: {t[:200]}" for idx, t in candidate_targets[:50]])
    
    system = "你是翻译对齐专家。"
    user = f"""配对原文和译文。

原文：
{sources_text}

译文：
{targets_text}

规则：
1. 为每条原文找到对应的译文
2. 如果一行译文包含多个字段，只提取对应部分
3. 直接返回译文内容

输出JSON：[{{"s": 1, "t": "对应的译文内容"}}, ...]
s是原文序号，t是匹配的译文内容。
只输出JSON："""

    try:
        result = llm_helper._call(system, user, temperature=0.1)
        result = result.strip()
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        json_start = result.find('[')
        json_end = result.rfind(']')
        if json_start != -1 and json_end > json_start:
            result = result[json_start:json_end+1]
        
        mappings = json.loads(result)
        
        pairs = []
        for m in mappings:
            s_idx = m.get("s", 0) - 1
            t_text = m.get("t", "")
            
            if 0 <= s_idx < len(sources) and t_text:
                pairs.append((sources[s_idx], t_text.strip()))
        
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] LLM对齐批次失败: {e}")
        return []


def _split_all_sentences(paragraphs: List[str]) -> List[str]:
    """对所有段落进行分句"""
    sentences = []
    for para in paragraphs:
        sents = _smart_split_sentences(para)
        sentences.extend(sents)
    return sentences


def _smart_split_sentences(text: str) -> List[str]:
    """智能分句 - 按句号分割，保留编号"""
    if not text or not text.strip():
        return []
    
    text = text.strip()
    
    # 判断语言
    chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    is_chinese = chinese_chars > len(text) * 0.3
    
    # 先提取编号前缀（如果有）
    number_prefix = ""
    number_match = re.match(
        r'^(第[一二三四五六七八九十\d]+[章条节款项]\s*|'
        r'[一二三四五六七八九十]+[、.]\s*|'
        r'\d+[\.、]\s*|'
        r'[（\(][一二三四五六七八九十\d]+[）\)]\s*|'
        r'Chapter\s+[IVX\d]+\s*|'
        r'Article\s+\d+\s*|'
        r'Section\s+\d+\s*)',
        text,
        re.IGNORECASE
    )
    if number_match:
        number_prefix = number_match.group(0)
        text = text[len(number_prefix):]
    
    # 按句号分割
    if is_chinese:
        # 中文按"。"分割
        parts = re.split(r'(?<=[。])', text)
    else:
        # 英文：先保护编号和缩写
        protected = text
        # 保护罗马数字编号 (i. ii. iii. iv. v. vi. vii. viii. ix. x. xi. xii.)
        protected = re.sub(r'\b(i{1,3}|iv|vi{0,3}|ix|xi{0,2})\.\s+', r'\1<<DOT>> ', protected, flags=re.IGNORECASE)
        # 保护阿拉伯数字编号 (1. 2. 10. 等)
        protected = re.sub(r'\b(\d+)\.\s+', r'\1<<DOT>> ', protected)
        # 保护常见缩写
        abbrs = ['Mr.', 'Mrs.', 'Dr.', 'Prof.', 'Inc.', 'Ltd.', 'Co.', 'Corp.',
                 'etc.', 'e.g.', 'i.e.', 'vs.', 'No.', 'Art.', 'Sec.']
        for abbr in abbrs:
            protected = protected.replace(abbr, abbr.replace('.', '<<DOT>>'))
        
        parts = re.split(r'(?<=\.)\s+', protected)
        parts = [p.replace('<<DOT>>', '.') for p in parts]
    
    # 组装结果
    sentences = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        
        # 第一个句子加上编号前缀
        if i == 0 and number_prefix:
            part = number_prefix + part
        
        sentences.append(part)
    
    # 如果没有分割成功，返回原文
    if not sentences:
        if number_prefix:
            return [number_prefix + text]
        return [text] if text else []
    
    return sentences


def _align_sentences(source_sents: List[str], target_sents: List[str]) -> List[Tuple[str, str]]:
    """对齐句子 - 先按编号匹配，剩余按顺序"""
    
    # 提取编号
    def get_number(text: str) -> Optional[str]:
        """提取标准化编号"""
        cn_map = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
                  '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
        
        patterns = [
            (r'^第([一二三四五六七八九十]+)[章条节]', lambda m: f"c{cn_map.get(m.group(1), m.group(1))}"),
            (r'^第(\d+)[章条节]', lambda m: f"c{m.group(1)}"),
            (r'^[Cc]hapter\s*([IVX\d]+)', lambda m: f"c{m.group(1)}"),
            (r'^[Aa]rticle\s*(\d+)', lambda m: f"a{m.group(1)}"),
            (r'^(\d+)\.', lambda m: m.group(1)),
            (r'^[（\(]([一二三四五六七八九十\d]+)[）\)]', 
             lambda m: cn_map.get(m.group(1), m.group(1))),
            (r'^([一二三四五六七八九十]+)[、]', 
             lambda m: cn_map.get(m.group(1), m.group(1))),
        ]
        
        for pattern, extractor in patterns:
            match = re.match(pattern, text.strip(), re.IGNORECASE)
            if match:
                return extractor(match)
        return None
    
    # 按编号分组
    source_by_num = {}
    source_no_num = []
    for i, s in enumerate(source_sents):
        num = get_number(s)
        if num:
            if num not in source_by_num:
                source_by_num[num] = []
            source_by_num[num].append((i, s))
        else:
            source_no_num.append((i, s))
    
    target_by_num = {}
    target_no_num = []
    for i, t in enumerate(target_sents):
        num = get_number(t)
        if num:
            if num not in target_by_num:
                target_by_num[num] = []
            target_by_num[num].append((i, t))
        else:
            target_no_num.append((i, t))
    
    pairs = []
    used_target = set()
    
    # 1. 按编号匹配
    for num, src_list in source_by_num.items():
        if num in target_by_num:
            tgt_list = target_by_num[num]
            for (si, s), (ti, t) in zip(src_list, tgt_list):
                if ti not in used_target:
                    pairs.append((s, t))
                    used_target.add(ti)
    
    # 2. 无编号的按顺序匹配
    tgt_no_num_unused = [(i, t) for i, t in target_no_num if i not in used_target]
    for (si, s), (ti, t) in zip(source_no_num, tgt_no_num_unused):
        pairs.append((s, t))
        used_target.add(ti)
    
    # 按原文顺序排序
    return pairs


def _llm_segment_and_align(
    source_text: str,
    target_text: str,
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    _is_chunk: bool = False,  # 标记是否是分块调用，防止递归
) -> List[Tuple[str, str]]:
    """让大模型同时完成分句和对齐，确保一句对一句"""
    
    # 如果文本太长且不是分块调用，分块处理
    if not _is_chunk and (len(source_text) > 8000 or len(target_text) > 8000):
        return _llm_segment_and_align_chunked(source_text, target_text, llm_helper, progress_callback)
    
    if progress_callback:
        progress_callback(30, 100, "正在分句对齐...")
    
    # 如果是分块调用但文本还是太长，截断
    max_len = 10000
    if len(source_text) > max_len:
        source_text = source_text[:max_len]
    if len(target_text) > max_len:
        target_text = target_text[:max_len]
    
    system = "你是专业的翻译对齐专家。"
    user = f"""请将以下原文和译文按句子对齐。

【原文】
{source_text}

【译文】
{target_text}

【分句规则】
1. 中文按"。"分句，英文按"."分句
2. "第一章"、"Chapter I"、"Article 1"等标题单独成句
3. 每个句子必须完整，不要在逗号处断开

【对齐规则】
1. 每个原文句子对应一个译文句子
2. 原文和译文数量必须相同
3. 按顺序一一对应

【输出格式】JSON数组：
[
  {{"source": "第一章 总则", "target": "Chapter I General Provisions"}},
  {{"source": "第一条 为持续提升...", "target": "Article 1 These Measures..."}}
]

只输出JSON："""

    try:
        print(f"[ReferenceAnalyzer] 调用LLM进行分句对齐，原文长度: {len(source_text)}, 译文长度: {len(target_text)}")
        result = llm_helper._call(system, user, temperature=0.1)
        result = result.strip()
        print(f"[ReferenceAnalyzer] LLM返回长度: {len(result)}")
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
            result = result.strip()
        
        # 尝试找到 JSON 数组的边界
        start_idx = result.find('[')
        end_idx = result.rfind(']')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            result = result[start_idx:end_idx+1]
        
        pairs_data = json.loads(result)
        
        pairs = []
        for item in pairs_data:
            source = item.get("source", "").strip()
            target = item.get("target", "").strip()
            if source and target:
                pairs.append((source, target))
        
        print(f"[ReferenceAnalyzer] 分句对齐成功: {len(pairs)} 对")
        
        if progress_callback:
            progress_callback(100, 100, f"对齐完成: {len(pairs)} 对")
        
        return pairs
        
    except json.JSONDecodeError as e:
        print(f"[ReferenceAnalyzer] JSON解析失败: {e}")
        print(f"[ReferenceAnalyzer] 原始返回: {result[:500]}...")
        return []
    except Exception as e:
        print(f"[ReferenceAnalyzer] LLM分句对齐失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def _llm_segment_and_align_chunked(
    source_text: str,
    target_text: str,
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """分块处理长文本"""
    
    # 按段落分块
    source_paras = [p.strip() for p in source_text.split('\n\n') if p.strip()]
    target_paras = [p.strip() for p in target_text.split('\n\n') if p.strip()]
    
    # 如果段落太少，按换行分
    if len(source_paras) < 3:
        source_paras = [p.strip() for p in source_text.split('\n') if p.strip()]
    if len(target_paras) < 3:
        target_paras = [p.strip() for p in target_text.split('\n') if p.strip()]
    
    all_pairs = []
    chunk_size = 10  # 每次处理10个段落
    total_chunks = max(len(source_paras), len(target_paras)) // chunk_size + 1
    
    for i in range(0, max(len(source_paras), len(target_paras)), chunk_size):
        chunk_idx = i // chunk_size + 1
        if progress_callback:
            progress = int(chunk_idx / total_chunks * 100)
            progress_callback(progress, 100, f"处理第 {chunk_idx}/{total_chunks} 块")
        
        src_chunk = "\n\n".join(source_paras[i:i+chunk_size])
        tgt_chunk = "\n\n".join(target_paras[i:i+chunk_size])
        
        if not src_chunk or not tgt_chunk:
            continue
        
        chunk_pairs = _llm_segment_and_align(src_chunk, tgt_chunk, llm_helper, None, _is_chunk=True)
        all_pairs.extend(chunk_pairs)
    
    if progress_callback:
        progress_callback(100, 100, f"对齐完成: {len(all_pairs)} 对")
    
    return all_pairs


def _extract_paragraphs_from_doc(doc: Document, preserve_structure: bool = True) -> List[str]:
    """从文档中提取段落列表
    
    Args:
        doc: 文档对象
        preserve_structure: 是否保持原始段落结构（用于双语对齐时保持一致性）
    """
    paragraphs = []
    
    # 先从 paragraphs 获取
    raw_paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    
    # 如果 paragraphs 为空，回退到 raw_text
    if not raw_paras:
        text = _clean_text(doc.raw_text)
        raw_paras = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    
    if preserve_structure:
        # 保持原始段落结构，只做最小化的清理
        # 这样中英文文档的段落数保持一致
        for para in raw_paras:
            para = para.strip()
            if para:
                paragraphs.append(para)
    else:
        # 合并过短的连续段落（仅用于非双语对齐场景）
        merged_paras = _merge_short_paragraphs(raw_paras)
        for para in merged_paras:
            para = para.strip()
            if para:
                paragraphs.append(para)
    
    return paragraphs


def _merge_short_paragraphs(paragraphs: List[str], min_length: int = 30) -> List[str]:
    """合并过短的连续段落
    
    解决中英文文档段落边界不一致的问题。
    关键改进：更积极地合并，减少段落数量差异
    """
    if not paragraphs:
        return []
    
    # 第一遍：把单独的编号与下一个段落合并
    pre_merged = []
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i].strip()
        if not para:
            i += 1
            continue
        
        # 判断是否是单独的编号（更宽松的匹配）
        is_just_number = bool(re.match(
            r'^[IVXivx]+\.?$|'                          # 罗马数字
            r'^\d+\.?$|'                                 # 纯数字
            r'^[a-z]\.?$|'                               # 单字母
            r'^（[一二三四五六七八九十]+）$|'             # 中文括号数字
            r'^[一二三四五六七八九十]+[、.]?$|'          # 中文数字
            r'^第[一二三四五六七八九十\d]+[条章节款项]?$|'  # 第X条
            r'^[Aa]rticle\s*\d+\.?$|'                   # Article X
            r'^[Cc]hapter\s*\d+\.?$|'                   # Chapter X
            r'^[Ss]ection\s*\d+\.?$',                   # Section X
            para
        ))
        
        # 非常短的段落也视为需要合并（可能是标题或子编号）
        is_very_short = len(para) < 15 and not any(c in para for c in '。.!?！？')
        
        if (is_just_number or is_very_short) and i + 1 < len(paragraphs):
            # 与下一个段落合并
            next_para = paragraphs[i + 1].strip()
            pre_merged.append(para + " " + next_para)
            i += 2
        else:
            pre_merged.append(para)
            i += 1
    
    # 第二遍：合并其他短段落（更积极地合并）
    merged = []
    current = ""
    
    for para in pre_merged:
        para = para.strip()
        if not para:
            continue
        
        # 判断当前段落是否有编号
        current_has_number = _extract_number(para) is not None
        
        # 判断是否需要与前一个合并
        should_merge = False
        if current:
            # 如果当前段落没有编号，且前一个没有结束，倾向于合并
            current_not_complete = not current.endswith(('。', '！', '？', '；', '.', '!', '?', ';'))
            para_is_short = len(para) < min_length
            para_starts_lowercase = para[0].islower() if para else False
            para_starts_with_continuation = para.startswith(('and ', 'or ', 'but ', '，', ',', '、'))
            para_no_number = not current_has_number
            
            should_merge = (
                (current_not_complete and para_is_short and para_no_number) or
                para_starts_lowercase or
                para_starts_with_continuation
            )
        
        if not current:
            current = para
        elif should_merge:
            current = current + " " + para
        else:
            merged.append(current)
            current = para
    
    if current:
        merged.append(current)
    
    return merged


def _split_paragraph_into_sentences(text: str) -> List[str]:
    """将段落按句子分割，专门处理中英文混合文本"""
    if not text or len(text) <= 150:
        return [text] if text else []
    
    sentences = []
    
    # 判断是否主要是中文
    chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
    is_chinese = chinese_chars > len(text) * 0.3
    
    if is_chinese:
        # 中文：按中文句号分割
        parts = re.split(r'(?<=[。！？；])', text)
    else:
        # 英文：按句号+空格分割，避免在缩写和编号处错误分割
        protected = text
        # 保护罗马数字编号 (i. ii. iii. iv. v. vi. vii. viii. ix. x. xi. xii.)
        protected = re.sub(r'\b(i{1,3}|iv|vi{0,3}|ix|xi{0,2})\.\s+', r'\1<DOT> ', protected, flags=re.IGNORECASE)
        # 保护阿拉伯数字编号 (1. 2. 10. 等)
        protected = re.sub(r'\b(\d+)\.\s+', r'\1<DOT> ', protected)
        # 保护常见缩写
        abbreviations = ['Mr.', 'Mrs.', 'Dr.', 'Prof.', 'Inc.', 'Ltd.', 'Co.', 'Corp.', 
                        'etc.', 'e.g.', 'i.e.', 'vs.', 'P.R.C.', 'U.S.', 'U.K.']
        for abbr in abbreviations:
            protected = protected.replace(abbr, abbr.replace('.', '<DOT>'))
        
        # 按句号+空格分割
        parts = re.split(r'(?<=[.!?])\s+', protected)
        
        # 恢复被保护的点
        parts = [p.replace('<DOT>', '.') for p in parts]
    
    for part in parts:
        part = part.strip()
        if part and len(part) >= 2:
            sentences.append(part)
    
    # 如果分割后只有一个，说明没有合适的分割点，直接返回
    if len(sentences) <= 1:
        return [text]
    
    return sentences


def _extract_number(text: str) -> Optional[str]:
    """提取段落开头的编号，如 '3.', '4.1', '（一）', '第一条' 等
    
    返回标准化的编号（统一为阿拉伯数字），便于中英文匹配
    """
    text = text.strip()
    
    # 中文数字到阿拉伯数字的映射
    cn_num_map = {
        '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
        '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
        '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
        '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20',
    }
    
    # 匹配常见编号格式，返回标准化结果
    patterns = [
        # 阿拉伯数字: 1. 1.1 1.2.3 (1) 1)
        (r'^(\d+(?:\.\d+)*)[\.、\s\)]', lambda m: m.group(1)),
        (r'^[（\(](\d+)[）\)]', lambda m: m.group(1)),
        
        # 中文括号数字: （一）（二）
        (r'^[（\(]([一二三四五六七八九十]+)[）\)]', 
         lambda m: cn_num_map.get(m.group(1), m.group(1))),
        
        # 第X条/章/节: 第一条 第二章 Article 1
        (r'^第([一二三四五六七八九十]+)[条章节款项]', 
         lambda m: cn_num_map.get(m.group(1), m.group(1))),
        (r'^[Aa]rticle\s*(\d+)', lambda m: m.group(1)),
        (r'^[Cc]hapter\s*(\d+)', lambda m: m.group(1)),
        (r'^[Ss]ection\s*(\d+)', lambda m: m.group(1)),
        (r'^[Cc]lause\s*(\d+)', lambda m: m.group(1)),
        (r'^[Ii]tem\s*(\d+)', lambda m: m.group(1)),
        
        # 中文顿号: 一、二、
        (r'^([一二三四五六七八九十]+)[、\.\s]', 
         lambda m: cn_num_map.get(m.group(1), m.group(1))),
        
        # 字母编号: a. b) c
        (r'^([a-zA-Z])[\.、\)\s]', lambda m: m.group(1).lower()),
    ]
    
    for pattern, extractor in patterns:
        match = re.match(pattern, text)
        if match:
            return extractor(match)
    
    return None


def _align_by_number(source_sents: List[str], target_sents: List[str], llm_helper=None) -> List[Tuple[str, str]]:
    """按编号对齐，返回配对结果。如果编号匹配率低则返回空列表
    
    改进：编号标准化后匹配，支持中英文编号互相匹配
    """
    # 提取编号和对应段落（保留所有，不去重）
    source_with_num = [(i, _extract_number(s), s) for i, s in enumerate(source_sents)]
    target_with_num = [(i, _extract_number(t), t) for i, t in enumerate(target_sents)]
    
    # 分离有编号和无编号的段落
    source_numbered = [(i, num, s) for i, num, s in source_with_num if num]
    target_numbered = [(i, num, t) for i, num, t in target_with_num if num]
    source_no_num = [(i, s) for i, num, s in source_with_num if not num]
    target_no_num = [(i, t) for i, num, t in target_with_num if not num]
    
    print(f"[ReferenceAnalyzer] 编号统计 - 原文有编号: {len(source_numbered)}, 译文有编号: {len(target_numbered)}")
    print(f"[ReferenceAnalyzer] 无编号统计 - 原文: {len(source_no_num)}, 译文: {len(target_no_num)}")
    
    if not source_numbered or not target_numbered:
        return []
    
    # 构建译文编号索引（同一编号可能有多个）
    target_by_num = {}
    for i, num, t in target_numbered:
        if num not in target_by_num:
            target_by_num[num] = []
        target_by_num[num].append((i, t))
    
    # 按编号配对
    pairs = []
    matched_count = 0
    used_target_indices = set()
    
    for src_i, num, src_text in source_numbered:
        if num in target_by_num:
            # 找到编号匹配的译文（取第一个未使用的）
            for tgt_i, tgt_text in target_by_num[num]:
                if tgt_i not in used_target_indices:
                    pairs.append((src_text, tgt_text))
                    used_target_indices.add(tgt_i)
                    matched_count += 1
                    break
    
    # 如果编号匹配率太低，返回空让外层用其他方式
    match_rate = matched_count / len(source_numbered) if source_numbered else 0
    if match_rate < 0.3:
        print(f"[ReferenceAnalyzer] 编号匹配率过低: {match_rate:.1%}")
        return []
    
    print(f"[ReferenceAnalyzer] 编号对齐: {matched_count}/{len(source_numbered)} 匹配 ({match_rate:.0%})")
    
    # 处理没有编号的段落（按顺序对齐）
    if source_no_num and target_no_num:
        # 按原始顺序排序
        source_no_num_sorted = sorted(source_no_num, key=lambda x: x[0])
        target_no_num_sorted = sorted(target_no_num, key=lambda x: x[0])
        
        min_len = min(len(source_no_num_sorted), len(target_no_num_sorted))
        for j in range(min_len):
            pairs.append((source_no_num_sorted[j][1], target_no_num_sorted[j][1]))
        
        print(f"[ReferenceAnalyzer] 无编号段落顺序对齐: {min_len} 对")
    
    return pairs


def _llm_align(
    source_sents: List[str], 
    target_sents: List[str], 
    llm_helper,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[Tuple[str, str]]:
    """使用 LLM 进行语义对齐 - 一次性处理全部内容
    
    Args:
        progress_callback: 进度回调函数 (current, total, message)
    """
    total_sents = len(source_sents)
    
    # 如果数量不多，一次性让LLM处理
    if len(source_sents) <= 100 and len(target_sents) <= 100:
        if progress_callback:
            progress_callback(0, total_sents, "正在进行语义对齐...")
        result = _llm_align_all(source_sents, target_sents, llm_helper)
        if progress_callback:
            progress_callback(total_sents, total_sents, f"对齐完成: {len(result)} 对")
        return result
    
    # 数量太多，分批处理
    all_pairs = []
    batch_size = 15
    total_batches = (len(source_sents) + batch_size - 1) // batch_size
    
    for batch_idx, i in enumerate(range(0, len(source_sents), batch_size)):
        batch_sources = source_sents[i:i+batch_size]
        
        # 更新进度
        current_count = min(i + batch_size, len(source_sents))
        if progress_callback:
            progress_callback(
                current_count, 
                total_sents, 
                f"对齐批次 {batch_idx + 1}/{total_batches}"
            )
        
        ratio = len(target_sents) / len(source_sents) if source_sents else 1
        start_idx = max(0, int(i * ratio) - 10)
        end_idx = min(len(target_sents), int((i + batch_size) * ratio) + 10)
        
        candidate_targets = [(j, target_sents[j]) for j in range(start_idx, end_idx)]
        
        if not candidate_targets:
            continue
            
        batch_pairs = _llm_align_batch_simple(batch_sources, candidate_targets, llm_helper)
        all_pairs.extend(batch_pairs)
        
        print(f"[ReferenceAnalyzer] LLM对齐进度: {current_count}/{len(source_sents)}")
    
    if progress_callback:
        progress_callback(total_sents, total_sents, f"对齐完成: {len(all_pairs)} 对")
    
    print(f"[ReferenceAnalyzer] LLM对齐完成: {len(all_pairs)} 对")
    return all_pairs


def _llm_align_all(
    source_sents: List[str], 
    target_sents: List[str], 
    llm_helper
) -> List[Tuple[str, str]]:
    """一次性让LLM对齐所有内容，支持多对一合并"""
    
    sources_text = "\n".join([f"S{i+1}: {s}" for i, s in enumerate(source_sents)])
    targets_text = "\n".join([f"T{i+1}: {t}" for i, t in enumerate(target_sents)])
    
    system = "你是专业的翻译对齐专家。你的任务是将原文句子与对应的译文句子配对。"
    user = f"""请将原文与译文进行配对。

【原文列表】（共{len(source_sents)}条）：
{sources_text}

【译文列表】（共{len(target_sents)}条）：
{targets_text}

【配对规则】：
1. 找出每条原文对应的完整译文
2. 如果一条原文对应多条译文（译文被拆分了），用数组表示，如 "t": [1, 2]
3. 如果多条原文对应一条译文（原文被拆分了），每条原文单独列出，指向同一译文
4. 找不到对应的跳过，不要强行匹配错误的内容
5. 注意：编号如"四、"/"IV."应该和后面的内容在一起

【输出格式】：JSON数组
[{{"s": 原文序号, "t": 译文序号或序号数组}}, ...]

例如：
- 一对一：{{"s": 1, "t": 1}}
- 一对多：{{"s": 1, "t": [1, 2]}}（原文1对应译文1和2合并）

只输出JSON："""

    try:
        result = llm_helper._call(system, user, temperature=0.1)
        result = result.strip()
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        mappings = json.loads(result)
        
        pairs = []
        used_t_indices = set()
        
        for m in mappings:
            s_idx = m.get("s", 0) - 1
            t_val = m.get("t")
            
            if not (0 <= s_idx < len(source_sents)):
                continue
                
            source_text = source_sents[s_idx]
            
            # 处理译文（可能是单个序号或数组）
            if isinstance(t_val, list):
                # 多条译文合并
                target_parts = []
                for t_idx in t_val:
                    if isinstance(t_idx, int):
                        idx = t_idx - 1
                        if 0 <= idx < len(target_sents) and idx not in used_t_indices:
                            target_parts.append(target_sents[idx])
                            used_t_indices.add(idx)
                if target_parts:
                    target_text = " ".join(target_parts)
                    pairs.append((source_text, target_text))
            elif isinstance(t_val, int):
                # 单条译文
                t_idx = t_val - 1
                if 0 <= t_idx < len(target_sents) and t_idx not in used_t_indices:
                    pairs.append((source_text, target_sents[t_idx]))
                    used_t_indices.add(t_idx)
        
        print(f"[ReferenceAnalyzer] LLM对齐: {len(pairs)}/{len(source_sents)} 对")
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] LLM对齐失败: {e}")
        min_len = min(len(source_sents), len(target_sents))
        return list(zip(source_sents[:min_len], target_sents[:min_len]))


def _llm_align_batch_simple(
    sources: List[str],
    candidate_targets: List[Tuple[int, str]],
    llm_helper
) -> List[Tuple[str, str]]:
    """LLM 对齐一批，支持多对一合并"""
    
    sources_text = "\n".join([f"S{i+1}: {s[:300]}" for i, s in enumerate(sources)])
    targets_text = "\n".join([f"T{idx}: {t[:300]}" for idx, t in candidate_targets[:50]])
    
    # 构建完整译文映射
    target_map = {idx: t for idx, t in candidate_targets}
    
    system = "你是专业的翻译对齐专家。"
    user = f"""将原文与译文进行配对。

原文：
{sources_text}

译文：
{targets_text}

配对规则：
1. 找出每条原文对应的完整译文
2. 如果一条原文对应多条译文，用数组表示：{{"s": 1, "t": [T序号1, T序号2]}}
3. 找不到对应的跳过
4. 注意语义完整性，不要把不完整的译文配对

输出格式：[{{"s": S序号, "t": T序号或数组}}, ...]
只输出JSON："""

    try:
        result = llm_helper._call(system, user, temperature=0.1)
        result = result.strip()
        
        if "```" in result:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
        
        mappings = json.loads(result)
        
        pairs = []
        used_t_indices = set()
        
        for m in mappings:
            s_idx = m.get("s", 0) - 1
            t_val = m.get("t")
            
            if not (0 <= s_idx < len(sources)):
                continue
            
            source_text = sources[s_idx]
            
            if isinstance(t_val, list):
                # 多条译文合并
                target_parts = []
                for t_idx in t_val:
                    if t_idx in target_map and t_idx not in used_t_indices:
                        target_parts.append(target_map[t_idx])
                        used_t_indices.add(t_idx)
                if target_parts:
                    pairs.append((source_text, " ".join(target_parts)))
            elif t_val in target_map and t_val not in used_t_indices:
                pairs.append((source_text, target_map[t_val]))
                used_t_indices.add(t_val)
        
        return pairs
        
    except Exception as e:
        print(f"[ReferenceAnalyzer] LLM对齐批次失败: {e}")
        return []


def _clean_text(text: str) -> str:
    """清理文本"""
    # 去掉页码标记
    text = re.sub(r'[—\-]\s*\d+\s*[—\-]', '\n', text)
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    return text


def _split_by_sentence(text: str) -> List[str]:
    """按句子分割文本
    
    将文本按句子级别分割，支持中英文标点：
    - 中文：。！？；
    - 英文：. ! ? ;
    """
    if not text or not text.strip():
        return []
    
    # 先按换行分割成段落
    parts = re.split(r'\n+', text)
    result = []
    
    for p in parts:
        p = p.strip()
        if not p or len(p) < 2:
            continue
        
        # 按句子分割每个段落
        sentences = _split_text_into_sentences(p)
        result.extend(sentences)
    
    return result


def _split_text_into_sentences(text: str) -> List[str]:
    """将单个文本块按句子分割"""
    if not text or not text.strip():
        return []
    
    text = text.strip()
    
    # 如果文本很短且没有句子结束标点，直接返回
    if len(text) < 30 and not any(c in text for c in '。！？；.!?;'):
        return [text] if text else []
    
    # 按句子分割
    # 中文句号、感叹号、问号、分号后直接分割
    # 英文句号、感叹号、问号后需要跟空格或结束
    pattern = r'(?<=[。！？；])|(?<=[.!?;])(?=\s|$)'
    parts = re.split(pattern, text)
    
    sentences = []
    current_sentence = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # 如果当前部分以句子结束标点结尾，它是一个完整句子
        if part and part[-1] in '。！？；.!?;':
            if current_sentence:
                sentences.append(current_sentence + " " + part)
                current_sentence = ""
            else:
                sentences.append(part)
        else:
            # 否则累积到当前句子
            if current_sentence:
                current_sentence += " " + part
            else:
                current_sentence = part
    
    # 处理最后一个句子（可能没有标点结尾）
    if current_sentence:
        sentences.append(current_sentence)
    
    # 过滤并对过长的句子进行二次分割
    result = []
    for s in sentences:
        s = s.strip()
        if not s or len(s) < 2:
            continue
        # 如果句子太长（超过300字符），尝试按逗号分割
        if len(s) > 300:
            sub_parts = _split_long_sentence(s)
            result.extend(sub_parts)
        else:
            result.append(s)
    
    return result


def _split_long_sentence(text: str) -> List[str]:
    """对过长的句子进行二次分割"""
    # 尝试按中文逗号、顿号或英文逗号分割
    parts = re.split(r'[，、,]', text)
    
    result = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        if not current:
            current = part
        elif len(current) + len(part) < 200:
            # 合并短片段
            current = current + "，" + part
        else:
            # 当前片段够长了，保存并开始新片段
            result.append(current)
            current = part
    
    if current:
        result.append(current)
    
    return result if result else [text]


def _split_by_paragraph(text: str) -> List[str]:
    """按段落分割，同时支持按句子分割长段落（参考项目实现）"""
    # 先按换行分割
    parts = re.split(r'\n+', text)
    result = []
    
    for p in parts:
        p = p.strip()
        if not p or len(p) < 2:
            continue
        
        # 如果段落太长（超过500字符），尝试按句子分割
        if len(p) > 500:
            # 按句子分割（中英文句号、问号、感叹号）
            sentences = re.split(r'(?<=[。！？.!?])\s*', p)
            for sent in sentences:
                sent = sent.strip()
                if sent and len(sent) >= 2:
                    result.append(sent)
        else:
            result.append(p)
    
    return result


def _is_term_level(source: str, target: str) -> bool:
    """判断是否为术语级别
    
    术语标准（更严格）：
    1. 原文非常短（中文<=8字符，英文<=4词）
    2. 不包含句子结束标点
    3. 不包含编号前缀（如"一、"、"1."、"①"等）
    4. 不是标题格式
    """
    source_stripped = source.strip()
    target_stripped = target.strip()
    
    # 空内容不是术语
    if not source_stripped or not target_stripped:
        return False
    
    # 包含句子结束标点，是句子不是术语
    sentence_end_marks = '.。!！?？;；'
    if any(mark in source_stripped for mark in sentence_end_marks):
        return False
    
    # 包含编号前缀的不是术语（是标题或条目）
    numbering_patterns = [
        r'^[一二三四五六七八九十]+[、.]',       # 一、二、
        r'^[（\(][一二三四五六七八九十\d]+[）\)]',  # （一）(1)
        r'^\d+[\.、]',                          # 1. 2、
        r'^[①②③④⑤⑥⑦⑧⑨⑩]',                  # 圈数字
        r'^第[一二三四五六七八九十\d]+[条章节款项]',  # 第一条
        r'^[IVX]+\.',                           # I. II.
        r'^[a-zA-Z][\.）\)]',                   # a. b)
        r'^Article\s*\d+',                      # Article 1
        r'^Chapter\s*\d+',                      # Chapter 1
        r'^Section\s*\d+',                      # Section 1
    ]
    for pattern in numbering_patterns:
        if re.match(pattern, source_stripped, re.IGNORECASE):
            return False
    
    # 判断是否主要是中文
    chinese_chars = len([c for c in source_stripped if '\u4e00' <= c <= '\u9fff'])
    is_mostly_chinese = chinese_chars > len(source_stripped) * 0.3
    
    if is_mostly_chinese:
        # 中文：非常短才是术语（<=8字符）
        return len(source_stripped) <= 8 and chinese_chars <= 6
    else:
        # 英文：按单词数判断（<=3词）
        word_count = len(source_stripped.split())
        return word_count <= 3 and len(source_stripped) <= 30
