"""参考文件分析 API 路由"""

import asyncio
import json
import os
import shutil
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import FileRecord, Project, ReferenceFile, ReferenceProfile, Segment, SegmentRevision, User
from app.services.reference_analyzer.service import (
    analyze_reference_files,
    get_reference_llm_helper,
    profile_to_dict,
)
from app.services.reference_analyzer.progress import (
    ProgressReporter,
    get_progress,
    clear_progress,
    progress_to_dict,
)
from app.services.llm_service import (
    LLMConfigurationError,
    LLMTranslationFailure,
    LLMTranslationTask,
    iter_batch_translate,
    split_format_tagged_translation,
    validate_provider_choice,
)
from app.services.reference_sync_service import (
    cleanup_reference_profile_resources,
    sync_reference_profile_resources,
)
from app.services.file_operation_lock_service import (
    FILE_OPERATION_TOKEN_HEADER,
    ensure_file_record_write_allowed,
)
from app.services.file_record_service import SEGMENT_ORDERING, set_segment_target_layout_text
from app.services.analytics_service import count_source_words, record_translation_metric_event
from app.services.normalizer import normalize_text
from app.services.segment_status import apply_segment_status

router = APIRouter(prefix="/reference", tags=["reference"])
REFERENCE_UPLOAD_COPY_CHUNK_SIZE = 1024 * 1024


class ReferenceProfileResponse(BaseModel):
    id: str
    file_record_id: Optional[str]
    source_files: List[str]
    terminology_count: int
    tm_count: int
    style: Optional[dict]
    analysis_report: Optional[dict]
    overall_confidence: float
    created_at: str
    updated_at: str


class AnalyzeResponse(BaseModel):
    profile_id: str
    source_files: List[str]
    terminology_count: int
    tm_count: int
    style: Optional[dict]
    analysis_report: Optional[dict]
    overall_confidence: float


@router.post("/file-records/{file_record_id}/upload", response_model=dict)
async def upload_reference_file(
    file_record_id: str,
    file: UploadFile = File(...),
    is_bilingual_source: bool = Form(False),
    is_bilingual_target: bool = Form(False),
    bilingual_pair_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传参考文件到指定任务"""
    settings = get_settings()
    
    file_record = db.execute(
        select(FileRecord).where(FileRecord.id == uuid.UUID(file_record_id))
    ).scalar_one_or_none()
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    profile = db.execute(
        select(ReferenceProfile).where(ReferenceProfile.file_record_id == file_record.id)
    ).scalar_one_or_none()
    
    if not profile:
        profile = ReferenceProfile(file_record_id=file_record.id)
        db.add(profile)
        db.flush()
    
    storage_dir = os.path.join(settings.file_storage_dir, "reference_files", str(profile.id))
    os.makedirs(storage_dir, exist_ok=True)
    
    original_filename = file.filename or "reference"
    file_ext = os.path.splitext(original_filename)[1]
    file_id = str(uuid.uuid4())[:8]
    saved_filename = f"{file_id}{file_ext}"
    file_path = os.path.join(storage_dir, saved_filename)

    file_size = 0
    max_file_size = max(1, int(settings.upload_max_size_mb or 100)) * 1024 * 1024
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(REFERENCE_UPLOAD_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > max_file_size:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件 {original_filename} 超过大小限制（{settings.upload_max_size_mb} MB）。",
                    )
                f.write(chunk)
    except Exception:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    if file_size <= 0:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail="上传的参考文件为空。")
    
    ref_file = ReferenceFile(
        profile_id=profile.id,
        filename=original_filename,
        file_path=file_path,
        file_size=file_size,
        is_bilingual_source=is_bilingual_source,
        is_bilingual_target=is_bilingual_target,
        bilingual_pair_id=uuid.UUID(bilingual_pair_id) if bilingual_pair_id else None,
    )
    db.add(ref_file)
    db.commit()
    
    return {
        "file_id": str(ref_file.id),
        "filename": original_filename,
        "file_size": file_size,
        "profile_id": str(profile.id),
    }


import logging
import traceback

logger = logging.getLogger(__name__)


@router.get("/file-records/{file_record_id}/analyze/progress")
async def get_analyze_progress(
    file_record_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取分析进度（轮询端点）"""
    progress = get_progress(file_record_id)
    
    if progress:
        return progress_to_dict(progress)
    
    # 没有进度信息时返回空状态
    return {
        "stage": "idle",
        "stage_label": "等待中",
        "progress": 0,
        "message": "",
        "detail": None,
    }


@router.post("/file-records/{file_record_id}/analyze", response_model=AnalyzeResponse)
async def analyze_reference(
    file_record_id: str,
    enable_deep_analysis: bool = Form(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分析已上传的参考文件"""
    settings = get_settings()
    
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="未找到参考文件，请先上传")
    
    ref_files = db.execute(
        select(ReferenceFile).where(ReferenceFile.profile_id == profile.id)
    ).scalars().all()
    
    if not ref_files:
        raise HTTPException(status_code=400, detail="没有上传任何参考文件")
    
    file_paths = []
    bilingual_pairs = []
    bilingual_sources = {}
    bilingual_targets = {}
    
    for ref_file in ref_files:
        if ref_file.is_bilingual_source and ref_file.bilingual_pair_id:
            bilingual_sources[str(ref_file.bilingual_pair_id)] = ref_file.file_path
        elif ref_file.is_bilingual_target and ref_file.bilingual_pair_id:
            bilingual_targets[str(ref_file.bilingual_pair_id)] = ref_file.file_path
        else:
            file_paths.append(ref_file.file_path)
    
    for pair_id, source_path in bilingual_sources.items():
        if pair_id in bilingual_targets:
            bilingual_pairs.append((source_path, bilingual_targets[pair_id]))
    
    # 验证双语文件对是否正确配置
    if not file_paths and not bilingual_pairs:
        # 检查是否有未配对的原文或译文
        unmatched_sources = [k for k in bilingual_sources.keys() if k not in bilingual_targets]
        unmatched_targets = [k for k in bilingual_targets.keys() if k not in bilingual_sources]
        
        if unmatched_sources or unmatched_targets:
            raise HTTPException(
                status_code=400, 
                detail="双语文件配对不完整：请确保原文和译文文件都已正确标注且配对ID匹配"
            )
        raise HTTPException(status_code=400, detail="没有可分析的参考文件")
    
    # 检查文件是否存在
    all_paths = file_paths + [p for pair in bilingual_pairs for p in pair]
    missing_files = [p for p in all_paths if not os.path.exists(p)]
    if missing_files:
        logger.error(f"参考文件不存在: {missing_files}")
        raise HTTPException(
            status_code=400, 
            detail=f"参考文件不存在，请重新上传: {', '.join([os.path.basename(p) for p in missing_files])}"
        )
    
    llm_helper = get_reference_llm_helper(settings)
    
    # 创建进度报告器
    progress_reporter = ProgressReporter(file_record_id)
    
    # 使用 asyncio 的线程执行器来运行分析，让事件循环可以处理其他请求
    import asyncio
    
    def run_analysis():
        return analyze_reference_files(
            file_paths=file_paths,
            bilingual_pairs=bilingual_pairs if bilingual_pairs else None,
            llm_helper=llm_helper,
            enable_deep_analysis=enable_deep_analysis,
            progress_reporter=progress_reporter,
        )
    
    try:
        # 在线程池中执行分析，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        translation_profile = await loop.run_in_executor(None, run_analysis)
    except Exception as e:
        logger.error(f"参考文件分析失败: {e}\n{traceback.format_exc()}")
        progress_reporter.error(str(e))
        raise HTTPException(
            status_code=500, 
            detail=f"参考文件分析失败: {str(e)}"
        )
    
    profile_dict = profile_to_dict(translation_profile)
    
    profile.source_files = json.dumps(translation_profile.source_files, ensure_ascii=False)
    profile.terminology = json.dumps(
        [{"source": t.source, "target": t.target, "context": t.context, "category": t.category}
         for t in translation_profile.constraints.terminology],
        ensure_ascii=False
    )
    profile.translation_memory = json.dumps(
        [{"source": p.source, "target": p.target, "similarity": p.similarity}
         for p in translation_profile.references.translation_memory],
        ensure_ascii=False
    )
    
    if translation_profile.references.style:
        style = translation_profile.references.style
        profile.style_guide = json.dumps({
            "tone": style.tone, "person": style.person,
            "preferences": style.preferences, "avoid": style.avoid,
        }, ensure_ascii=False)
    
    # 始终保存分析报告（即使是默认值）
    if translation_profile.analysis:
        analysis_data = profile_dict.get("analysis", {})
        logger.info(f"保存分析报告: {json.dumps(analysis_data, ensure_ascii=False, default=str)[:500]}")
        profile.analysis_report = json.dumps(analysis_data, ensure_ascii=False)
        profile.overall_confidence = translation_profile.analysis.overall_confidence
    else:
        # 即使没有深度分析，也保存基本结构
        logger.info("分析结果为空，保存默认报告结构")
        profile.analysis_report = json.dumps({
            "industry": "general",
            "strategy": "balanced",
            "overall_confidence": 0.0
        }, ensure_ascii=False)
        profile.overall_confidence = 0.0

    # 把术语和翻译记忆同步到项目级的词汇表/记忆库，让翻译流程通过原生通道命中。
    # 文件不属于任何项目时，sync 会自动跳过；同步失败不阻塞分析结果保存。
    file_record = (
        db.execute(
            select(FileRecord).where(FileRecord.id == uuid.UUID(file_record_id))
        )
        .scalar_one_or_none()
    )
    project = None
    if file_record is not None and file_record.project_id is not None:
        project = (
            db.execute(select(Project).where(Project.id == file_record.project_id))
            .scalar_one_or_none()
        )

    # 先把 profile 的修改 flush 进当前事务，再尝试同步；失败则回滚同步引发的脏状态。
    db.flush()
    sync_savepoint = db.begin_nested()
    try:
        sync_reference_profile_resources(
            db,
            profile,
            project=project,
            source_language=file_record.source_language if file_record else None,
            target_language=file_record.target_language if file_record else None,
            creator_id=getattr(current_user, "id", None),
            terminology=[
                {
                    "source": t.source,
                    "target": t.target,
                    "context": t.context,
                    "category": t.category,
                }
                for t in translation_profile.constraints.terminology
            ],
            translation_memory=[
                {"source": p.source, "target": p.target}
                for p in translation_profile.references.translation_memory
            ],
        )
        sync_savepoint.commit()
    except Exception as exc:  # 同步失败不阻塞分析结果保存，但要记录日志
        sync_savepoint.rollback()
        logger.warning(
            "参考分析同步到词汇表/记忆库失败 profile_id=%s error=%s",
            profile.id,
            exc,
        )

    db.commit()    
    return AnalyzeResponse(
        profile_id=str(profile.id),
        source_files=translation_profile.source_files,
        terminology_count=len(translation_profile.constraints.terminology),
        tm_count=len(translation_profile.references.translation_memory),
        style=json.loads(profile.style_guide) if profile.style_guide else None,
        analysis_report=json.loads(profile.analysis_report) if profile.analysis_report else None,
        overall_confidence=profile.overall_confidence,
    )


@router.get("/file-records/{file_record_id}/profile", response_model=ReferenceProfileResponse)
async def get_reference_profile(
    file_record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取任务的参考分析结果"""
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="未找到参考分析结果")
    
    return ReferenceProfileResponse(
        id=str(profile.id),
        file_record_id=str(profile.file_record_id) if profile.file_record_id else None,
        source_files=json.loads(profile.source_files) if profile.source_files else [],
        terminology_count=len(json.loads(profile.terminology)) if profile.terminology else 0,
        tm_count=len(json.loads(profile.translation_memory)) if profile.translation_memory else 0,
        style=json.loads(profile.style_guide) if profile.style_guide else None,
        analysis_report=json.loads(profile.analysis_report) if profile.analysis_report else None,
        overall_confidence=profile.overall_confidence,
        created_at=profile.created_at.isoformat() if profile.created_at else "",
        updated_at=profile.updated_at.isoformat() if profile.updated_at else "",
    )


@router.get("/file-records/{file_record_id}/files", response_model=List[dict])
async def list_reference_files(
    file_record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取任务已上传的参考文件列表"""
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile:
        return []
    
    ref_files = db.execute(
        select(ReferenceFile).where(ReferenceFile.profile_id == profile.id)
    ).scalars().all()
    
    return [
        {
            "id": str(f.id),
            "filename": f.filename,
            "file_size": f.file_size,
            "is_bilingual_source": f.is_bilingual_source,
            "is_bilingual_target": f.is_bilingual_target,
            "bilingual_pair_id": str(f.bilingual_pair_id) if f.bilingual_pair_id else None,
            "created_at": f.created_at.isoformat() if f.created_at else "",
        }
        for f in ref_files
    ]


@router.get("/file-records/{file_record_id}/terminology", response_model=List[dict])
async def get_reference_terminology(
    file_record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取参考文件提取的术语"""
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile or not profile.terminology:
        return []
    
    return json.loads(profile.terminology)


@router.get("/file-records/{file_record_id}/tm", response_model=List[dict])
async def get_reference_tm(
    file_record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取参考文件提取的翻译记忆"""
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile or not profile.translation_memory:
        return []
    
    return json.loads(profile.translation_memory)


@router.delete("/file-records/{file_record_id}/profile")
async def delete_reference_profile(
    file_record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除参考分析结果和相关文件"""
    settings = get_settings()
    
    profile = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="未找到参考分析结果")
    
    # 删除文件系统中的参考文件
    storage_dir = os.path.join(settings.file_storage_dir, "reference_files", str(profile.id))
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir, ignore_errors=True)
    
    # 清理同步出来的项目级词汇表/记忆库及文件层绑定
    cleanup_reference_profile_resources(db, profile)

    # 删除数据库记录
    db.execute(
        ReferenceFile.__table__.delete().where(ReferenceFile.profile_id == profile.id)
    )
    db.delete(profile)
    db.commit()
    
    return {"message": "参考分析结果已删除"}


@router.delete("/files/{file_id}")
async def delete_reference_file(
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除单个参考文件"""
    ref_file = db.execute(
        select(ReferenceFile).where(ReferenceFile.id == uuid.UUID(file_id))
    ).scalar_one_or_none()
    
    if not ref_file:
        raise HTTPException(status_code=404, detail="参考文件不存在")
    
    # 删除文件
    if ref_file.file_path and os.path.exists(ref_file.file_path):
        os.remove(ref_file.file_path)
    
    db.delete(ref_file)
    db.commit()
    
    return {"message": "参考文件已删除"}


class UpdateFileRoleRequest(BaseModel):
    role: str  # "source" | "target" | "none"
    bilingual_pair_id: Optional[str] = None


@router.patch("/files/{file_id}/role")
async def update_file_role(
    file_id: str,
    request: UpdateFileRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新参考文件的双语角色"""
    ref_file = db.execute(
        select(ReferenceFile).where(ReferenceFile.id == uuid.UUID(file_id))
    ).scalar_one_or_none()
    
    if not ref_file:
        raise HTTPException(status_code=404, detail="参考文件不存在")
    
    # 处理 bilingual_pair_id：支持完整UUID或短字符串
    def parse_pair_id(pair_id_str: str | None) -> uuid.UUID | None:
        if not pair_id_str:
            return None
        try:
            # 尝试解析为完整UUID
            return uuid.UUID(pair_id_str)
        except ValueError:
            # 如果是短字符串，生成一个基于它的确定性UUID
            # 使用 UUID5 以确保相同输入产生相同输出
            return uuid.uuid5(uuid.NAMESPACE_DNS, f"bilingual-pair-{pair_id_str}")
    
    if request.role == "source":
        ref_file.is_bilingual_source = True
        ref_file.is_bilingual_target = False
        ref_file.bilingual_pair_id = parse_pair_id(request.bilingual_pair_id)
    elif request.role == "target":
        ref_file.is_bilingual_source = False
        ref_file.is_bilingual_target = True
        ref_file.bilingual_pair_id = parse_pair_id(request.bilingual_pair_id)
    else:  # "none"
        ref_file.is_bilingual_source = False
        ref_file.is_bilingual_target = False
        ref_file.bilingual_pair_id = None
    
    db.commit()
    
    return {
        "id": str(ref_file.id),
        "is_bilingual_source": ref_file.is_bilingual_source,
        "is_bilingual_target": ref_file.is_bilingual_target,
        "bilingual_pair_id": str(ref_file.bilingual_pair_id) if ref_file.bilingual_pair_id else None,
    }


# ============ AI翻译功能 ============

class ReferenceAITranslateRequest(BaseModel):
    """基于参考文件的AI翻译请求"""
    scope: Literal["fuzzy_only", "none_only", "empty_target_only", "all", "all_with_exact"] = "empty_target_only"
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"
    model: Optional[str] = Field(default=None, max_length=120)
    translation_unit: Literal["paragraph", "sentence"] = "paragraph"
    temporary_prompt: str = ""


import logging
logger = logging.getLogger(__name__)


def _build_reference_translation_guidelines(
    profile_record: ReferenceProfile,
    temporary_prompt: str = "",
) -> str:
    """根据参考分析结果构建翻译指南"""
    parts: list[str] = []
    
    # 1. 深度分析报告
    if profile_record.analysis_report:
        try:
            analysis = json.loads(profile_record.analysis_report)
            
            # 行业领域
            industry = analysis.get("industry", "general")
            industry_labels = {
                "legal": "法律", "finance": "金融", "medical": "医疗",
                "tech": "科技", "marketing": "营销", "general": "通用"
            }
            parts.append(f"【行业领域】{industry_labels.get(industry, industry)}")
            
            # 翻译策略
            strategy = analysis.get("strategy", "balanced")
            strategy_labels = {
                "literal": "直译为主，保持原文结构",
                "free": "意译为主，追求流畅自然",
                "balanced": "灵活处理，根据语境选择"
            }
            parts.append(f"【翻译策略】{strategy_labels.get(strategy, strategy)}")
            
            # 策略建议
            if analysis.get("strategy_reasoning"):
                parts.append(f"【策略说明】{analysis['strategy_reasoning']}")
            
            # 客户风格
            if analysis.get("client_profile"):
                parts.append(f"【客户风格】{analysis['client_profile']}")
            
            # 品牌术语
            brand_terms = analysis.get("brand_terms", [])
            if brand_terms:
                term_lines = [f"  - {t['source']} → {t['target']}" for t in brand_terms[:20]]
                parts.append("【品牌/专有名词】必须按以下译法翻译：\n" + "\n".join(term_lines))
            
            # 易错点提示
            risk_points = analysis.get("risk_points", [])
            if risk_points:
                risk_lines = [f"  - [{r.get('category', '注意')}] {r['description']}" for r in risk_points[:10]]
                parts.append("【易错点提示】\n" + "\n".join(risk_lines))
            
            # 格式规范
            format_spec = analysis.get("format_spec", {})
            format_notes = []
            if format_spec.get("number_format"):
                format_notes.append(f"数字格式: {format_spec['number_format']}")
            if format_spec.get("date_format"):
                format_notes.append(f"日期格式: {format_spec['date_format']}")
            if format_spec.get("currency_format"):
                format_notes.append(f"货币格式: {format_spec['currency_format']}")
            if format_notes:
                parts.append("【格式规范】" + "；".join(format_notes))
                
        except (json.JSONDecodeError, TypeError):
            pass
    
    # 2. 风格指南
    if profile_record.style_guide:
        try:
            style = json.loads(profile_record.style_guide)
            style_parts = []
            if style.get("tone"):
                style_parts.append(f"语气: {style['tone']}")
            if style.get("person"):
                style_parts.append(f"人称: {style['person']}")
            if style.get("preferences"):
                style_parts.append(f"偏好: {', '.join(style['preferences'])}")
            if style.get("avoid"):
                style_parts.append(f"避免: {', '.join(style['avoid'])}")
            if style_parts:
                parts.append("【风格指南】" + "；".join(style_parts))
        except (json.JSONDecodeError, TypeError):
            pass
    
    # 注：术语表和翻译记忆已经被同步到项目级 GlossaryBase / TMCollection，
    # 翻译时通过原生通道（智能注入 / 模糊匹配改写）使用，这里不再重复注入到 prompt。
    
    # 5. 临时提示词
    if temporary_prompt.strip():
        parts.append(f"【本次临时提示词】{temporary_prompt.strip()}")
    
    return "\n\n".join(parts)


def _segment_metadata_layout_text(segment) -> str:
    """从句段 segment_metadata 取带行内格式标签的版式原文（PPTX 解析注入）。"""
    raw = getattr(segment, "segment_metadata", None)
    if not raw:
        return ""
    try:
        metadata = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("source_layout_text") or "")


def _build_reference_translation_tasks(
    db: Session,
    file_record_id: uuid.UUID,
    scope: str,
    source_language: Optional[str] = None,
    target_language: Optional[str] = None,
    include_context: bool = False,
) -> list[LLMTranslationTask]:
    """构建基于参考的翻译任务列表"""
    statuses_by_scope = {
        "fuzzy_only": {"fuzzy"},
        "none_only": {"none"},
        "all": {"fuzzy", "none"},
        "all_with_exact": {"exact", "fuzzy", "none"},
    }
    
    segments = db.execute(
        select(Segment).where(Segment.file_record_id == file_record_id).order_by(*SEGMENT_ORDERING)
    ).scalars().all()
    
    tasks: list[LLMTranslationTask] = []
    
    for segment in segments:
        should_translate = True
        
        if scope == "empty_target_only":
            # 只翻译没有译文的句段
            if (segment.target_text or "") != "":
                should_translate = False
        else:
            target_statuses = statuses_by_scope.get(scope, {"fuzzy", "none"})
            if segment.status not in target_statuses:
                should_translate = False
        
        source_layout_text = (
            _segment_metadata_layout_text(segment)
            or (segment.display_text or "")
        )
        tasks.append(LLMTranslationTask(
            sentence_id=segment.sentence_id,
            status=segment.status,
            source_text=segment.source_text or "",
            source_language=source_language,
            target_language=target_language,
            block_type=segment.block_type or "paragraph",
            block_index=segment.block_index or 0,
            row_index=segment.row_index,
            cell_index=segment.cell_index,
            source_layout_text=source_layout_text,
            matched_source_text=segment.matched_source_text,
            tm_target_text=None,  # 由参考翻译指南替代
            should_translate=should_translate,
        ))
    
    return tasks


def _sse_event(event: str, data: dict) -> str:
    """格式化SSE事件"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_segment_status(segment: Segment) -> str:
    """解析未确认句段的状态"""
    if segment.status == "confirmed":
        return "confirmed"
    if segment.score and segment.score >= 1.0:
        return "exact"
    if segment.score and segment.score >= 0.6:
        return "fuzzy"
    return "none"


@router.post("/file-records/{file_record_id}/ai-translate")
async def reference_ai_translate(
    file_record_id: str,
    request: Request,
    payload: Optional[ReferenceAITranslateRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    operation_token: Optional[str] = Header(default=None, alias=FILE_OPERATION_TOKEN_HEADER),
):
    """基于参考文件分析结果进行AI翻译
    
    使用参考文件中提取的术语、翻译记忆、风格指南和深度分析报告作为翻译指南，
    让大模型进行更精准的翻译。
    """
    # 验证文件记录存在
    file_record = db.execute(
        select(FileRecord).where(FileRecord.id == uuid.UUID(file_record_id))
    ).scalar_one_or_none()
    
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在")
    
    # 验证参考分析结果存在
    profile_record = db.execute(
        select(ReferenceProfile).where(
            ReferenceProfile.file_record_id == uuid.UUID(file_record_id)
        )
    ).scalar_one_or_none()
    
    if not profile_record:
        raise HTTPException(status_code=404, detail="未找到参考分析结果，请先上传并分析参考文件")
    
    # 检查是否有术语或翻译记忆
    has_terminology = profile_record.terminology and json.loads(profile_record.terminology)
    has_tm = profile_record.translation_memory and json.loads(profile_record.translation_memory)
    has_analysis = profile_record.analysis_report
    
    if not has_terminology and not has_tm and not has_analysis:
        raise HTTPException(status_code=400, detail="参考分析结果为空，请确保已正确分析参考文件")
    
    # 验证写入权限
    ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
    
    # 解析请求参数
    body = payload or ReferenceAITranslateRequest()
    requested_model = normalize_text(body.model or "") if body.model else None
    
    # 验证LLM配置
    try:
        validate_provider_choice(body.provider, model_override=requested_model)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    # 构建翻译指南
    guidelines = _build_reference_translation_guidelines(
        profile_record,
        body.temporary_prompt,
    )
    
    logger.info(f"[ReferenceAITranslate] 构建翻译指南，长度: {len(guidelines)} 字符")
    
    # 构建翻译任务
    source_language = file_record.source_language
    target_language = file_record.target_language
    
    translation_tasks = _build_reference_translation_tasks(
        db=db,
        file_record_id=uuid.UUID(file_record_id),
        scope=body.scope,
        source_language=source_language,
        target_language=target_language,
        include_context=body.translation_unit == "paragraph",
    )
    
    async def event_stream():
        updated_count = 0
        error_count = 0
        total_count = sum(1 for task in translation_tasks if task.should_translate)
        
        yield _sse_event("start", {
            "file_record_id": file_record_id,
            "scope": body.scope,
            "provider": body.provider,
            "model": requested_model,
            "translation_unit": body.translation_unit,
            "source_language": source_language,
            "target_language": target_language,
            "total": total_count,
            "guidelines_length": len(guidelines),
        })
        
        if total_count == 0:
            yield _sse_event("complete", {
                "file_record_id": file_record_id,
                "updated_count": 0,
                "error_count": 0,
                "total": 0,
            })
            return
        
        # 预加载所有句段
        all_segments = db.execute(
            select(Segment).where(Segment.file_record_id == uuid.UUID(file_record_id))
        ).scalars().all()
        seg_map = {s.sentence_id: s for s in all_segments}
        
        COMMIT_INTERVAL = 50
        uncommitted_count = 0
        
        async for result in iter_batch_translate(
            translation_tasks,
            provider=body.provider,
            translation_guidelines=guidelines,
            translation_unit=body.translation_unit,
            model_override=requested_model,
        ):
            if await request.is_disconnected():
                break
            
            if isinstance(result, LLMTranslationFailure):
                error_count += 1
                yield _sse_event("error", {
                    "sentence_id": result.sentence_id,
                    "status": result.status,
                    "message": result.error_message,
                })
                continue
            
            # 检查写入权限
            try:
                ensure_file_record_write_allowed(db, file_record, operation_token=operation_token)
            except Exception as exc:
                db.rollback()
                error_count += 1
                yield _sse_event("error", {
                    "sentence_id": result.sentence_id,
                    "status": result.status,
                    "message": f"数据库更新失败：{exc}",
                })
                continue
            
            segment = seg_map.get(result.sentence_id)
            if not segment:
                error_count += 1
                yield _sse_event("error", {
                    "sentence_id": result.sentence_id,
                    "status": result.status,
                    "message": "片段不存在，无法写回译文。",
                })
                continue
            
            try:
                before_text = segment.target_text
                # 拆分：纯译文入库 target_text，带标签版式译文单独存放供导出
                clean_translated_text, layout_translated_text = split_format_tagged_translation(
                    result.translated_text
                )
                segment.target_text = clean_translated_text
                set_segment_target_layout_text(segment, layout_translated_text)
                segment.target_html = None
                segment.source = "llm"
                segment.version = int(segment.version or 1) + 1
                segment.source_word_count = segment.source_word_count or count_source_words(segment.source_text)
                segment.llm_provider = result.provider
                segment.llm_model = result.model
                apply_segment_status(segment, _resolve_segment_status(segment))
                
                # 记录修订历史
                if (before_text or "") != (clean_translated_text or ""):
                    db.add(SegmentRevision(
                        file_record_id=uuid.UUID(file_record_id),
                        segment_id=segment.id,
                        sentence_id=segment.sentence_id,
                        before_text=before_text or "",
                        after_text=clean_translated_text or "",
                        source="llm",
                        status="pending",
                        author_id=current_user.id if current_user else None,
                    ))
                
                record_translation_metric_event(
                    db,
                    segment=segment,
                    before_text=before_text,
                    after_text=clean_translated_text,
                    source="llm",
                    current_user=current_user,
                )
                
                uncommitted_count += 1
                if uncommitted_count >= COMMIT_INTERVAL:
                    db.commit()
                    uncommitted_count = 0
                
                updated_count += 1
                yield _sse_event("progress", {
                    "sentence_id": result.sentence_id,
                    "status": result.status,
                    "translated_text": result.translated_text,
                    "provider": result.provider,
                    "model": result.model,
                    "updated_count": updated_count,
                    "error_count": error_count,
                    "total": total_count,
                })
                
            except Exception as exc:
                db.rollback()
                error_count += 1
                logger.exception(f"更新句段失败: {result.sentence_id}")
                yield _sse_event("error", {
                    "sentence_id": result.sentence_id,
                    "status": result.status,
                    "message": f"更新失败：{str(exc)}",
                })
        
        # 提交剩余更改
        if uncommitted_count > 0:
            db.commit()
        
        yield _sse_event("complete", {
            "file_record_id": file_record_id,
            "updated_count": updated_count,
            "error_count": error_count,
            "total": total_count,
        })
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
