from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from lxml import etree

from app.services.normalizer import normalize_text
from app.services.tmx_stream import normalize_tmx_language_tag


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class TBXTerm:
    language: str
    text: str
    lang_set_attributes: dict[str, str] = field(default_factory=dict)
    tig_attributes: dict[str, str] = field(default_factory=dict)
    term_notes: list[dict[str, str]] = field(default_factory=list)
    descrips: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TBXEntry:
    row_index: int
    entry_id: str
    attributes: dict[str, str] = field(default_factory=dict)
    terms: list[TBXTerm] = field(default_factory=list)


@dataclass
class TBXRow:
    source_text: str
    target_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def iter_tbx_rows(
    source: str | Path | bytes | bytearray | BinaryIO,
    source_language: str,
    target_language: str,
) -> Iterable[TBXRow]:
    for entry in iter_tbx_entries(source):
        source_term = find_tbx_language_term(entry.terms, source_language)
        target_term = find_tbx_language_term(entry.terms, target_language)
        if (source_term is None or target_term is None) and len(entry.terms) >= 2:
            source_term = source_term or entry.terms[0]
            target_term = target_term or entry.terms[1]

        source_text = source_term.text if source_term is not None else ""
        target_text = target_term.text if target_term is not None else ""
        yield TBXRow(
            source_text=source_text,
            target_text=target_text,
            metadata=build_tbx_row_metadata(entry, source_term, target_term),
        )


def iter_tbx_entries(source: str | Path | bytes | bytearray | BinaryIO) -> Iterable[TBXEntry]:
    row_index = 0
    for _, element in _iterparse(source, tag=("{*}termEntry",)):
        row_index += 1
        yield _parse_tbx_entry(element, row_index)
        _clear_element(element)


def build_tbx_row_metadata(
    entry: TBXEntry,
    source_term: TBXTerm | None,
    target_term: TBXTerm | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "tbx_entry_id": entry.entry_id,
        "tbx_entry_attributes": entry.attributes,
    }
    if source_term is not None:
        metadata["source_tbx_term"] = _term_metadata(source_term)
    if target_term is not None:
        metadata["target_tbx_term"] = _term_metadata(target_term)
    return _compact_metadata(metadata)


def find_tbx_language_term(terms: list[TBXTerm], language: str) -> TBXTerm | None:
    normalized_language = normalize_tmx_language_tag(language)
    for term in terms:
        if normalize_tmx_language_tag(term.language) == normalized_language:
            return term

    primary_language = normalized_language.split("-", 1)[0]
    for term in terms:
        if normalize_tmx_language_tag(term.language).split("-", 1)[0] == primary_language:
            return term
    return None


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


def _parse_tbx_entry(element: etree._Element, row_index: int) -> TBXEntry:
    attributes = _clean_attributes(element.attrib)
    entry = TBXEntry(
        row_index=row_index,
        entry_id=attributes.get("id", ""),
        attributes=attributes,
    )
    for lang_set in element.iterchildren(tag=etree.Element):
        if _local_name(lang_set.tag) != "langSet":
            continue
        entry.terms.extend(_parse_tbx_lang_set(lang_set))
    return entry


def _parse_tbx_lang_set(element: etree._Element) -> list[TBXTerm]:
    attributes = _clean_attributes(element.attrib)
    language = (
        attributes.get(XML_LANG)
        or attributes.get("xml:lang")
        or attributes.get("lang")
        or attributes.get("language")
        or ""
    )
    terms: list[TBXTerm] = []
    for child in element.iterchildren(tag=etree.Element):
        child_name = _local_name(child.tag)
        if child_name in {"tig", "ntig"}:
            term = _parse_tbx_tig(child, language, attributes)
            if term is not None:
                terms.append(term)
        elif child_name == "term":
            text = normalize_text("".join(child.itertext()))
            if text:
                terms.append(
                    TBXTerm(
                        language=language,
                        text=text,
                        lang_set_attributes=attributes,
                    )
                )
    return terms


def _parse_tbx_tig(
    element: etree._Element,
    language: str,
    lang_set_attributes: dict[str, str],
) -> TBXTerm | None:
    term_text = _find_tbx_term_text(element)
    if not term_text:
        return None
    return TBXTerm(
        language=language,
        text=term_text,
        lang_set_attributes=lang_set_attributes,
        tig_attributes=_clean_attributes(element.attrib),
        term_notes=_extract_typed_children(element, "termNote"),
        descrips=_extract_typed_children(element, "descrip"),
        notes=_extract_notes(element),
    )


def _find_tbx_term_text(element: etree._Element) -> str:
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) == "term":
            text = normalize_text("".join(child.itertext()))
            if text:
                return text
    for child in element.iterdescendants(tag=etree.Element):
        if _local_name(child.tag) == "term":
            text = normalize_text("".join(child.itertext()))
            if text:
                return text
    return ""


def _extract_typed_children(element: etree._Element, local_name: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) != local_name:
            continue
        item_type = child.attrib.get("type") or ""
        value = normalize_text("".join(child.itertext()))
        if item_type or value:
            items.append({"type": item_type, "value": value})
    return items


def _extract_notes(element: etree._Element) -> list[str]:
    notes: list[str] = []
    for child in element.iterchildren(tag=etree.Element):
        if _local_name(child.tag) != "note":
            continue
        value = normalize_text("".join(child.itertext()))
        if value:
            notes.append(value)
    return notes


def _term_metadata(term: TBXTerm) -> dict[str, Any]:
    return _compact_metadata(
        {
            "language": term.language,
            "lang_set_attributes": term.lang_set_attributes,
            "tig_attributes": term.tig_attributes,
            "term_notes": term.term_notes,
            "descrips": term.descrips,
            "notes": term.notes,
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
