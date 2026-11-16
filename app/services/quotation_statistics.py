"""翻译报价使用实际解析句段统计。"""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from app.services.quotation_counter import count_text, COUNT_POLICY_VERSION

PARSER_VERSION = "translation-segments-v1"

def segment_text(segment):
    get = segment.get if isinstance(segment, dict) else lambda key, default=None: getattr(segment, key, default)
    text = str(get("source_text", "") or "")
    placeholders = get("math_placeholders", {}) or {}
    metadata = get("segment_metadata", {}) or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    if not isinstance(metadata, dict):
        metadata = {}
    text = str(metadata.get("quotation_text", text))
    for placeholder in placeholders:
        text = text.replace(placeholder, "")
    for placeholder in metadata.get("quotation_excluded_placeholders", []):
        text = text.replace(placeholder, "")
    return text

def segment_metrics(segment):
    metrics = count_text(segment_text(segment))
    get = segment.get if isinstance(segment, dict) else lambda key, default=None: getattr(segment, key, default)
    metadata = get("segment_metadata", {}) or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if isinstance(metadata, dict) and metadata.get("quotation_semantic_text") is not None:
        semantic = dict(segment_metadata={**metadata, "quotation_text":metadata["quotation_semantic_text"]})
        metrics = replace(metrics, word_count=count_text(segment_text(semantic)).word_count)
    return metrics

def statistics_from_segments(segments):
    segments = list(segments)
    texts = [segment_text(segment) for segment in segments]
    metrics = [segment_metrics(segment) for segment in segments]
    return {
        "source": "parsed_text", "engine": "translation-parser",
        "engine_version": COUNT_POLICY_VERSION, "statistics_profile": COUNT_POLICY_VERSION,
        "content_scope": "parser_translatable_text", "statistics_scope": "standard",
        "count_policy_version": COUNT_POLICY_VERSION, "parser_version": PARSER_VERSION,
        "text_snapshot_sha256": hashlib.sha256(json.dumps({"texts": texts, "words": [m.word_count for m in metrics], "policy": COUNT_POLICY_VERSION}, ensure_ascii=False).encode()).hexdigest(),
        "statistics_warnings": [], "words": sum(m.word_count for m in metrics),
        "characters": sum(m.non_space_chars for m in metrics),
        "characters_with_spaces": sum(m.raw_chars for m in metrics),
        "paragraphs": len(texts), "lines": sum(len(t.splitlines()) for t in texts),
        "asian_characters": sum(m.han_count + m.kana_count + m.hangul_count for m in metrics),
        "non_asian_words": sum(m.word_count-m.han_count-m.kana_count-m.hangul_count-m.cjk_punct_count for m in metrics),
    }

def parse_quotation(raw_bytes, filename, options=None, source_language=None):
    from app.services.document_workspace import normalize_document_parse_options, parse_docx_workspace
    from app.services.adapters import ensure_default_adapters_registered
    normalized = normalize_document_parse_options(options)
    normalized["source_language"] = source_language or ""
    suffix = Path(filename).suffix.lower()
    converted_from = None
    if suffix == ".doc":
        from app.services.libreoffice_service import convert_word_to_docx
        raw_bytes = convert_word_to_docx(raw_bytes, filename)
        converted_from, filename, suffix = filename, Path(filename).stem+".docx", ".docx"
    if suffix == ".docx":
        segments = parse_docx_workspace(raw_bytes, document_parse_options=normalized)["segments"]
    else:
        result = ensure_default_adapters_registered().get_adapter(filename).parse_with_options(raw_bytes, filename=filename, options=normalized)
        segments = [dict(sentence_id=s.segment_id, source_text=s.source_text, display_text=s.display_text,
                         segment_metadata=s.metadata or {}, block_path=s.block_path) for s in result.segments]
    if not segments:
        raise ValueError("未解析出可翻译文本；如为扫描文档，请先完成 OCR。")
    units = []
    for index, segment in enumerate(segments):
        text = segment_text(segment)
        units.append(dict(id=segment.get("sentence_id") or str(index), text=text,
                          metadata=segment.get("segment_metadata") or {}, metrics=asdict(segment_metrics(segment))))
    return dict(filename=filename, converted_from=converted_from, parse_options=normalized,
                parser_version=PARSER_VERSION, count_policy_version=COUNT_POLICY_VERSION,
                statistics=statistics_from_segments(segments), units=units)
