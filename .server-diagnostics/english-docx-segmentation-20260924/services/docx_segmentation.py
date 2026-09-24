"""版本化的英文 Word 逻辑段落恢复，仅修改内存中的 XML 副本。"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from inspect import signature
import json
import logging
import os
import re
from xml.etree import ElementTree as ET

PROFILE = ContextVar('docx_segmentation_profile', default='legacy')
LOCATIONS = ContextVar('docx_segmentation_locations', default=None)
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
CONTINUATIONS = frozenset('a an the of to for with without by from in on at into and or nor than as be been being is are was were shall must should may can could will would please'.split())
logger = logging.getLogger(__name__)


def validate_profile(value):
    value = value or 'legacy'
    if value not in {'legacy', 'en_docx_v1'}:
        raise ValueError('不支持的英文 Word 断句规则版本。')
    return value


def new_import_options(options, filename, source_language):
    """仅在新导入入口调用；读取旧文件时不得推断或升级规则。"""
    result = json.loads(options) if isinstance(options, str) and options else dict(options or {})
    if 'segmentation_profile' not in result:
        enabled = os.getenv('EN_DOCX_SEGMENTATION_DEFAULT', 'en_docx_v1')
        result['segmentation_profile'] = validate_profile(enabled) if (
            filename.lower().endswith('.docx') and (source_language or '').lower().split('-')[0] == 'en'
        ) else 'legacy'
    return result


def segmentation_scope(function):
    """为解析和导出建立请求隔离的规则上下文，嵌套调用结束后恢复。"""
    sig = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        options = sig.bind(*args, **kwargs).arguments.get('document_parse_options') or {}
        if isinstance(options, str):
            options = json.loads(options)
        token = PROFILE.set(validate_profile(options.get('segmentation_profile')))
        locations_token = LOCATIONS.set({})
        try:
            return function(*args, **kwargs)
        finally:
            LOCATIONS.reset(locations_token)
            PROFILE.reset(token)
    return wrapped


def paragraph_text(paragraph):
    return ''.join(n.text or '' if n.tag == W + 't' else '\n' for n in paragraph.iter() if n.tag in {W+'t', W+'br', W+'cr'})


def _value(p, path, attr='val', default=''):
    node = p.find(path)
    return node.get(W + attr, default) if node is not None else default


def _unsafe(p):
    forbidden = {'drawing', 'pict', 'object', 'fldChar', 'fldSimple', 'instrText', 'ins', 'del', 'moveFrom', 'moveTo', 'sectPr', 'pageBreakBefore', 'outlineLvl', 'tab', 'hyperlink', 'footnoteReference', 'endnoteReference'}
    for n in p.iter():
        name = n.tag.rsplit('}', 1)[-1]
        if name in forbidden or name.startswith('oMath'):
            return True
        if name == 'br' and n.get(W+'type', 'textWrapping') != 'textWrapping':
            return True
    style = _value(p, f'{W}pPr/{W}pStyle').lower()
    return any(x in style for x in ('heading', 'title', 'caption', 'toc'))


def _fonts(p):
    return {_value(r, f'{W}rPr/{W}sz') for r in p.findall(W+'r') if paragraph_text(r).strip()}


def _indent(p):
    return int(_value(p, f'{W}pPr/{W}ind', 'left', '0'))


def _reason(first, following, current_text):
    nxt = paragraph_text(following).strip()
    current = current_text.rstrip()
    if not current or not nxt or _unsafe(first) or _unsafe(following):
        return None
    if current[-1] in '.!?;:…' or not re.match(r'[a-z]', nxt):
        return None
    if following.find(f'{W}pPr/{W}numPr') is not None:
        return None
    if re.match(r'(?:[•●▪◦–—-]|\d+[.)]|[A-Za-z][.)])\s', nxt):
        return None
    if _value(first, f'{W}pPr/{W}pStyle') != _value(following, f'{W}pPr/{W}pStyle'):
        return None
    if _fonts(first) != _fonts(following) or abs(_indent(first)-_indent(following)) > 180:
        return None
    word = re.search(r'([A-Za-z]+)$', current)
    if word and word.group(1).lower() in CONTINUATIONS:
        return 'unfinished_english_phrase'
    if len(current.split()) >= 6 and len(nxt.split()) <= 3 and re.search(r'[.!?][\"\')]*$', nxt):
        return 'short_lowercase_sentence_tail'
    return None


def prepare_package(package):
    """保留段落节点及序号，将续段的 run 移至首段，原字节不变。"""
    if PROFILE.get() != 'en_docx_v1':
        return
    root = package.read_xml('word/document.xml')
    if root is None:
        return
    locations = LOCATIONS.get()
    ids = {id(p): i for i, p in enumerate(root.iter(W+'p'))}
    repaired = 0
    candidates = 0
    body = root.find(W+'body')
    if body is None:
        return
    # 仅直接正文与表格单元格，不进入文本框或修订包装节点。
    def cells(container):
        for table in container.findall(W+'tbl'):
            for row in table.findall(W+'tr'):
                for cell in row.findall(W+'tc'):
                    yield cell
                    yield from cells(cell)
    containers = [body, *cells(body)]
    styles_root = package.read_xml('word/styles.xml')
    styles = {} if styles_root is None else {s.get(W+'styleId'): s for s in styles_root.findall(W+'style')}
    def inherited_boundary(p, include_numbering):
        style_id = _value(p, f'{W}pPr/{W}pStyle')
        seen = set()
        while style_id in styles and style_id not in seen:
            seen.add(style_id)
            style = styles[style_id]
            if style.find(f'{W}pPr/{W}outlineLvl') is not None or (include_numbering and style.find(f'{W}pPr/{W}numPr') is not None):
                return True
            style_id = _value(style, W+'basedOn')
        return False
    for container in containers:
        previous = None
        count = 0
        for p in list(container):
            if p.tag != W+'p':
                previous = None
                continue
            raw = paragraph_text(p)
            own = {'part': 'word/document.xml', 'paragraph_index': ids[id(p)], 'start': 0, 'end': len(raw), 'logical_start': 0, 'logical_end': len(raw)}
            locations[id(p)] = {'sources': [own], 'reasons': []}
            current = paragraph_text(previous) if previous is not None else ''
            reason = _reason(previous, p, current) if previous is not None else None
            if inherited_boundary(p, True) or (previous is not None and inherited_boundary(previous, False)):
                reason = None
            if previous is not None and current.strip() and raw.strip():
                candidates += 1
            if reason and count < 3 and len(current)+len(raw)+1 <= 2000:
                separator = '' if current[-1:].isspace() or raw[:1].isspace() else ' '
                if separator:
                    r = ET.SubElement(previous, W+'r')
                    t = ET.SubElement(r, W+'t', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
                    t.text = separator
                start = len(current)+len(separator)
                for child in list(p):
                    if child.tag != W+'pPr':
                        p.remove(child)
                        previous.append(child)
                own.update(logical_start=start, logical_end=start+len(raw))
                locations[id(previous)]['sources'].append(own)
                locations[id(previous)]['reasons'].append(reason)
                count += 1
                repaired += 1
            else:
                previous = p if raw.strip() and not _unsafe(p) else None
                count = 1
    logger.info('English DOCX segmentation: profile=en_docx_v1 repaired=%s skipped=%s', repaired, candidates-repaired)


def annotate_segments(paragraph, segments):
    info = (LOCATIONS.get() or {}).get(id(paragraph))
    if PROFILE.get() != 'en_docx_v1' or not info:
        return
    raw = paragraph_text(paragraph)
    cursor = 0
    for segment in segments:
        value = segment['display_text']
        start = raw.find(value, cursor)
        if start < 0:
            continue
        end = start+len(value)
        sources = []
        for source in info['sources']:
            lo, hi = max(start, source['logical_start']), min(end, source['logical_end'])
            if hi > lo:
                sources.append({'part': source['part'], 'paragraph_index': source['paragraph_index'], 'start': lo-source['logical_start'], 'end': hi-source['logical_start']})
        segment.setdefault('segment_metadata', {})['docx_segmentation'] = {
            'profile': 'en_docx_v1', 'sources': sources,
            'merge_reasons': info['reasons'] if len(sources) > 1 else [],
        }
        cursor = end
