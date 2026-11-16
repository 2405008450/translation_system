"""供字数统计系统复用的翻译解析接口。"""
import hmac
import json
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from app.config import get_settings
from app.services.quotation_statistics import parse_quotation, PARSER_VERSION
from app.services.quotation_counter import COUNT_POLICY_VERSION
router = APIRouter(prefix="/internal/quotation", tags=["quotation"])

def verify_service(authorization: str = Header(default="")):
    token = get_settings().quotation_service_token
    if not token:
        raise HTTPException(503, "报价解析服务尚未配置凭据")
    if not hmac.compare_digest(authorization, "Bearer " + token):
        raise HTTPException(401, "无效的服务凭据")

@router.get("/config", dependencies=[Depends(verify_service)])
def configuration():
    from app.services.document_workspace import normalize_document_parse_options
    return dict(parser_version=PARSER_VERSION, count_policy_version=COUNT_POLICY_VERSION,
                parse_options=normalize_document_parse_options(), extensions=[".docx", ".xlsx", ".pptx", ".txt", ".pdf"])

@router.post("/parse", dependencies=[Depends(verify_service)])
def parse(file: UploadFile = File(...), options: str = Form("{}"), source_language: str = Form("")):
    try:
        parsed_options = json.loads(options)
        if not isinstance(parsed_options, dict):
            raise ValueError("解析选项必须是对象")
        maximum = get_settings().upload_max_size_mb * 1024 * 1024
        content = file.file.read(maximum + 1)
        if len(content) > maximum:
            raise HTTPException(413, "文件超过解析服务大小限制")
        return parse_quotation(content, file.filename or "source", parsed_options, source_language)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, detail="翻译文本解析失败：" + str(exc)) from exc
