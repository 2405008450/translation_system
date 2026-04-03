import uuid
from io import BytesIO

from docx import Document
from sqlalchemy.orm import Session

from app.schemas import MatchResult
from app.services.matcher import match_sentences
from app.services.sentence_splitter import split_sentences


def parse_docx_for_slate(db: Session, raw_bytes: bytes, similarity_threshold: float = 0.6) -> dict:
    """"""
    # 将 docx 转换为 Slate JSON 并匹配 TM
    """"""
    document = Document(BytesIO(raw_bytes))
    
    slate_nodes = []
    segments = []
    
    # 获取所有的段落文本并提前准备分句
    for paragraph in document.paragraphs:
        p_text = paragraph.text.strip()
        if not p_text:
            continue
            
        sentences = split_sentences(p_text)
        if not sentences:
            # 没有提取出句子，按原样存入段落
            slate_nodes.append({
                "type": "paragraph",
                "children": [{"type": "text", "text": p_text}]
            })
            continue
            
        p_children = []
        for sent_text in sentences:
            sent_id = str(uuid.uuid4())
            p_children.append({
                "type": "sentence",
                "id": sent_id,
                "children": [{"type": "text", "text": sent_text}]
            })
            segments.append({
                "id": sent_id,
                "source": sent_text,
                "target": "",
                "status": "new",
                "score": 0.0,
            })
            
        slate_nodes.append({
            "type": "paragraph",
            "children": p_children
        })

    # 对提取到的所有独立句子进行 TM 匹配
    if segments:
        sources = [seg["source"] for seg in segments]
        match_results = match_sentences(db=db, sentences=sources, similarity_threshold=similarity_threshold)
        
        # 将匹配结果写回 segments 数组
        # matcher.match_sentences 保证了返回的列表顺序与传入的 sentences 顺序一致
        for i, match in enumerate(match_results):
            if match.status != "none":
                segments[i]["target"] = match.target_text or ""
                segments[i]["status"] = match.status
                segments[i]["score"] = match.score

    return {
        "slate_document": slate_nodes,
        "segments": segments
    }
