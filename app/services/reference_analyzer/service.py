"""参考分析服务 - 主入口，供 API 路由调用"""

from typing import List, Optional, Tuple
from dataclasses import asdict

from .schema import TranslationProfile, FileType, TermEntry, SentencePair
from .parser.factory import parse_file
from .parser.base import Document, Paragraph
from .classifier import classify_document
from .extractor import extract_profile
from .aligner import align_bilingual_files
from .analyzer import DeepAnalyzer
from .llm_helper import ReferenceLLMHelper
from .progress import ProgressReporter


def get_reference_llm_helper(settings) -> Optional[ReferenceLLMHelper]:
    """从配置创建参考分析专用的 LLM Helper"""
    api_key = getattr(settings, 'reference_llm_api_key', None)
    if not api_key:
        # 没有配置专用 key，尝试回退到 openrouter
        api_key = settings.openrouter_api_key
    
    if not api_key:
        return None
    
    model = getattr(settings, 'reference_llm_model', None) or 'google/gemini-2.0-flash-001'
    base_url = getattr(settings, 'reference_llm_base_url', None) or 'https://openrouter.ai/api/v1'
    
    return ReferenceLLMHelper(api_key=api_key, model=model, base_url=base_url)


def analyze_reference_files(
    file_paths: List[str],
    bilingual_pairs: Optional[List[Tuple[str, str]]] = None,
    llm_helper: Optional[ReferenceLLMHelper] = None,
    enable_deep_analysis: bool = True,
    progress_reporter: Optional[ProgressReporter] = None,
) -> TranslationProfile:
    """
    分析参考文件，生成翻译规格。

    Args:
        file_paths: 单独的参考文件路径列表（术语表、格式规范等）
        bilingual_pairs: 用户标注的双语文件对 [(原文路径, 译文路径), ...]
        llm_helper: 参考分析专用 LLM Helper
        enable_deep_analysis: 是否启用深度分析
        progress_reporter: 进度报告器

    Returns:
        TranslationProfile: 合并后的翻译规格
    """
    profile = TranslationProfile()
    
    if progress_reporter:
        progress_reporter.start()  # 显示 0%

    # 计算总文件数
    total_files = len(file_paths) + (len(bilingual_pairs) * 2 if bilingual_pairs else 0)
    file_index = 0
    
    if progress_reporter and total_files > 0:
        progress_reporter.init_progress(f"准备解析 {total_files} 个文件...")

    # 处理单独的参考文件
    for path in file_paths:
        file_index += 1
        filename = path.split("/")[-1].split("\\")[-1]
        
        if progress_reporter:
            progress_reporter.parsing_files(file_index, total_files, filename)
        
        doc = parse_file(path)

        # 规则分类
        file_type = classify_document(doc)

        # 规则判断不了时，用LLM兜底
        if file_type == FileType.UNKNOWN and llm_helper:
            file_type = llm_helper.classify_document(doc.raw_text)

        sub_profile = extract_profile(doc, file_type)
        profile = _merge_profiles(profile, sub_profile)

    # 处理双语对照文件对
    if bilingual_pairs:
        pair_index = 0
        total_pairs = len(bilingual_pairs)
        
        for source_path, target_path in bilingual_pairs:
            pair_index += 1
            source_filename = source_path.split("/")[-1].split("\\")[-1]
            target_filename = target_path.split("/")[-1].split("\\")[-1]
            
            # 解析原文
            file_index += 1
            if progress_reporter:
                progress_reporter.parsing_files(file_index, total_files, source_filename)
            
            # 如果是 PDF 且有 LLM，用 LLM 提取文本
            if source_path.lower().endswith('.pdf') and llm_helper:
                print(f"[ReferenceAnalyzer] 使用LLM提取PDF文本: {source_path}")
                source_text = llm_helper.extract_pdf_text(source_path)
                source_doc = Document(
                    paragraphs=[Paragraph(text=p) for p in source_text.split('\n') if p.strip()],
                    tables=[],
                    filename=source_filename,
                    raw_text=source_text,
                )
            else:
                source_doc = parse_file(source_path)
            
            # 解析译文
            file_index += 1
            if progress_reporter:
                progress_reporter.parsing_files(file_index, total_files, target_filename)
            
            if target_path.lower().endswith('.pdf') and llm_helper:
                print(f"[ReferenceAnalyzer] 使用LLM提取PDF文本: {target_path}")
                target_text = llm_helper.extract_pdf_text(target_path)
                target_doc = Document(
                    paragraphs=[Paragraph(text=p) for p in target_text.split('\n') if p.strip()],
                    tables=[],
                    filename=target_filename,
                    raw_text=target_text,
                )
            else:
                target_doc = parse_file(target_path)
            
            print(f"[ReferenceAnalyzer] 原文文件: {source_doc.filename}, 段落数: {len(source_doc.paragraphs)}")
            print(f"[ReferenceAnalyzer] 译文文件: {target_doc.filename}, 段落数: {len(target_doc.paragraphs)}")
            
            # 对齐阶段
            if progress_reporter:
                progress_reporter.aligning(pair_index, total_pairs, f"{source_filename} ↔ {target_filename}")
            
            # 创建进度回调函数，用于 LLM 对齐的细粒度进度
            def make_align_callback(reporter: ProgressReporter, pair_idx: int, total_p: int, detail_str: str):
                """创建对齐进度回调闭包"""
                def callback(current: int, total: int, message: str):
                    # 计算对齐阶段内的进度
                    # 对齐阶段范围为 20%-60%，共 40% 的权重
                    base = 20  # 对齐阶段起始进度
                    weight = 40  # 对齐阶段权重
                    
                    # 当前文件对的基础进度（0-1）
                    pair_base = (pair_idx - 1) / max(total_p, 1)
                    pair_weight = 1 / max(total_p, 1)
                    
                    # 当前批次的进度（0-1）
                    batch_progress = current / max(total, 1)
                    
                    # 总进度 = 基础 + 权重 * (文件对进度 + 批次进度 * 单文件权重)
                    overall = base + weight * (pair_base + batch_progress * pair_weight)
                    
                    from .progress import set_progress, AnalysisStage
                    set_progress(
                        reporter.task_id,
                        AnalysisStage.ALIGNING,
                        overall,
                        message,
                        f"{detail_str} ({current}/{total})",
                    )
                return callback
            
            align_callback = None
            if progress_reporter:
                align_callback = make_align_callback(
                    progress_reporter, 
                    pair_index, 
                    total_pairs, 
                    f"{source_filename} ↔ {target_filename}"
                )
            
            terms, tm = align_bilingual_files(source_doc, target_doc, llm_helper, align_callback)
            
            print(f"[ReferenceAnalyzer] 提取术语: {len(terms)} 条, 翻译记忆: {len(tm)} 条")
            
            if progress_reporter:
                progress_reporter.extracting(
                    f"提取完成: {len(terms)} 术语, {len(tm)} 翻译记忆",
                    f"{source_filename} ↔ {target_filename}"
                )
            
            profile.constraints.terminology.extend(terms)
            profile.references.translation_memory.extend(tm)
            profile.source_files.extend([source_doc.filename, target_doc.filename])

            # 用LLM分析双语句对的风格
            if llm_helper and tm:
                pairs_data = [{"source": p.source, "target": p.target} for p in tm]
                analyzed_style = llm_helper.analyze_style_from_pairs(pairs_data)
                if analyzed_style and (analyzed_style.tone or analyzed_style.preferences):
                    profile.references.style = analyzed_style

    # 深度分析
    if enable_deep_analysis:
        if progress_reporter:
            progress_reporter.deep_analysis("正在进行深度分析...")
        
        analyzer = DeepAnalyzer(llm_helper)
        profile.analysis = analyzer.analyze(profile)
        if profile.analysis:
            print(f"[ReferenceAnalyzer] 深度分析完成: 行业={profile.analysis.industry.value}, 策略={profile.analysis.strategy.value}")
    
    # 完成
    if progress_reporter:
        progress_reporter.complete(
            len(profile.constraints.terminology),
            len(profile.references.translation_memory)
        )

    return profile


def match_segments_with_reference(
    segments: List[dict],
    profile: TranslationProfile,
    exact_threshold: float = 1.0,
    fuzzy_threshold: float = 0.6,
) -> dict:
    """
    将当前任务的句段与参考文件进行匹配。
    
    返回:
        {
            "exact_matches": [{"segment_id": ..., "source": ..., "target": ..., "source_file": ...}, ...],
            "fuzzy_matches": [{"segment_id": ..., "source": ..., "target": ..., "similarity": ..., "source_file": ...}, ...],
            "term_matches": [{"segment_id": ..., "terms": [...], "source_file": ...}, ...],
        }
    """
    from .similarity import SimpleMatcher
    
    exact_matches = []
    fuzzy_matches = []
    term_matches = []
    
    # 获取参考文件来源（用于显示）
    source_files = profile.source_files or []
    source_file_label = ", ".join(source_files) if source_files else "参考文件"
    
    # 构建参考 TM 查找表（精确匹配用），同时记录每个原文对应的来源
    tm_lookup = {}
    for pair in profile.references.translation_memory:
        normalized = pair.source.strip().lower()
        if normalized not in tm_lookup:
            tm_lookup[normalized] = {
                "target": pair.target,
                "source_file": source_file_label,
            }
    
    # 术语列表
    terms = profile.constraints.terminology
    
    # 简单匹配器（模糊匹配用）
    matcher = SimpleMatcher()
    
    for seg in segments:
        seg_id = seg.get('sentence_id') or seg.get('id')
        source_text = seg.get('source_text', '').strip()
        if not source_text:
            continue
        
        normalized_source = source_text.lower()
        
        # 1. 精确匹配
        if normalized_source in tm_lookup:
            match_info = tm_lookup[normalized_source]
            exact_matches.append({
                "segment_id": seg_id,
                "source": source_text,
                "target": match_info["target"],
                "match_type": "reference-exact",
                "similarity": 1.0,
                "source_file": match_info["source_file"],
            })
            continue
        
        # 2. 模糊匹配
        similar_pairs = matcher.find_similar_pairs(
            source_text,
            profile.references.translation_memory,
            top_k=3,
            threshold=fuzzy_threshold,
        )
        if similar_pairs:
            for pair in similar_pairs:
                fuzzy_matches.append({
                    "segment_id": seg_id,
                    "source": source_text,
                    "matched_source": pair.source,
                    "target": pair.target,
                    "similarity": pair.similarity,
                    "match_type": "reference-fuzzy",
                    "source_file": source_file_label,
                })
        
        # 3. 术语匹配
        matched_terms = []
        seen_terms = set()  # 用于去重
        source_lower = source_text.lower()
        for term in terms:
            term_key = (term.source.lower(), term.target)  # 使用原文+译文作为唯一标识
            if term_key in seen_terms:
                continue  # 跳过重复术语
            if term.source.lower() in source_lower:
                seen_terms.add(term_key)
                matched_terms.append({
                    "source": term.source,
                    "target": term.target,
                    "category": term.category,
                    "source_file": source_file_label,
                })
        if matched_terms:
            term_matches.append({
                "segment_id": seg_id,
                "terms": matched_terms,
                "source_file": source_file_label,
            })
    
    # 打印匹配结果摘要
    print(f"\n[ReferenceAnalyzer] ========== 匹配结果摘要 ==========")
    print(f"[ReferenceAnalyzer] 精确匹配: {len(exact_matches)} 条")
    if exact_matches:
        for i, m in enumerate(exact_matches[:5], 1):
            print(f"  [{i}] 原文: {m['source'][:50]}{'...' if len(m['source']) > 50 else ''}")
            print(f"      译文: {m['target'][:50]}{'...' if len(m['target']) > 50 else ''}")
            print(f"      来源: {m['source_file']}")
        if len(exact_matches) > 5:
            print(f"  ... 还有 {len(exact_matches) - 5} 条精确匹配")
    
    print(f"\n[ReferenceAnalyzer] 模糊匹配: {len(fuzzy_matches)} 条")
    if fuzzy_matches:
        for i, m in enumerate(fuzzy_matches[:5], 1):
            print(f"  [{i}] 原文: {m['source'][:50]}{'...' if len(m['source']) > 50 else ''}")
            print(f"      匹配: {m['matched_source'][:50]}{'...' if len(m['matched_source']) > 50 else ''}")
            print(f"      相似度: {m['similarity']:.1%}")
            print(f"      来源: {m['source_file']}")
        if len(fuzzy_matches) > 5:
            print(f"  ... 还有 {len(fuzzy_matches) - 5} 条模糊匹配")
    
    print(f"\n[ReferenceAnalyzer] 术语匹配: {len(term_matches)} 条")
    if term_matches:
        for i, m in enumerate(term_matches[:5], 1):
            terms_str = ", ".join([f"{t['source']}→{t['target']}" for t in m['terms'][:3]])
            if len(m['terms']) > 3:
                terms_str += f" 等{len(m['terms'])}个"
            print(f"  [{i}] 句段ID: {m['segment_id']}")
            print(f"      术语: {terms_str}")
            print(f"      来源: {m['source_file']}")
        if len(term_matches) > 5:
            print(f"  ... 还有 {len(term_matches) - 5} 条术语匹配")
    
    print(f"[ReferenceAnalyzer] =====================================\n")
    
    return {
        "exact_matches": exact_matches,
        "fuzzy_matches": fuzzy_matches,
        "term_matches": term_matches,
    }


def profile_to_dict(profile: TranslationProfile) -> dict:
    """将 TranslationProfile 转为可 JSON 序列化的字典"""
    from enum import Enum
    
    def convert(obj):
        if isinstance(obj, Enum):
            return obj.value
        elif isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert(item) for item in obj]
        elif hasattr(obj, '__dataclass_fields__'):
            return {k: convert(v) for k, v in asdict(obj).items()}
        else:
            return obj
    
    return convert(asdict(profile))


def _merge_profiles(base: TranslationProfile, new: TranslationProfile) -> TranslationProfile:
    """合并两个翻译规格"""
    base.constraints.terminology.extend(new.constraints.terminology)
    base.constraints.forbidden_words.extend(new.constraints.forbidden_words)
    base.source_files.extend(new.source_files)

    if new.references.style:
        base.references.style = new.references.style

    base.references.translation_memory.extend(new.references.translation_memory)

    return base
