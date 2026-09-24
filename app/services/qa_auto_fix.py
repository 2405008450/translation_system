"""安全 QA 规则的确定性修复。

仅处理答案唯一、可以通过文本区间替换完成的本地 QA 规则。修复前会校验
目标文本哈希；带格式译文只有在纯文本位置能够可靠映射到 HTML 文本节点时才修改。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from bs4 import BeautifulSoup, Comment, NavigableString
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment, SegmentQAIssue, User
from app.services.file_record_service import update_segment_by_sentence_id
from app.services.local_qa import (
    LOCAL_QA_DETECTORS,
    LOCAL_QA_PROVIDER_PUNCTUATION,
    check_segments_local_qa,
)
from app.services.spelling_grammar_qa import (
    QA_ISSUE_STATUS_OPEN,
    QA_ISSUE_STATUS_RESOLVED,
    QA_RULE_CONSECUTIVE_DUPLICATE_WORDS,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
    QA_RULE_MULTIPLE_SPACES,
    QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE,
    QA_RULE_REPEATED_PUNCTUATION,
    QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE,
    target_text_hash,
)

SAFE_QA_AUTO_FIX_RULE_KEYS: frozenset[str] = frozenset(
    {
        QA_RULE_REPEATED_PUNCTUATION,
        QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION,
        QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION,
        QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE,
        QA_RULE_MULTIPLE_SPACES,
        QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE,
        QA_RULE_CONSECUTIVE_DUPLICATE_WORDS,
    }
)

# 区间重叠时优先保留语义更具体的修复。例如句末连续空格应全部删除，
# 而不是被“多个空格”规则压缩成一个空格。
_RULE_PRIORITY = {
    QA_RULE_SEGMENT_TRAILING_EXTRA_SPACE: 100,
    QA_RULE_EXTRA_SPACE_AFTER_PUNCTUATION: 90,
    QA_RULE_MISSING_SPACE_AFTER_PUNCTUATION: 90,
    QA_RULE_PUNCTUATION_LEADING_EXTRA_SPACE: 90,
    QA_RULE_REPEATED_PUNCTUATION: 80,
    QA_RULE_CONSECUTIVE_DUPLICATE_WORDS: 80,
    QA_RULE_MULTIPLE_SPACES: 70,
}


@dataclass(frozen=True)
class QAAutoFixPatch:
    issue_id: UUID
    rule_key: str
    offset: int
    length: int
    replacement: str

    @property
    def end(self) -> int:
        return self.offset + self.length

    def signature(self) -> tuple[int, int, str]:
        return self.offset, self.length, self.replacement


@dataclass
class QAAutoFixResult:
    applied_issue_ids: list[UUID]
    skipped: list[dict[str, str]]
    updated_segment_ids: list[UUID]

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_count": len(self.applied_issue_ids),
            "applied_issue_ids": [str(value) for value in self.applied_issue_ids],
            "skipped_count": len(self.skipped),
            "skipped": self.skipped,
            "updated_segment_count": len(self.updated_segment_ids),
            "updated_segment_ids": [str(value) for value in self.updated_segment_ids],
        }


def _load_single_replacement(issue: SegmentQAIssue) -> tuple[bool, str]:
    try:
        replacements = json.loads(issue.replacements or "[]")
    except (TypeError, ValueError):
        return False, ""
    if not isinstance(replacements, list) or len(replacements) != 1:
        return False, ""
    replacement = replacements[0]
    if not isinstance(replacement, str):
        return False, ""
    return True, replacement


def _matches_current_detector(
    issue: SegmentQAIssue,
    segment: Segment,
    replacement: str,
) -> bool:
    if issue.provider != LOCAL_QA_PROVIDER_PUNCTUATION:
        return False
    detector = LOCAL_QA_DETECTORS.get(issue.rule_key)
    if detector is None:
        return False
    file_record = getattr(segment, "file_record", None)
    try:
        current_issues = detector(
            segment.source_text or "",
            segment.target_text or "",
            segment.source_html or "",
            segment.target_html or "",
            target_language=(getattr(file_record, "target_language", "") or ""),
            source_language=(getattr(file_record, "source_language", "") or ""),
            rule_settings=None,
            project_context=None,
        )
    except Exception:
        return False
    return any(
        current.rule_key == issue.rule_key
        and current.rule_id == issue.rule_id
        and current.offset == int(issue.offset or 0)
        and current.length == int(issue.length or 0)
        and current.replacements == (replacement,)
        for current in current_issues
    )


def _build_patch(issue: SegmentQAIssue, segment: Segment) -> tuple[QAAutoFixPatch | None, str]:
    if issue.status != QA_ISSUE_STATUS_OPEN:
        return None, "问题不是待处理状态"
    if issue.rule_key not in SAFE_QA_AUTO_FIX_RULE_KEYS:
        return None, "该规则不支持自动修改"

    current_text = segment.target_text or ""
    text_hash_matches = issue.target_text_hash == target_text_hash(current_text)

    offset = int(issue.offset or 0)
    length = int(issue.length or 0)
    if offset < 0 or length <= 0 or offset + length > len(current_text):
        return None, "修改位置已失效"

    has_replacement, replacement = _load_single_replacement(issue)
    if not has_replacement:
        return None, "没有唯一的修改建议"
    if not _matches_current_detector(issue, segment, replacement):
        if not text_hash_matches:
            return None, "译文已变更，请重新生成 QA 结果"
        return None, "修改建议与当前 QA 检测结果不一致"

    return (
        QAAutoFixPatch(
            issue_id=issue.id,
            rule_key=issue.rule_key,
            offset=offset,
            length=length,
            replacement=replacement,
        ),
        "",
    )


def _visible_html_text_nodes(soup: BeautifulSoup) -> list[NavigableString]:
    nodes: list[NavigableString] = []
    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            continue
        parent_name = (getattr(node.parent, "name", "") or "").lower()
        if parent_name in {"script", "style"}:
            continue
        nodes.append(node)
    return nodes


def _apply_patches_to_html(
    target_html: str | None,
    original_text: str,
    new_text: str,
    patches: list[QAAutoFixPatch],
) -> tuple[bool, str | None]:
    if not target_html:
        return True, None
    if target_html == original_text:
        return True, new_text

    soup = BeautifulSoup(target_html, "html.parser")
    if str(soup) != target_html:
        return False, target_html
    nodes = _visible_html_text_nodes(soup)
    if "".join(str(node) for node in nodes) != original_text:
        return False, target_html

    ranges: list[tuple[int, int, NavigableString]] = []
    cursor = 0
    for node in nodes:
        value = str(node)
        ranges.append((cursor, cursor + len(value), node))
        cursor += len(value)

    patches_by_node: dict[int, list[tuple[int, int, str]]] = {}
    node_by_id: dict[int, NavigableString] = {}
    for patch in patches:
        matched = False
        for start, end, node in ranges:
            if patch.offset >= start and patch.end <= end:
                node_id = id(node)
                node_by_id[node_id] = node
                patches_by_node.setdefault(node_id, []).append(
                    (patch.offset - start, patch.length, patch.replacement)
                )
                matched = True
                break
        if not matched:
            return False, target_html

    for node_id, node_patches in patches_by_node.items():
        node = node_by_id[node_id]
        value = str(node)
        for offset, length, replacement in sorted(node_patches, reverse=True):
            value = value[:offset] + replacement + value[offset + length :]
        node.replace_with(NavigableString(value))

    updated_nodes = _visible_html_text_nodes(soup)
    if "".join(str(node) for node in updated_nodes) != new_text:
        return False, target_html
    return True, str(soup)


def _apply_text_patches(text: str, patches: list[QAAutoFixPatch]) -> str:
    result = text
    for patch in sorted(patches, key=lambda value: value.offset, reverse=True):
        result = result[: patch.offset] + patch.replacement + result[patch.end :]
    return result


def build_qa_auto_fix_preview(issue: SegmentQAIssue, segment: Segment | None) -> dict[str, Any]:
    """返回工作台展示所需的自动修复能力与修正后译文。"""
    base = {
        "can_auto_fix": False,
        "fixed_target_text": "",
        "fix_offset": None,
        "fix_length": None,
        "fix_replacement": "",
        "fix_unavailable_reason": "",
    }
    if segment is None:
        base["fix_unavailable_reason"] = "对应句段不存在"
        return base

    patch, reason = _build_patch(issue, segment)
    if patch is None:
        base["fix_unavailable_reason"] = reason
        return base

    original_text = segment.target_text or ""
    fixed_text = _apply_text_patches(original_text, [patch])
    if fixed_text == "":
        base["fix_unavailable_reason"] = "自动修改不能将整段译文清空"
        return base
    html_ok, _ = _apply_patches_to_html(
        segment.target_html,
        original_text,
        fixed_text,
        [patch],
    )
    if not html_ok:
        base["fix_unavailable_reason"] = "译文格式复杂，请定位后手动修改"
        return base

    base.update(
        {
            "can_auto_fix": True,
            "fixed_target_text": fixed_text,
            "fix_offset": patch.offset,
            "fix_length": patch.length,
            "fix_replacement": patch.replacement,
        }
    )
    return base


def _patches_overlap(left: QAAutoFixPatch, right: QAAutoFixPatch) -> bool:
    return not (left.end <= right.offset or right.end <= left.offset)


def _select_non_conflicting_patches(
    candidates: list[QAAutoFixPatch],
) -> tuple[list[QAAutoFixPatch], list[UUID], list[dict[str, str]]]:
    accepted: list[QAAutoFixPatch] = []
    applied_issue_ids: list[UUID] = []
    skipped: list[dict[str, str]] = []
    signatures: dict[tuple[int, int, str], QAAutoFixPatch] = {}

    ordered = sorted(
        candidates,
        key=lambda patch: (-_RULE_PRIORITY.get(patch.rule_key, 0), patch.offset, patch.length),
    )
    for patch in ordered:
        if patch.signature() in signatures:
            applied_issue_ids.append(patch.issue_id)
            continue
        if any(_patches_overlap(patch, current) for current in accepted):
            skipped.append({"issue_id": str(patch.issue_id), "reason": "修改区间与其他问题冲突"})
            continue
        accepted.append(patch)
        signatures[patch.signature()] = patch
        applied_issue_ids.append(patch.issue_id)
    return accepted, applied_issue_ids, skipped


def apply_qa_auto_fixes(
    db: Session,
    issues: Iterable[SegmentQAIssue],
    current_user: User,
) -> QAAutoFixResult:
    """按句段合并并应用安全 QA 修复，每个句段只写入一次修订。"""
    normalized_issues = list({issue.id: issue for issue in issues}.values())
    grouped: dict[UUID, list[SegmentQAIssue]] = {}
    for issue in normalized_issues:
        grouped.setdefault(issue.segment_id, []).append(issue)

    applied_issue_ids: list[UUID] = []
    updated_segment_ids: list[UUID] = []
    skipped: list[dict[str, str]] = []
    file_by_id: dict[UUID, FileRecord] = {}

    for segment_id, segment_issues in grouped.items():
        segment = (
            db.query(Segment)
            .filter(Segment.id == segment_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if segment is None:
            skipped.extend(
                {"issue_id": str(issue.id), "reason": "对应句段不存在"}
                for issue in segment_issues
            )
            continue

        candidates: list[QAAutoFixPatch] = []
        for issue in segment_issues:
            patch, reason = _build_patch(issue, segment)
            if patch is None:
                skipped.append({"issue_id": str(issue.id), "reason": reason})
                continue
            candidates.append(patch)
        if not candidates:
            continue

        patches, candidate_issue_ids, conflict_skips = _select_non_conflicting_patches(candidates)
        all_candidate_issue_ids = [patch.issue_id for patch in candidates]
        if not patches:
            skipped.extend(conflict_skips)
            continue

        original_text = segment.target_text or ""
        new_text = _apply_text_patches(original_text, patches)
        if new_text == original_text or new_text == "":
            reason = (
                "自动修改不能将整段译文清空"
                if new_text == ""
                else "修改后译文没有变化"
            )
            skipped.extend(
                {"issue_id": str(issue_id), "reason": reason}
                for issue_id in all_candidate_issue_ids
            )
            continue

        html_ok, new_html = _apply_patches_to_html(
            segment.target_html,
            original_text,
            new_text,
            patches,
        )
        if not html_ok:
            skipped.extend(
                {"issue_id": str(issue_id), "reason": "译文格式复杂，请定位后手动修改"}
                for issue_id in all_candidate_issue_ids
            )
            continue

        updated = update_segment_by_sentence_id(
            db=db,
            file_record_id=segment.file_record_id,
            sentence_id=segment.sentence_id,
            target_text=new_text,
            target_html=new_html,
            source="manual",
            current_user=current_user,
            track_revision=True,
        )
        if updated is None:
            skipped.extend(
                {"issue_id": str(issue_id), "reason": "对应句段不存在"}
                for issue_id in all_candidate_issue_ids
            )
            continue

        file_record = file_by_id.get(updated.file_record_id)
        if file_record is None:
            file_record = (
                db.query(FileRecord).filter(FileRecord.id == updated.file_record_id).first()
            )
            if file_record is not None:
                file_by_id[updated.file_record_id] = file_record
        if file_record is not None:
            check_segments_local_qa(db, file_record=file_record, segments=[updated])

        for conflict in conflict_skips:
            conflict_issue = db.get(SegmentQAIssue, UUID(conflict["issue_id"]))
            if conflict_issue is not None:
                db.refresh(conflict_issue)
            if conflict_issue is not None and conflict_issue.status == QA_ISSUE_STATUS_RESOLVED:
                candidate_issue_ids.append(conflict_issue.id)
            else:
                skipped.append(conflict)

        applied_issue_ids.extend(candidate_issue_ids)
        updated_segment_ids.append(updated.id)

    return QAAutoFixResult(
        applied_issue_ids=applied_issue_ids,
        skipped=skipped,
        updated_segment_ids=updated_segment_ids,
    )
