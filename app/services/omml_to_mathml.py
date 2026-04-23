from __future__ import annotations

from html import escape
import re
from xml.etree import ElementTree as ET


OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
_NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-zΑ-Ωα-ω]+$")
_OPERATOR_CHARS = frozenset("+-=±∓×÷*/<>≤≥≠≈∑∏∫∮√∞∂∇∪∩∈∉∅∀∃∴∵→←↔⇒⇔|()[]{}.,:;!?")

ET.register_namespace("m", OMML_NS)


def convert(node: ET.Element) -> str:
    """Convert an OMML math node into a MathML string."""
    node_name = _local_name(node.tag)
    display_attr = ' display="block"' if node_name == "oMathPara" else ""
    body = _convert_math_container(node)
    if not body:
        body = "<mrow></mrow>"
    return f'<math xmlns="{MATHML_NS}"{display_attr}>{body}</math>'


def serialize_omml_xml(node: ET.Element) -> str:
    return ET.tostring(node, encoding="unicode").strip()


def _convert_math_container(node: ET.Element) -> str:
    node_name = _local_name(node.tag)
    if node_name == "oMathPara":
        expressions = [
            _wrap_row(_convert_children(child))
            for child in list(node)
            if _local_name(child.tag) == "oMath"
        ]
        if expressions:
            return _wrap_row(expressions)
        return _wrap_row(_convert_children(node))
    if node_name == "oMath":
        return _wrap_row(_convert_children(node))
    return _convert_element(node)


def _convert_children(node: ET.Element, skip_names: set[str] | None = None) -> list[str]:
    skipped = skip_names or set()
    parts: list[str] = []
    for child in list(node):
        child_name = _local_name(child.tag)
        if child_name in skipped or child_name.endswith("Pr"):
            continue
        converted = _convert_element(child)
        if converted:
            parts.append(converted)
    return parts


def _convert_element(node: ET.Element) -> str:
    node_name = _local_name(node.tag)

    if node_name in {"oMath", "oMathPara"}:
        return _convert_math_container(node)
    if node_name == "r":
        return _wrap_row(_convert_children(node))
    if node_name == "t":
        return _token_to_mathml(node.text or "")
    if node_name == "f":
        return _convert_fraction(node)
    if node_name == "sSub":
        return _convert_script(node, "msub")
    if node_name == "sSup":
        return _convert_script(node, "msup")
    if node_name == "sSubSup":
        return _convert_script(node, "msubsup")
    if node_name == "rad":
        return _convert_radical(node)
    if node_name == "nary":
        return _convert_nary(node)
    if node_name == "d":
        return _convert_delimiter(node)
    if node_name == "m":
        return _convert_matrix(node)
    if node_name == "bar":
        return _convert_bar(node)
    if node_name == "acc":
        return _convert_accent(node)
    if node_name == "limLow":
        return _convert_limit(node, over=False)
    if node_name == "limUpp":
        return _convert_limit(node, over=True)
    if node_name == "func":
        return _convert_function(node)
    if node_name == "groupChr":
        return _convert_group_character(node)
    if node_name == "eqArr":
        return _convert_equation_array(node)
    if node_name in {"num", "den", "e", "sub", "sup", "deg", "fName", "lim"}:
        return _wrap_row(_convert_children(node))
    if node_name == "mr":
        cells = [
            f"<mtd>{_wrap_row(_convert_children(cell))}</mtd>"
            for cell in list(node)
            if _local_name(cell.tag) == "e"
        ]
        return f"<mtr>{''.join(cells)}</mtr>" if cells else ""
    if node_name in {"box", "borderBox"}:
        return _wrap_row(_convert_children(node))
    return _fallback_mathml(node)


def _convert_fraction(node: ET.Element) -> str:
    numerator = _convert_named_child(node, "num")
    denominator = _convert_named_child(node, "den")
    return f"<mfrac>{numerator}{denominator}</mfrac>"


def _convert_script(node: ET.Element, tag_name: str) -> str:
    base = _convert_named_child(node, "e")
    sub = _convert_named_child(node, "sub")
    sup = _convert_named_child(node, "sup")
    if tag_name == "msub":
        return f"<msub>{base}{sub}</msub>"
    if tag_name == "msup":
        return f"<msup>{base}{sup}</msup>"
    return f"<msubsup>{base}{sub}{sup}</msubsup>"


def _convert_radical(node: ET.Element) -> str:
    degree = _find_child(node, "deg")
    expression = _convert_named_child(node, "e")
    if degree is None:
        return f"<msqrt>{expression}</msqrt>"
    return f"<mroot>{expression}{_wrap_row(_convert_children(degree))}</mroot>"


def _convert_nary(node: ET.Element) -> str:
    properties = _find_child(node, "naryPr")
    operator = _read_math_char(properties, "chr", "∑")
    operator_mathml = f"<mo>{escape(operator)}</mo>"
    base = _convert_named_child(node, "e")
    lower = _find_child(node, "sub")
    upper = _find_child(node, "sup")

    if lower is not None and upper is not None:
        operator_mathml = (
            f"<munderover>{operator_mathml}"
            f"{_wrap_row(_convert_children(lower))}"
            f"{_wrap_row(_convert_children(upper))}</munderover>"
        )
    elif lower is not None:
        operator_mathml = f"<munder>{operator_mathml}{_wrap_row(_convert_children(lower))}</munder>"
    elif upper is not None:
        operator_mathml = f"<mover>{operator_mathml}{_wrap_row(_convert_children(upper))}</mover>"

    return f"<mrow>{operator_mathml}{base}</mrow>"


def _convert_delimiter(node: ET.Element) -> str:
    properties = _find_child(node, "dPr")
    beg_char = _read_math_char(properties, "begChr", "(")
    end_char = _read_math_char(properties, "endChr", ")")
    expression = _convert_named_child(node, "e")
    return (
        "<mrow>"
        f"<mo>{escape(beg_char)}</mo>"
        f"{expression}"
        f"<mo>{escape(end_char)}</mo>"
        "</mrow>"
    )


def _convert_matrix(node: ET.Element) -> str:
    rows = [_convert_element(child) for child in list(node) if _local_name(child.tag) == "mr"]
    rows = [row for row in rows if row]
    return f"<mtable>{''.join(rows)}</mtable>" if rows else "<mtable></mtable>"


def _convert_bar(node: ET.Element) -> str:
    properties = _find_child(node, "barPr")
    position = _read_math_attr(properties, "pos", "top")
    accent = "<mo>¯</mo>"
    expression = _convert_named_child(node, "e")
    if position == "bot":
        return f"<munder>{expression}{accent}</munder>"
    return f"<mover>{expression}{accent}</mover>"


def _convert_accent(node: ET.Element) -> str:
    properties = _find_child(node, "accPr")
    accent_char = _read_math_char(properties, "chr", "̂")
    expression = _convert_named_child(node, "e")
    return f"<mover accent=\"true\">{expression}<mo>{escape(accent_char)}</mo></mover>"


def _convert_limit(node: ET.Element, over: bool) -> str:
    base = _convert_named_child(node, "e")
    limit = _convert_named_child(node, "lim")
    tag_name = "mover" if over else "munder"
    return f"<{tag_name}>{base}{limit}</{tag_name}>"


def _convert_function(node: ET.Element) -> str:
    function_name = _convert_named_child(node, "fName")
    expression = _convert_named_child(node, "e")
    return f"<mrow>{function_name}{expression}</mrow>"


def _convert_group_character(node: ET.Element) -> str:
    properties = _find_child(node, "groupChrPr")
    position = _read_math_attr(properties, "pos", "top")
    group_char = _read_math_char(properties, "chr", "⏞")
    expression = _convert_named_child(node, "e")
    if position == "bot":
        return f"<munder>{expression}<mo>{escape(group_char)}</mo></munder>"
    return f"<mover>{expression}<mo>{escape(group_char)}</mo></mover>"


def _convert_equation_array(node: ET.Element) -> str:
    rows = [
        f"<mtr><mtd>{_wrap_row(_convert_children(child))}</mtd></mtr>"
        for child in list(node)
        if _local_name(child.tag) == "e"
    ]
    return f"<mtable>{''.join(rows)}</mtable>" if rows else "<mtable></mtable>"


def _convert_named_child(node: ET.Element, child_name: str) -> str:
    child = _find_child(node, child_name)
    if child is None:
        return "<mrow></mrow>"
    return _wrap_row(_convert_children(child))


def _fallback_mathml(node: ET.Element) -> str:
    text_value = _flatten_text(node)
    if not text_value:
        return "<mrow></mrow>"
    return f"<mtext>{escape(text_value)}</mtext>"


def _flatten_text(node: ET.Element) -> str:
    pieces: list[str] = []
    for element in node.iter():
        if _local_name(element.tag) == "t" and element.text:
            pieces.append(element.text)
    return "".join(pieces).strip()


def _wrap_row(parts: list[str]) -> str:
    filtered = [part for part in parts if part]
    if not filtered:
        return "<mrow></mrow>"
    if len(filtered) == 1:
        return filtered[0]
    return f"<mrow>{''.join(filtered)}</mrow>"


def _token_to_mathml(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if _NUMBER_RE.fullmatch(stripped):
        return f"<mn>{escape(stripped)}</mn>"
    if _IDENTIFIER_RE.fullmatch(stripped):
        return f"<mi>{escape(stripped)}</mi>"
    if all(char in _OPERATOR_CHARS for char in stripped):
        return f"<mo>{escape(stripped)}</mo>"
    return f"<mtext>{escape(stripped)}</mtext>"


def _read_math_char(node: ET.Element | None, child_name: str, default: str) -> str:
    if node is None:
        return default
    child = _find_child(node, child_name)
    if child is None:
        return default
    return _read_math_attr(child, "val", default) or default


def _read_math_attr(node: ET.Element | None, attr_name: str, default: str = "") -> str:
    if node is None:
        return default
    return node.get(_mq(attr_name), node.get(attr_name, default))


def _find_child(node: ET.Element, child_name: str) -> ET.Element | None:
    for child in list(node):
        if _local_name(child.tag) == child_name:
            return child
    return None


def _mq(local_name: str) -> str:
    return f"{{{OMML_NS}}}{local_name}"


def _local_name(tag: str) -> str:
    if not tag.startswith("{") or "}" not in tag:
        return tag
    return tag.rsplit("}", 1)[-1]
