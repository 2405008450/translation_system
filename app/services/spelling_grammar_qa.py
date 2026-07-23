from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import FileRecord, Project, Segment, SegmentQAIssue
from app.services.cache import aget_json, aset_json, get_json
from app.services.language_pairs import LANGUAGE_OPTIONS
from app.services.normalizer import normalize_text

logger = logging.getLogger(__name__)

QA_RULE_SPELLING_GRAMMAR = "spelling_grammar"
QA_RULE_TARGET_WITHOUT_TAG = "target_without_tag"
QA_RULE_TARGET_TAG_MISSING = "target_tag_missing"
QA_RULE_UNMATCHED_CLOSING_TAG = "unmatched_closing_tag"
QA_RULE_UNMATCHED_OPENING_TAG = "unmatched_opening_tag"
QA_RULE_TARGET_PLACEHOLDER_MISSING = "target_placeholder_missing"
QA_RULE_TERM_INCONSISTENCY = "term_inconsistency"
QA_RULE_PAIRED_PUNCTUATION_MISSING = "paired_punctuation_missing"
QA_RULE_ENDING_PUNCTUATION_MISMATCH = "ending_punctuation_mismatch"
QA_RULE_REPEATED_PUNCTUATION = "repeated_punctuation"
QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION = "extra_space_after_punctuation"
QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION = "missing_space_after_punctuation"
QA_ISSUE_STATUS_OPEN = "open"
QA_ISSUE_STATUS_IGNORED = "ignored"
QA_ISSUE_STATUS_RESOLVED = "resolved"
QUALITY_QA_SEVERITIES = {"low", "medium", "high"}

QUALITY_QA_RULE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"key": QA_RULE_TARGET_WITHOUT_TAG, "default_enabled": True},
    {"key": QA_RULE_TARGET_TAG_MISSING, "default_enabled": True},
    {"key": QA_RULE_UNMATCHED_CLOSING_TAG, "default_enabled": True},
    {"key": QA_RULE_UNMATCHED_OPENING_TAG, "default_enabled": True},
    {"key": QA_RULE_TARGET_PLACEHOLDER_MISSING, "default_enabled": True},
    {"key": QA_RULE_SPELLING_GRAMMAR, "default_enabled": True},
    {"key": QA_RULE_TERM_INCONSISTENCY, "default_enabled": True},
    {"key": QA_RULE_PAIRED_PUNCTUATION_MISSING, "default_enabled": False},
    {"key": QA_RULE_ENDING_PUNCTUATION_MISMATCH, "default_enabled": False},
    {"key": QA_RULE_REPEATED_PUNCTUATION, "default_enabled": False},
    {"key": QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION, "default_enabled": False},
    {"key": QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION, "default_enabled": False},
)

DEFAULT_QUALITY_QA_SETTINGS: dict[str, Any] = {
    "rules": {
        str(rule["key"]): {"enabled": bool(rule["default_enabled"])}
        for rule in QUALITY_QA_RULE_DEFINITIONS
    },
    "spelling_grammar": {
        "enabled": True,
        "severity": "medium",
    }
}
DEFAULT_QUALITY_QA_SETTINGS["rules"][QA_RULE_TERM_INCONSISTENCY]["case_sensitive"] = False

LANGUAGETOOL_LANGUAGE_MAP: dict[str, str] = {
    "zh-CN": "zh-CN",
    "zh-TW": "zh-TW",
    "zh-HK": "zh-HK",
    "zh-MO": "zh-HK",
    "en-US": "en-US",
    "en-GB": "en-GB",
    "ja-JP": "ja-JP",
    "fr-FR": "fr",
    "de-DE": "de-DE",
    "es-ES": "es",
    "es-419": "es",
    "pt-BR": "pt-BR",
    "pt-PT": "pt-PT",
    "it-IT": "it",
    "ru-RU": "ru-RU",
    "ar-SA": "ar",
    # 预留常见 LanguageTool 支持语言，便于后续扩展语言列表。
    "da-DK": "da-DK",
    "nl-NL": "nl",
    "el-GR": "el-GR",
    "km-KH": "km-KH",
    "pl-PL": "pl-PL",
    "ro-RO": "ro-RO",
    "sk-SK": "sk-SK",
    "sl-SI": "sl-SI",
    "sv-SE": "sv-SE",
    "uk-UA": "uk-UA",
}


class LanguageToolUnavailableError(RuntimeError):
    pass


class LanguageToolBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanedLanguageToolIssue:
    language: str
    severity: str
    message: str
    short_message: str
    rule_id: str
    rule_category: str
    issue_type: str
    context_text: str
    offset: int
    length: int
    replacements: list[str]

    def fingerprint(self, target_text_hash: str) -> tuple[str, str, int, int, str]:
        return (
            target_text_hash,
            self.rule_id,
            self.offset,
            self.length,
            self.message,
        )


_live_check_semaphore: asyncio.Semaphore | None = None
_live_check_semaphore_limit: int | None = None


def _get_live_check_semaphore() -> asyncio.Semaphore:
    """返回当前 Web worker 内共享的实时检查并发闸门。"""
    global _live_check_semaphore, _live_check_semaphore_limit
    settings = get_settings()
    limit = max(1, int(settings.languagetool_live_max_concurrency_per_worker or 1))
    if _live_check_semaphore is None or _live_check_semaphore_limit != limit:
        _live_check_semaphore = asyncio.Semaphore(limit)
        _live_check_semaphore_limit = limit
    return _live_check_semaphore


def _clone_default_quality_qa_settings() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_QUALITY_QA_SETTINGS))


def _normalize_rule_enabled(raw: Any, fallback: bool) -> bool:
    if isinstance(raw, dict):
        return bool(raw.get("enabled", fallback))
    if isinstance(raw, bool):
        return raw
    return fallback


def normalize_quality_qa_settings(raw: Any) -> dict[str, Any]:
    settings = _clone_default_quality_qa_settings()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}

    spelling_grammar = raw.get(QA_RULE_SPELLING_GRAMMAR)
    if isinstance(spelling_grammar, dict):
        enabled = bool(spelling_grammar.get("enabled", settings[QA_RULE_SPELLING_GRAMMAR]["enabled"]))
        settings[QA_RULE_SPELLING_GRAMMAR]["enabled"] = enabled
        settings["rules"][QA_RULE_SPELLING_GRAMMAR]["enabled"] = enabled
        severity = str(spelling_grammar.get("severity") or "medium").strip().lower()
        settings[QA_RULE_SPELLING_GRAMMAR]["severity"] = severity if severity in QUALITY_QA_SEVERITIES else "medium"

    rules = raw.get("rules")
    if isinstance(rules, dict):
        for definition in QUALITY_QA_RULE_DEFINITIONS:
            key = str(definition["key"])
            settings["rules"][key]["enabled"] = _normalize_rule_enabled(
                rules.get(key),
                bool(settings["rules"][key]["enabled"]),
            )
        term_inconsistency = rules.get(QA_RULE_TERM_INCONSISTENCY)
        if isinstance(term_inconsistency, dict):
            settings["rules"][QA_RULE_TERM_INCONSISTENCY]["case_sensitive"] = bool(
                term_inconsistency.get("case_sensitive", False)
            )

    settings[QA_RULE_SPELLING_GRAMMAR]["enabled"] = bool(
        settings["rules"][QA_RULE_SPELLING_GRAMMAR]["enabled"]
    )
    return settings


def load_quality_qa_settings(project: Project | None) -> dict[str, Any]:
    return normalize_quality_qa_settings(getattr(project, "quality_qa_settings", None))


def store_quality_qa_settings(project: Project, settings: Any) -> dict[str, Any]:
    normalized = normalize_quality_qa_settings(settings)
    project.quality_qa_settings = json.dumps(normalized, ensure_ascii=False)
    return normalized


def is_spelling_grammar_enabled(project: Project | None) -> bool:
    settings = load_quality_qa_settings(project)
    return bool(settings["rules"][QA_RULE_SPELLING_GRAMMAR]["enabled"])


def is_languagetool_configured() -> bool:
    return bool((get_settings().languagetool_base_url or "").strip())


def get_languagetool_language(app_language: str | None) -> str | None:
    if not app_language:
        return None
    return LANGUAGETOOL_LANGUAGE_MAP.get(app_language)


def get_supported_quality_qa_languages() -> list[dict[str, Any]]:
    known_codes = {option.code for option in LANGUAGE_OPTIONS}
    extra_codes = sorted(set(LANGUAGETOOL_LANGUAGE_MAP) - known_codes)
    rows: list[dict[str, Any]] = []
    for option in LANGUAGE_OPTIONS:
        lt_code = LANGUAGETOOL_LANGUAGE_MAP.get(option.code)
        rows.append({
            "code": option.code,
            "label": option.label,
            "languagetool_code": lt_code,
            "supported": bool(lt_code),
        })
    for code in extra_codes:
        rows.append({
            "code": code,
            "label": code,
            "languagetool_code": LANGUAGETOOL_LANGUAGE_MAP.get(code),
            "supported": True,
        })
    return rows


def target_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _live_spelling_cache_key(language: str, severity: str, text_hash: str) -> str:
    return f"languagetool:live:v1:{language}:{severity}:{text_hash}"


def serialize_segment_qa_issue(issue: SegmentQAIssue) -> dict[str, Any]:
    try:
        replacements = json.loads(issue.replacements or "[]")
        if not isinstance(replacements, list):
            replacements = []
    except json.JSONDecodeError:
        replacements = []
    return {
        "id": str(issue.id),
        "project_id": str(issue.project_id) if issue.project_id else None,
        "file_record_id": str(issue.file_record_id),
        "segment_id": str(issue.segment_id),
        "sentence_id": issue.sentence_id,
        "rule_key": issue.rule_key,
        "provider": issue.provider,
        "language": issue.language,
        "severity": issue.severity,
        "message": issue.message,
        "short_message": issue.short_message,
        "rule_id": issue.rule_id,
        "rule_category": issue.rule_category,
        "issue_type": issue.issue_type,
        "context_text": issue.context_text,
        "offset": issue.offset,
        "length": issue.length,
        "replacements": [str(value) for value in replacements],
        "target_text_hash": issue.target_text_hash,
        "status": issue.status,
        "ignored": issue.status == QA_ISSUE_STATUS_IGNORED,
        "ignored_at": issue.ignored_at.isoformat() if issue.ignored_at else None,
        "ignored_by_id": str(issue.ignored_by_id) if issue.ignored_by_id else None,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
    }


def load_open_segment_qa_issues_by_segment_id(
    db: Session,
    segment_ids: Iterable[UUID],
) -> dict[UUID, list[SegmentQAIssue]]:
    normalized_ids = list(dict.fromkeys(segment_ids))
    if not normalized_ids:
        return {}
    issues = (
        db.query(SegmentQAIssue)
        .filter(
            SegmentQAIssue.segment_id.in_(normalized_ids),
            SegmentQAIssue.status == QA_ISSUE_STATUS_OPEN,
        )
        .order_by(SegmentQAIssue.offset.asc(), SegmentQAIssue.created_at.asc())
        .all()
    )
    grouped: dict[UUID, list[SegmentQAIssue]] = {segment_id: [] for segment_id in normalized_ids}
    for issue in issues:
        grouped.setdefault(issue.segment_id, []).append(issue)
    return grouped


class LanguageToolClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        resolved_base_url = (base_url or settings.languagetool_base_url or "").strip().rstrip("/")
        if not resolved_base_url:
            raise LanguageToolUnavailableError("LanguageTool base URL is not configured.")
        self.check_url = resolved_base_url if resolved_base_url.endswith("/check") else f"{resolved_base_url}/check"
        self.timeout_seconds = timeout_seconds or settings.languagetool_timeout_seconds

    def check(self, *, text: str, language: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self.check_url,
                data={
                    "text": text,
                    "language": language,
                },
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}


LanguageToolCheckFn = Callable[[str, str], dict[str, Any]]


def clean_languagetool_matches(
    payload: dict[str, Any],
    *,
    text: str,
    language: str,
    severity: str,
) -> list[CleanedLanguageToolIssue]:
    matches = payload.get("matches") if isinstance(payload, dict) else []
    if not isinstance(matches, list):
        return []

    cleaned: list[CleanedLanguageToolIssue] = []
    text_length = len(text)
    for match in matches:
        if not isinstance(match, dict):
            continue
        try:
            offset = max(0, int(match.get("offset") or 0))
            length = max(0, int(match.get("length") or 0))
        except (TypeError, ValueError):
            continue
        if length <= 0 or offset >= text_length:
            continue
        length = min(length, text_length - offset)
        rule = match.get("rule") if isinstance(match.get("rule"), dict) else {}
        category = rule.get("category") if isinstance(rule.get("category"), dict) else {}
        replacements = match.get("replacements")
        replacement_values: list[str] = []
        if isinstance(replacements, list):
            for item in replacements[:8]:
                if isinstance(item, dict):
                    value = normalize_text(str(item.get("value") or ""))
                    if value:
                        replacement_values.append(value)
        context = match.get("context") if isinstance(match.get("context"), dict) else {}
        message = normalize_text(str(match.get("message") or ""))
        rule_id = normalize_text(str(rule.get("id") or "LANGUAGETOOL_RULE"))
        cleaned.append(
            CleanedLanguageToolIssue(
                language=language,
                severity=severity,
                message=message,
                short_message=normalize_text(str(match.get("shortMessage") or "")),
                rule_id=rule_id[:120],
                rule_category=normalize_text(str(category.get("id") or category.get("name") or ""))[:120],
                issue_type=normalize_text(str(rule.get("issueType") or ""))[:80],
                context_text=normalize_text(str(context.get("text") or "")),
                offset=offset,
                length=length,
                replacements=replacement_values,
            )
        )
    return cleaned


def is_spelling_issue(issue: CleanedLanguageToolIssue) -> bool:
    """只挑出适合在编辑器中显示红色波浪线的拼写问题。"""
    return _is_spelling_issue_fields(issue.issue_type, issue.rule_id)


def _is_spelling_issue_fields(issue_type: str | None, rule_id: str | None) -> bool:
    issue_type = (issue_type or "").strip().lower()
    if issue_type == "misspelling":
        return True
    # 兼容部分旧版/第三方规则没有正确填写 issueType 的情况。
    rule_id = (rule_id or "").strip().upper()
    return any(marker in rule_id for marker in ("MORFOLOGIK_RULE", "HUNSPELL", "SPELLER_RULE"))


def serialize_live_spelling_issue(
    issue: CleanedLanguageToolIssue,
    *,
    text_hash: str,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(
        f"{text_hash}\0{issue.rule_id}\0{issue.offset}\0{issue.length}\0{issue.message}".encode("utf-8")
    ).hexdigest()[:24]
    return {
        "id": f"live-{fingerprint}",
        "language": issue.language,
        "severity": issue.severity,
        "message": issue.message,
        "short_message": issue.short_message,
        "rule_id": issue.rule_id,
        "rule_category": issue.rule_category,
        "issue_type": issue.issue_type,
        "context_text": issue.context_text,
        "offset": issue.offset,
        "length": issue.length,
        "replacements": issue.replacements,
        "target_text_hash": text_hash,
        "status": QA_ISSUE_STATUS_OPEN,
    }


async def check_live_spelling_text(
    *,
    text: str,
    language: str,
    severity: str = "medium",
) -> dict[str, Any]:
    """检查一段未保存译文，只返回拼写问题且不写数据库。"""
    settings = get_settings()
    resolved_base_url = (settings.languagetool_base_url or "").strip().rstrip("/")
    if not resolved_base_url:
        raise LanguageToolUnavailableError("LanguageTool base URL is not configured.")

    max_length = max(1, int(settings.languagetool_max_text_length or 20000))
    checked_text = (text or "")[:max_length]
    text_hash = target_text_hash(text or "")
    if not normalize_text(checked_text):
        return {
            "language": language,
            "target_text_hash": text_hash,
            "checked_length": len(checked_text),
            "truncated": len(text or "") > len(checked_text),
            "cached": False,
            "issues": [],
        }

    cache_key = _live_spelling_cache_key(language, severity, text_hash)
    cached = await aget_json(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("issues"), list):
        return {**cached, "cached": True}

    semaphore = _get_live_check_semaphore()
    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=max(0.01, float(settings.languagetool_live_queue_timeout_seconds or 0.3)),
        )
    except TimeoutError as exc:
        raise LanguageToolBusyError("LanguageTool live check is busy.") from exc

    try:
        check_url = resolved_base_url if resolved_base_url.endswith("/check") else f"{resolved_base_url}/check"
        timeout = max(0.1, float(settings.languagetool_live_timeout_seconds or 2.5))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                check_url,
                data={"text": checked_text, "language": language},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LanguageToolUnavailableError("LanguageTool live check failed.") from exc
    finally:
        semaphore.release()

    cleaned = clean_languagetool_matches(
        payload if isinstance(payload, dict) else {},
        text=checked_text,
        language=language,
        severity=severity,
    )
    result = {
        "language": language,
        "target_text_hash": text_hash,
        "checked_length": len(checked_text),
        "truncated": len(text or "") > len(checked_text),
        "cached": False,
        "issues": [
            serialize_live_spelling_issue(issue, text_hash=text_hash)
            for issue in cleaned
            if is_spelling_issue(issue)
        ],
    }
    await aset_json(
        cache_key,
        result,
        ttl_seconds=max(1, int(settings.languagetool_live_cache_ttl_seconds or 300)),
    )
    return result


def _existing_issue_fingerprint(issue: SegmentQAIssue) -> tuple[str, str, int, int, str]:
    return (
        issue.target_text_hash or "",
        issue.rule_id or "",
        int(issue.offset or 0),
        int(issue.length or 0),
        issue.message or "",
    )


def _apply_cleaned_issues(
    db: Session,
    *,
    file_record: FileRecord,
    project: Project | None,
    segment: Segment,
    text_hash: str,
    language: str,
    cleaned_issues: list[CleanedLanguageToolIssue],
    spelling_only: bool = False,
    touch_segment: bool = True,
) -> bool:
    existing_issues = (
        db.query(SegmentQAIssue)
        .filter(
            SegmentQAIssue.segment_id == segment.id,
            SegmentQAIssue.rule_key == QA_RULE_SPELLING_GRAMMAR,
        )
        .all()
    )
    managed_existing_issues = [
        issue
        for issue in existing_issues
        if not spelling_only or _is_spelling_issue_fields(issue.issue_type, issue.rule_id)
    ]
    existing_by_fingerprint = {
        _existing_issue_fingerprint(issue): issue
        for issue in managed_existing_issues
    }
    next_fingerprints = {issue.fingerprint(text_hash) for issue in cleaned_issues}
    changed = False
    now = datetime.now()

    for existing in managed_existing_issues:
        if _existing_issue_fingerprint(existing) not in next_fingerprints and existing.status != QA_ISSUE_STATUS_RESOLVED:
            existing.status = QA_ISSUE_STATUS_RESOLVED
            existing.updated_at = now
            changed = True

    for cleaned in cleaned_issues:
        fingerprint = cleaned.fingerprint(text_hash)
        existing = existing_by_fingerprint.get(fingerprint)
        if existing is None:
            db.add(
                SegmentQAIssue(
                    project_id=getattr(project, "id", None),
                    file_record_id=file_record.id,
                    segment_id=segment.id,
                    sentence_id=segment.sentence_id,
                    rule_key=QA_RULE_SPELLING_GRAMMAR,
                    provider="languagetool",
                    language=language,
                    severity=cleaned.severity,
                    message=cleaned.message,
                    short_message=cleaned.short_message,
                    rule_id=cleaned.rule_id,
                    rule_category=cleaned.rule_category,
                    issue_type=cleaned.issue_type,
                    context_text=cleaned.context_text,
                    offset=cleaned.offset,
                    length=cleaned.length,
                    replacements=json.dumps(cleaned.replacements, ensure_ascii=False),
                    target_text_hash=text_hash,
                    status=QA_ISSUE_STATUS_OPEN,
                )
            )
            changed = True
            continue

        if existing.status == QA_ISSUE_STATUS_RESOLVED:
            existing.status = QA_ISSUE_STATUS_OPEN
            changed = True
        existing.language = language
        existing.severity = cleaned.severity
        existing.short_message = cleaned.short_message
        existing.rule_category = cleaned.rule_category
        existing.issue_type = cleaned.issue_type
        existing.context_text = cleaned.context_text
        existing.replacements = json.dumps(cleaned.replacements, ensure_ascii=False)
        existing.updated_at = now

    if changed and touch_segment:
        segment.updated_at = now
    return changed


def _deserialize_cached_live_spelling_issues(
    payload: Any,
    *,
    expected_text_hash: str,
    language: str,
    severity: str,
) -> list[CleanedLanguageToolIssue] | None:
    """把实时检查缓存还原为可写入 QA 表的拼写问题。

    返回 None 表示缓存不存在或不属于当前译文；空列表表示检查成功且没有拼写问题。
    """
    if not isinstance(payload, dict) or payload.get("target_text_hash") != expected_text_hash:
        return None
    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        return None

    issues: list[CleanedLanguageToolIssue] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        try:
            offset = max(0, int(raw.get("offset") or 0))
            length = max(0, int(raw.get("length") or 0))
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue
        raw_replacements = raw.get("replacements")
        replacements = (
            [str(value) for value in raw_replacements if str(value).strip()]
            if isinstance(raw_replacements, list)
            else []
        )
        issue = CleanedLanguageToolIssue(
            language=str(raw.get("language") or language),
            severity=str(raw.get("severity") or severity),
            message=str(raw.get("message") or ""),
            short_message=str(raw.get("short_message") or ""),
            rule_id=str(raw.get("rule_id") or ""),
            rule_category=str(raw.get("rule_category") or ""),
            issue_type=str(raw.get("issue_type") or ""),
            context_text=str(raw.get("context_text") or ""),
            offset=offset,
            length=length,
            replacements=replacements,
        )
        if is_spelling_issue(issue):
            issues.append(issue)
    return issues


def apply_cached_live_spelling_issues(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
) -> int:
    """复用实时拼写检查缓存写入 QA 表，不再请求 LanguageTool。"""
    project = file_record.project or (
        db.query(Project).filter(Project.id == file_record.project_id).first()
        if file_record.project_id
        else None
    )
    if not project or not is_spelling_grammar_enabled(project):
        return 0

    language = get_languagetool_language(file_record.target_language)
    if not language:
        return 0
    qa_settings = load_quality_qa_settings(project)
    severity = str(qa_settings[QA_RULE_SPELLING_GRAMMAR].get("severity") or "medium")
    changed_count = 0

    for segment in segments:
        text = segment.target_text or ""
        text_hash = target_text_hash(text)
        if normalize_text(text):
            cached = get_json(_live_spelling_cache_key(language, severity, text_hash))
            cleaned = _deserialize_cached_live_spelling_issues(
                cached,
                expected_text_hash=text_hash,
                language=language,
                severity=severity,
            )
            if cleaned is None:
                continue
        else:
            # 清空译文时不会发起实时请求，但应将旧拼写问题标记为已解决。
            cleaned = []

        if _apply_cleaned_issues(
            db,
            file_record=file_record,
            project=project,
            segment=segment,
            text_hash=text_hash,
            language=language,
            cleaned_issues=cleaned,
            spelling_only=True,
            touch_segment=False,
        ):
            changed_count += 1

    if changed_count:
        db.commit()
    return changed_count


def persist_cached_live_spelling_issues_for_segment_ids(
    file_record_id: UUID,
    segment_ids: list[UUID],
) -> int:
    """在译文保存后持久化已完成的实时拼写检查结果。"""
    ids = list(dict.fromkeys(segment_ids))
    if not ids:
        return 0
    with SessionLocal() as db:
        file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
        if not file_record:
            return 0
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.id.in_(ids),
            )
            .all()
        )
        return apply_cached_live_spelling_issues(
            db,
            file_record=file_record,
            segments=segments,
        )


def check_segments_with_languagetool(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
    check_text: LanguageToolCheckFn | None = None,
) -> int:
    project = file_record.project or (
        db.query(Project).filter(Project.id == file_record.project_id).first()
        if file_record.project_id
        else None
    )
    if not project or not is_spelling_grammar_enabled(project):
        return 0

    lt_language = get_languagetool_language(file_record.target_language)
    if not lt_language:
        return 0

    if check_text is None:
        try:
            client = LanguageToolClient()
        except LanguageToolUnavailableError:
            return 0

        def check_text(text: str, language: str) -> dict[str, Any]:
            return client.check(text=text, language=language)

    settings = get_settings()
    qa_settings = load_quality_qa_settings(project)
    severity = qa_settings[QA_RULE_SPELLING_GRAMMAR]["severity"]
    max_length = max(1, int(settings.languagetool_max_text_length or 20000))
    changed_count = 0

    for segment in segments:
        target_text = segment.target_text or ""
        if not normalize_text(target_text):
            changed_count += int(_apply_cleaned_issues(
                db,
                file_record=file_record,
                project=project,
                segment=segment,
                text_hash=target_text_hash(target_text),
                language=lt_language,
                cleaned_issues=[],
            ))
            continue

        checked_text = target_text[:max_length]
        checked_hash = target_text_hash(target_text)
        try:
            payload = check_text(checked_text, lt_language)
        except Exception:
            logger.exception(
                "LanguageTool check failed file_record_id=%s segment_id=%s",
                file_record.id,
                segment.id,
            )
            continue
        cleaned = clean_languagetool_matches(
            payload,
            text=checked_text,
            language=lt_language,
            severity=severity,
        )
        if _apply_cleaned_issues(
            db,
            file_record=file_record,
            project=project,
            segment=segment,
            text_hash=checked_hash,
            language=lt_language,
            cleaned_issues=cleaned,
        ):
            changed_count += 1

    if changed_count:
        db.commit()
    return changed_count


def run_spelling_grammar_qa_for_segment_ids(file_record_id: UUID, segment_ids: list[UUID]) -> int:
    if not segment_ids:
        return 0
    with SessionLocal() as db:
        file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
        if not file_record:
            return 0
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.id.in_(list(dict.fromkeys(segment_ids))),
            )
            .all()
        )
        return check_segments_with_languagetool(db, file_record=file_record, segments=segments)


def run_spelling_grammar_qa_for_project(project_id: UUID) -> int:
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or not is_spelling_grammar_enabled(project):
            return 0
        files = db.query(FileRecord).filter(FileRecord.project_id == project_id).all()
        changed_count = 0
        for file_record in files:
            segments = (
                db.query(Segment)
                .filter(Segment.file_record_id == file_record.id)
                .all()
            )
            changed_count += check_segments_with_languagetool(db, file_record=file_record, segments=segments)
        return changed_count


def schedule_spelling_grammar_qa(
    background_tasks: Any,
    file_record_id: UUID,
    segment_ids: Iterable[UUID],
) -> None:
    ids = list(dict.fromkeys(segment_ids))
    if not ids or background_tasks is None:
        return
    background_tasks.add_task(run_spelling_grammar_qa_for_segment_ids, file_record_id, ids)
