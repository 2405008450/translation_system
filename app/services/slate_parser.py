from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.document_workspace import parse_docx_workspace
from app.services.matcher import match_sentences


def parse_docx_for_slate(
    db: Session,
    raw_bytes: bytes,
    similarity_threshold: float = 0.6,
) -> dict:
    workspace = parse_docx_workspace(raw_bytes)
    segments = workspace["segments"]

    slate_nodes = [
        {
            "type": "sentence",
            "id": segment["sentence_id"],
            "children": [{"type": "text", "text": segment["display_text"]}],
        }
        for segment in segments
    ]

    if not segments:
        return {"slate_document": slate_nodes, "segments": []}

    match_results = match_sentences(
        db=db,
        sentences=[segment["source_text"] for segment in segments],
        similarity_threshold=similarity_threshold,
        auxiliary_sentences=[
            f"{segment.get('numbering_text', '')} {segment['source_text']}".strip()
            if segment.get("numbering_text")
            else ""
            for segment in segments
        ],
    )

    output_segments: list[dict] = []
    for segment, match in zip(segments, match_results):
        output_segments.append(
            {
                "id": segment["sentence_id"],
                "source": segment["source_text"],
                "display_text": segment["display_text"],
                "target": match.target_text or "",
                "status": match.status,
                "score": match.score,
            }
        )

    return {
        "slate_document": slate_nodes,
        "segments": output_segments,
        "document_html": workspace["document_html"],
    }
