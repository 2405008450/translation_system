from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from lxml import etree

from app.services.normalizer import normalize_text


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class TMXSegment:
    language: str
    text: str
    attributes: dict[str, str] = field(default_factory=dict)
    props: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TMXUnit:
    row_index: int
    tuid: str
    attributes: dict[str, str] = field(default_factory=dict)
    props: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    segments: list[TMXSegment] = field(default_factory=list)


@dataclass
class TMXHeader:
    attributes: dict[str, str] = field(default_factory=dict)
    props: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TMXRow:
    source_text: str
    target_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def iter_tmx_rows(
    source: str | Path | bytes | bytearray | BinaryIO,
    source_language: str,
    target_language: str,
) -> Iterable[TMXRow]:
    for unit in iter_tmx_units(source):
        source_segment = find_tmx_language_segment(unit.segments, source_language)
        target_segment = find_tmx_language_segment(unit.segments, target_language)
        if (source_segment is None or target_segment is None) and len(unit.segments) >= 2:
            source_segment = source_segment or unit.segments[0]
            target_segment = target_segment or unit.segments[1]

        source_text = source_segment.text if source_segment is not None else ""
        target_text = target_segment.text if target_segment is not None else ""
        yield TMXRow(
            source_text=source_text,
            target_text=target_text,
            metadata=build_tmx_row_metadata(unit, source_segment, target_segment),
        )


def read_tmx_header(source: str | Path | bytes | bytearray | BinaryIO) -> TMXHeader:
    for _, element in _iterparse(source, tag=("{*}header",)):
        header = TMXHeader(
            attributes=_clean_attributes(element.attrib),
            props=_extract_props(element),
            notes=_extract_notes(element),
        )
        _clear_element(element)
        return header
    return TMXHeader()


def iter_tmx_units(source: str | Path | bytes | bytearray | BinaryIO) -> Iterable[TMXUnit]:
    row_index = 0
    for _, element in _iterparse(source, tag=("{*}tu",)):
        row_index += 1
        yield _parse_tmx_unit(element, row_index)
        _clear_element(element)


def build_tmx_row_metadata(
    unit: TMXUnit,
    source_segment: TMXSegment | None,
    target_segment: TMXSegment | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "tuid": unit.tuid,
        "tu_attributes": unit.attributes,
        "tu_props": unit.props,
        "tu_notes": unit.notes,
    }
    if source_segment is not None:
        metadata["source_tuv"] = _segment_metadata(source_segment)
    if target_segment is not None:
        metadata["target_tuv"] = _segment_metadata(target_segment)
    return _compact_metadata(metadata)


def find_tmx_language_segment(
    segments: list[TMXSegment],
    language: str,
) -> TMXSegment | None:
    normalized_language = normalize_tmx_language_tag(language)
    for segment in segments:
        if normalize_tmx_language_tag(segment.language) == normalized_language:
            return segment

    primary_language = normalized_language.split("-", 1)[0]
    for segment in segments:
        if normalize_tmx_language_tag(segment.language).split("-", 1)[0] == primary_language:
            return segment
    return None


def normalize_tmx_language_tag(language: str) -> str:
    return (language or "").strip().lower().replace("_", "-")


def _iterparse(source: str | Path | bytes | bytearray | BinaryIO, *, tag: tuple[str, ...]):
    parser_source: Any
    close_source = False
    if isinstance(source, (bytes, bytearray)):
        parser_source = BytesIO(bytes(source))
    elif isinstance(source, (str, Path)):
        parser_source = open(source, "rb")
        close_source = True
    else:
        parser_source = source
        try:
            parser_source.seek(0)
        except (AttributeError, OSError):
            pass

    try:
        yield from etree.iterparse(
            parser_source,
            events=("end",),
            tag=tag,
            recover=True,
            huge_tree=True,
            resolve_entities=False,
            remove_comments=True,
        )
    finally:
        if close_source:
            parser_source.close()


def _parse_tmx_unit(element: etree._Element, row_index: int) -> TMXUnit:
    attributes = _clean_attributes(element.attrib)
    unit = TMXUnit(
        row_index=row_index,
        tuid=attributes.get("tuid", ""),
        attributes=attributes,
        props=_extract_props(element),
        notes=_extract_notes(element),
    )
    for tuv in element.iterchildren(tag=etree.Element):
        if _local_name(tuv.tag) != "tuv":
            continue
        segment = _parse_tmx_segment(tuv)
        if segment.text:
            unit.segments.append(segment)
    return unit


def _parse_tmx_segment(element: etree._Element) -> TMXSegment:
    attributes = _clean_attributes(element.attrib)
    language = (
        attributes.get(XML_LANG)
        or attributes.get("xml:lang")
        or attributes.get("lang")
        or attributes.get("language")
        or ""
    )
    segment_element = None
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) == "seg":
            segment_element = child
            break
    text = normalize_text("".join(segment_element.itertext())) if segment_element is not None else ""
    return TMXSegment(
        language=language,
        text=text,
        attributes=attributes,
        props=_extract_props(element),
        notes=_extract_notes(element),
    )


def _extract_props(element: etree._Element) -> list[dict[str, str]]:
    props: list[dict[str, str]] = []
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) != "prop":
            continue
        prop_type = child.attrib.get("type") or ""
        value = normalize_text("".join(child.itertext()))
        if prop_type or value:
            props.append({"type": prop_type, "value": value})
    return props


def _extract_notes(element: etree._Element) -> list[str]:
    notes: list[str] = []
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) != "note":
            continue
        value = normalize_text("".join(child.itertext()))
        if value:
            notes.append(value)
    return notes


def _segment_metadata(segment: TMXSegment) -> dict[str, Any]:
    return _compact_metadata(
        {
            "language": segment.language,
            "attributes": segment.attributes,
            "props": segment.props,
            "notes": segment.notes,
        }
    )


def _clean_attributes(attributes: Any) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in dict(attributes).items():
        clean_key = str(key)
        if clean_key == XML_LANG:
            clean_key = "xml:lang"
        cleaned[clean_key] = str(value)
    return cleaned


def _compact_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in ("", None, [], {})
    }


def _clear_element(element: etree._Element) -> None:
    element.clear()
    parent = element.getparent()
    while parent is not None and element.getprevious() is not None:
        del parent[0]


def _local_name(tag: Any) -> str:
    value = str(tag)
    if "}" in value:
        return value.rsplit("}", 1)[-1]
    return value
