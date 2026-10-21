"""为真实修订生成整句批注，并完整维护 DOCX 的批注部件和关系。"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
COMMENTS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"


@dataclass(frozen=True)
class SentenceComment:
    sentence_id: str
    comment_id: int
    text: str


def allocate_comment_ids(raw_bytes: bytes | None, sentence_ids: list[str]) -> dict[str, int]:
    """同时避开批注正文和所有故事中的锚点编号，包括原件里的孤立锚点。"""
    used: set[int] = set()
    if raw_bytes is not None:
        with ZipFile(BytesIO(raw_bytes)) as archive:
            for name in archive.namelist():
                if not name.endswith(".xml"):
                    continue
                root = etree.fromstring(archive.read(name))
                for node in root.iter(W + "comment", W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference"):
                    try:
                        used.add(int(node.get(W + "id", "")))
                    except ValueError as exc:
                        raise ValueError("原文档批注编号无效，无法安全追加校对批注。") from exc
    first = max(used, default=-1) + 1
    return {sentence_id: first + index for index, sentence_id in enumerate(sentence_ids)}


def wrap_sentence_comment(nodes: list[ET.Element], comment_id: int | None) -> list[ET.Element]:
    """批注范围包在整句外层，不能放进接受/拒绝时会被移除的增删节点。"""
    if comment_id is None:
        return nodes
    start = ET.Element(W + "commentRangeStart", {W + "id": str(comment_id)})
    end = ET.Element(W + "commentRangeEnd", {W + "id": str(comment_id)})
    reference = ET.Element(W + "r")
    properties = ET.SubElement(reference, W + "rPr")
    ET.SubElement(properties, W + "rStyle", {W + "val": "CommentReference"})
    ET.SubElement(reference, W + "commentReference", {W + "id": str(comment_id)})
    return [start, *nodes, end, reference]


def add_sentence_comments(raw_bytes: bytes, comments: list[SentenceComment]) -> bytes:
    if not comments:
        return raw_bytes
    with ZipFile(BytesIO(raw_bytes)) as archive:
        # 校验成品中的实际锚点，不能以插入调用成功代替定位成功。
        root = etree.fromstring(archive.read("word/document.xml"))
        for comment in comments:
            found = [root.findall(f".//{W}{kind}[@{W}id='{comment.comment_id}']")
                     for kind in ("commentRangeStart", "commentRangeEnd", "commentReference")]
            if any(len(items) != 1 for items in found):
                raise ValueError(f"校对批注无法安全定位到正文整句：{comment.sentence_id}。")
            start, end, reference = (items[0] for items in found)
            paragraph = start.getparent()
            if (paragraph.tag != W + "p" or end.getparent() is not paragraph
                    or reference.getparent().tag != W + "r"
                    or reference.getparent().getparent() is not paragraph
                    or not paragraph.index(start) < paragraph.index(end) < paragraph.index(reference.getparent())
                    or any(node.tag in {W + "ins", W + "del"} for node in start.iterancestors())):
                raise ValueError(f"校对批注范围不能与增删修订混合：{comment.sentence_id}。")

        rels = (etree.fromstring(archive.read(DOCUMENT_RELS)) if DOCUMENT_RELS in archive.namelist()
                else etree.Element(f"{{{RELS}}}Relationships", nsmap={None: RELS}))
        comment_rels = [node for node in rels if node.get("Type") == COMMENTS_REL]
        if len(comment_rels) > 1:
            raise ValueError("原文档存在重复的批注关系，无法安全追加校对批注。")
        part_name = "word/comments.xml"
        if comment_rels:
            relation = comment_rels[0]
            target = relation.get("Target", "")
            if relation.get("TargetMode") == "External" or not target:
                raise ValueError("原文档批注关系无效，无法安全追加校对批注。")
            part_name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("word", target))
        else:
            used_ids = {node.get("Id") for node in rels}
            index = 1
            while f"rIdProofreadingComments{index}" in used_ids:
                index += 1
            etree.SubElement(rels, f"{{{RELS}}}Relationship", Id=f"rIdProofreadingComments{index}",
                             Type=COMMENTS_REL, Target="comments.xml")
        if part_name.startswith("../"):
            raise ValueError("原文档批注部件路径无效。")
        comment_root = (etree.fromstring(archive.read(part_name)) if part_name in archive.namelist()
                        else etree.Element(W + "comments", nsmap={"w": W[1:-1]}))
        if comment_root.tag != W + "comments":
            raise ValueError("原文档批注部件无效，无法安全追加校对批注。")
        existing_ids = {node.get(W + "id") for node in comment_root}
        for comment in comments:
            if str(comment.comment_id) in existing_ids:
                raise ValueError("校对批注编号与原文档重复。")
            existing_ids.add(str(comment.comment_id))
            node = etree.SubElement(comment_root, W + "comment", {W + "id": str(comment.comment_id), W + "author": "校对工作流"})
            for line in comment.text.splitlines() or [""]:
                paragraph = etree.SubElement(node, W + "p")
                run = etree.SubElement(paragraph, W + "r")
                text = etree.SubElement(run, W + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                text.text = line

        content_types = etree.fromstring(archive.read("[Content_Types].xml"))
        part_uri = "/" + part_name
        overrides = [node for node in content_types if node.get("PartName") == part_uri]
        if overrides:
            overrides[0].set("ContentType", COMMENTS_TYPE)
        else:
            etree.SubElement(content_types, f"{{{TYPES}}}Override", PartName=part_uri, ContentType=COMMENTS_TYPE)
        updates = {
            name: etree.tostring(xml, xml_declaration=True, encoding="UTF-8", standalone=True)
            for name, xml in ((part_name, comment_root), (DOCUMENT_RELS, rels), ("[Content_Types].xml", content_types))
        }
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as target:
            for info in archive.infolist():
                target.writestr(info, updates.pop(info.filename, archive.read(info.filename)))
            for name, content in updates.items():
                target.writestr(name, content)
    return output.getvalue()
