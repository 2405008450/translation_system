"""文档和片段的持久化服务"""
import hashlib
from io import BytesIO

from sqlalchemy.orm import Session

from app.models import Document, Segment
from app.services.document_workspace import build_docx_workspace


def create_document_with_segments(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
) -> Document:
    """创建 DOCX 文档并解析保存所有片段"""
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # 创建文档记录
    document = Document(
        filename=filename,
        file_hash=file_hash,
        status="in_progress",
    )
    db.add(document)
    db.flush()  # 获取 document.id

    # 解析文档获取片段
    workspace_data = build_docx_workspace(
        db=db,
        raw_bytes=raw_bytes,
        similarity_threshold=similarity_threshold,
    )

    # 保存片段
    for seg in workspace_data["segments"]:
        segment = Segment(
            document_id=document.id,
            sentence_id=seg["sentence_id"],
            source_text=seg["source_text"],
            display_text=seg["display_text"],
            target_text=seg["target_text"],
            status=seg["status"],
            score=seg["score"],
            matched_source_text=seg["matched_source_text"],
            source="tm" if seg["status"] in ("exact", "fuzzy") else "none",
            block_type=seg["block_type"],
            block_index=seg["block_index"],
            row_index=seg.get("row_index"),
            cell_index=seg.get("cell_index"),
        )
        db.add(segment)

    db.commit()
    db.refresh(document)

    return document


def create_txt_document_with_segments(
    db: Session,
    content: str,
    filename: str,
    results: list,
) -> Document:
    """创建 TXT 文档并保存匹配结果为片段"""
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # 创建文档记录
    document = Document(
        filename=filename,
        file_hash=file_hash,
        status="in_progress",
    )
    db.add(document)
    db.flush()

    # 保存片段
    for i, r in enumerate(results):
        segment = Segment(
            document_id=document.id,
            sentence_id=f"sent-{i+1:05d}",
            source_text=r.source_sentence,
            display_text=r.source_sentence,
            target_text=r.target_text or "",
            status=r.status,
            score=r.score,
            matched_source_text=r.matched_source_text,
            source="tm" if r.status in ("exact", "fuzzy") else "none",
            block_type="paragraph",
            block_index=i,
        )
        db.add(segment)

    db.commit()
    db.refresh(document)

    return document


def get_document(db: Session, document_id: int) -> Document | None:
    """获取文档"""
    return db.query(Document).filter(Document.id == document_id).first()


def get_document_with_segments(db: Session, document_id: int) -> dict | None:
    """获取文档及其所有片段"""
    document = get_document(db, document_id)
    if not document:
        return None

    segments = (
        db.query(Segment)
        .filter(Segment.document_id == document_id)
        .order_by(Segment.block_index, Segment.id)
        .all()
    )

    return {
        "document": document,
        "segments": segments,
    }


def list_documents(db: Session, skip: int = 0, limit: int = 50) -> list[Document]:
    """获取文档列表"""
    return (
        db.query(Document)
        .order_by(Document.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def update_segment_target(
    db: Session,
    segment_id: int,
    target_text: str,
    source: str = "manual",
) -> Segment | None:
    """更新片段译文"""
    segment = db.query(Segment).filter(Segment.id == segment_id).first()
    if not segment:
        return None

    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"

    db.commit()
    db.refresh(segment)
    return segment


def update_segment_by_sentence_id(
    db: Session,
    document_id: int,
    sentence_id: str,
    target_text: str,
    source: str = "manual",
) -> Segment | None:
    """通过 sentence_id 更新片段译文"""
    segment = (
        db.query(Segment)
        .filter(Segment.document_id == document_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        return None

    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"

    db.commit()
    db.refresh(segment)
    return segment


def batch_update_segments(
    db: Session,
    document_id: int,
    updates: list[dict],
) -> int:
    """批量更新片段译文，返回更新数量"""
    updated_count = 0
    for item in updates:
        sentence_id = item.get("sentence_id")
        target_text = item.get("target_text", "")
        source = item.get("source", "manual")

        segment = (
            db.query(Segment)
            .filter(Segment.document_id == document_id, Segment.sentence_id == sentence_id)
            .first()
        )
        if segment:
            segment.target_text = target_text
            segment.source = source
            if source == "manual":
                segment.status = "confirmed"
            updated_count += 1

    db.commit()
    return updated_count


def delete_document(db: Session, document_id: int) -> bool:
    """删除文档及其所有片段"""
    document = get_document(db, document_id)
    if not document:
        return False

    db.delete(document)
    db.commit()
    return True
