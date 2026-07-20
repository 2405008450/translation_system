import uuid

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

UUID_SQL_DEFAULT = text(
    """(
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid"""
)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    document_parse_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="full",
        server_default=text("'full'"),
    )
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deadline: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    access_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="team", server_default=text("'team'")
    )
    translation_guidelines: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    quality_qa_settings: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    auto_tm_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[creator_id]
    )
    file_records: Mapped[list["FileRecord"]] = relationship(
        "FileRecord",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    issue_markers: Mapped[list["IssueMarker"]] = relationship(
        "IssueMarker",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    assignments: Mapped[list["ProjectAssignment"]] = relationship(
        "ProjectAssignment",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    workflow_steps: Mapped[list["ProjectWorkflowStep"]] = relationship(
        "ProjectWorkflowStep",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectWorkflowStep.sort_order",
    )
    merge_views: Mapped[list["ProjectMergeView"]] = relationship(
        "ProjectMergeView",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectMergeView.created_at",
    )


class ProjectMergeView(Base):
    """项目"合并视图"：记录同一项目中哪些 file_records 组成一个编辑视图。

    仅持久化分组关系（name + 有序 file_ids），不为合并单独存储句段——
    句段仍通过 file_record_id 归属各自文件，保存/导出复用按文件的现有接口。
    """

    __tablename__ = "project_merge_views"
    __table_args__ = (
        Index("ix_project_merge_views_project_id", "project_id"),
        Index("ix_project_merge_views_creator_id", "creator_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="merge_views")
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[creator_id])


class ProjectWorkflowStep(Base):
    __tablename__ = "project_workflow_steps"
    __table_args__ = (
        Index("ix_project_workflow_steps_project_id", "project_id"),
        Index("ix_project_workflow_steps_project_order", "project_id", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    step_type: Mapped[str] = mapped_column(String(20), nullable=False, default="custom")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="workflow_steps")
    segments: Mapped[list["Segment"]] = relationship("Segment", back_populates="workflow_step")
    file_assignments: Mapped[list["FileAssignment"]] = relationship(
        "FileAssignment",
        back_populates="workflow_step",
    )


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    document_parse_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="full",
        server_default=text("'full'"),
    )
    document_parse_options: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    document_statistics: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    active_operation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_operation_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_operation_updated_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    active_operation_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memory_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    collection_ids_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    tm_match_threshold: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
        server_default=text("0.8"),
    )
    tm_scope_mode: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="selected",
        server_default=text("'selected'"),
    )
    tm_match_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tm_last_matched_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    term_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("term_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    term_base_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    term_base_write_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    qa_term_base_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    glossary_base_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    deadline: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    access_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="team", server_default=text("'team'")
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[creator_id]
    )
    active_operation_user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[active_operation_user_id]
    )
    assignee: Mapped["User | None"] = relationship(
        "User", foreign_keys=[assignee_id]
    )
    assigned_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[assigned_by_id]
    )
    project: Mapped["Project | None"] = relationship(
        "Project", back_populates="file_records"
    )
    collection: Mapped["TMCollection | None"] = relationship(
        "TMCollection", foreign_keys=[collection_id]
    )
    term_base: Mapped["TermBase | None"] = relationship(
        "TermBase", foreign_keys=[term_base_id]
    )
    segments: Mapped[list["Segment"]] = relationship(
        "Segment",
        back_populates="file_record",
        foreign_keys="Segment.file_record_id",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list["SegmentComment"]] = relationship(
        "SegmentComment",
        back_populates="file_record",
        cascade="all, delete-orphan",
    )
    issue_markers: Mapped[list["IssueMarker"]] = relationship(
        "IssueMarker",
        back_populates="file_record",
        passive_deletes=True,
    )
    revisions: Mapped[list["SegmentRevision"]] = relationship(
        "SegmentRevision",
        back_populates="file_record",
        cascade="all, delete-orphan",
    )
    revision_display_setting: Mapped["RevisionDisplaySetting | None"] = relationship(
        "RevisionDisplaySetting",
        back_populates="file_record",
        cascade="all, delete-orphan",
        uselist=False,
    )
    qa_issues: Mapped[list["SegmentQAIssue"]] = relationship(
        "SegmentQAIssue",
        back_populates="file_record",
        cascade="all, delete-orphan",
    )
    file_assignments: Mapped[list["FileAssignment"]] = relationship(
        "FileAssignment",
        back_populates="file_record",
        passive_deletes=True,
    )


class FileExportTask(Base):
    __tablename__ = "file_export_tasks"
    __table_args__ = (
        Index("ix_file_export_tasks_file_record_type", "file_record_id", "export_type"),
        Index("ix_file_export_tasks_status", "status"),
        Index("ix_file_export_tasks_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_type: Mapped[str] = mapped_column(String(40), nullable=False, default="original")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    result_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=False), nullable=False)

    file_record: Mapped["FileRecord"] = relationship("FileRecord")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])


class PretranslationRun(Base):
    __tablename__ = "pretranslation_runs"
    __table_args__ = (
        Index("ix_pretranslation_runs_project_id", "project_id"),
        Index("ix_pretranslation_runs_status", "status"),
        Index("ix_pretranslation_runs_created_by_id", "created_by_id"),
        Index("ix_pretranslation_runs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default=text("'queued'"))
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    canceled_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship("Project")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    tasks: Mapped[list["PretranslationTask"]] = relationship(
        "PretranslationTask",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class PretranslationTask(Base):
    __tablename__ = "pretranslation_tasks"
    __table_args__ = (
        Index("ix_pretranslation_tasks_run_id", "run_id"),
        Index("ix_pretranslation_tasks_file_record_id", "file_record_id"),
        Index("ix_pretranslation_tasks_file_status", "file_record_id", "status"),
        Index("ix_pretranslation_tasks_status", "status"),
        Index("ix_pretranslation_tasks_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pretranslation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default=text("'queued'"))
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", server_default=text("'queued'"))
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    unique_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    deduplicated_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    processed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    current_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_heartbeat_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    run: Mapped["PretranslationRun"] = relationship("PretranslationRun", back_populates="tasks")
    file_record: Mapped["FileRecord"] = relationship("FileRecord")


class DocumentStatisticsReport(Base):
    __tablename__ = "document_statistics_reports"
    __table_args__ = (
        Index("ix_document_statistics_reports_project_id", "project_id"),
        Index("ix_document_statistics_reports_created_by_id", "created_by_id"),
        Index("ix_document_statistics_reports_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    available_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    totals: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", server_default=text("'completed'"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    items: Mapped[list["DocumentStatisticsReportItem"]] = relationship(
        "DocumentStatisticsReportItem",
        back_populates="report",
        cascade="all, delete-orphan",
    )


class DocumentStatisticsReportItem(Base):
    __tablename__ = "document_statistics_report_items"
    __table_args__ = (
        Index("ix_document_statistics_report_items_report_id", "report_id"),
        Index("ix_document_statistics_report_items_project_id", "project_id"),
        Index("ix_document_statistics_report_items_file_record_id", "file_record_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_statistics_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    statistics: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    report: Mapped["DocumentStatisticsReport"] = relationship(
        "DocumentStatisticsReport",
        back_populates="items",
    )
    project: Mapped["Project"] = relationship("Project")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    translator_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default=text("'internal'")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    comments: Mapped[list["SegmentComment"]] = relationship("SegmentComment", back_populates="author")
    issue_markers: Mapped[list["IssueMarker"]] = relationship(
        "IssueMarker",
        foreign_keys="IssueMarker.reporter_id",
        back_populates="reporter",
    )
    resolved_issue_markers: Mapped[list["IssueMarker"]] = relationship(
        "IssueMarker",
        foreign_keys="IssueMarker.resolved_by_id",
        back_populates="resolved_by",
    )


class GuidelineTemplate(Base):
    __tablename__ = "guideline_templates"
    __table_args__ = (
        Index("ix_guideline_templates_updated_at", "updated_at"),
        Index("ix_guideline_templates_created_by_id", "created_by_id"),
        Index("ix_guideline_templates_last_modified_by_id", "last_modified_by_id"),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default=text("''"))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    source_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_modified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    last_modified_by: Mapped["User | None"] = relationship("User", foreign_keys=[last_modified_by_id])


class ProjectAssignment(Base):
    __tablename__ = "project_assignments"
    __table_args__ = (
        Index("ix_project_assignments_project_id", "project_id"),
        Index("ix_project_assignments_assignee_id", "assignee_id"),
        Index("ix_project_assignments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )

    project: Mapped["Project"] = relationship("Project", back_populates="assignments")
    assignee: Mapped["User"] = relationship("User", foreign_keys=[assignee_id])
    assigned_by: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_by_id])
    revoked_by: Mapped["User | None"] = relationship("User", foreign_keys=[revoked_by_id])


class FileAssignment(Base):
    __tablename__ = "file_assignments"
    __table_args__ = (
        Index("ix_file_assignments_project_id", "project_id"),
        Index("ix_file_assignments_file_record_id", "file_record_id"),
        Index("ix_file_assignments_assignee_id", "assignee_id"),
        Index("ix_file_assignments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("project_workflow_steps.id", ondelete="CASCADE"),
        nullable=True,
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    segment_range_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    segment_range_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    file_record: Mapped["FileRecord"] = relationship("FileRecord", back_populates="file_assignments")
    project: Mapped["Project"] = relationship("Project")
    workflow_step: Mapped["ProjectWorkflowStep | None"] = relationship(
        "ProjectWorkflowStep",
        back_populates="file_assignments",
    )
    assignee: Mapped["User"] = relationship("User", foreign_keys=[assignee_id])
    assigned_by: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_by_id])
    revoked_by: Mapped["User | None"] = relationship("User", foreign_keys=[revoked_by_id])


class AssignmentEvent(Base):
    __tablename__ = "assignment_events"
    __table_args__ = (
        Index("ix_assignment_events_project_id", "project_id"),
        Index("ix_assignment_events_file_record_id", "file_record_id"),
        Index("ix_assignment_events_assignee_id", "assignee_id"),
        Index("ix_assignment_events_actor_id", "actor_id"),
        Index("ix_assignment_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    before_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    after_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship("Project")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")
    assignee: Mapped["User"] = relationship("User", foreign_keys=[assignee_id])
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_id])


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_read_at", "read_at"),
        Index("ix_notifications_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    related_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assignment_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    read_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")
    related_event: Mapped["AssignmentEvent | None"] = relationship("AssignmentEvent")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        Index("ix_segments_file_record_id", "file_record_id"),
        Index("ix_segments_source_hash", "source_hash"),
        Index("ix_segments_file_source_hash", "file_record_id", "source_hash"),
        Index(
            "ix_segments_file_record_order",
            "file_record_id",
            "block_index",
            "row_index",
            "cell_index",
            "sentence_id",
        ),
        Index(
            "ix_segments_file_record_sequence_order",
            "file_record_id",
            "block_index",
            "row_index",
            "cell_index",
            "sequence_index",
            "sentence_id",
        ),
        Index("ix_segments_file_record_status", "file_record_id", "status"),
        Index("ix_segments_file_record_source", "file_record_id", "source"),
        Index("ix_segments_file_display_index", "file_record_id", "display_index"),
        Index("ix_segments_file_updated_at_id", "file_record_id", "updated_at", "id"),
        Index("ix_segments_last_modified_by_id", "last_modified_by_id"),
        Index("ix_segments_project_sync_source_segment_id", "project_sync_source_segment_id"),
        Index("ix_segments_project_sync_source_file_record_id", "project_sync_source_file_record_id"),
        Index("ix_segments_updated_at", "updated_at"),
        Index(
            "ix_segments_source_text_trgm",
            "source_text",
            postgresql_using="gin",
            postgresql_ops={"source_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_segments_display_text_trgm",
            "display_text",
            postgresql_using="gin",
            postgresql_ops={"display_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_segments_target_text_trgm",
            "target_text",
            postgresql_using="gin",
            postgresql_ops={"target_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("project_workflow_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    project_sync_disabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    project_sync_source_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_sync_source_file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    matched_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_collection_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    matched_creator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    matched_created_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    matched_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="tm")
    source_word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_type: Mapped[str] = mapped_column(String(20), nullable=False, default="paragraph")
    block_index: Mapped[int] = mapped_column(nullable=False, default=0)
    row_index: Mapped[int | None] = mapped_column(nullable=True)
    cell_index: Mapped[int | None] = mapped_column(nullable=True)
    # 句段在源文件中的权威顺序。sentence_id 仅用于身份标识，不能再参与位置推断。
    # -1 表示迁移前的历史数据，导出时继续使用源文档对齐兜底。
    sequence_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=-1,
        server_default=text("-1"),
    )
    segment_metadata: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    # 文档内显示序号（0 起）。-1 表示待回填，读取端会自动刷新。
    display_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=-1,
        server_default=text("-1"),
    )
    # 最近一次人工确认时间；取消确认后清空。项目同步冲突用其决胜。
    confirmed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    file_record: Mapped["FileRecord"] = relationship(
        "FileRecord",
        back_populates="segments",
        foreign_keys=[file_record_id],
    )
    last_modified_by: Mapped["User | None"] = relationship("User", foreign_keys=[last_modified_by_id])
    workflow_step: Mapped["ProjectWorkflowStep | None"] = relationship(
        "ProjectWorkflowStep",
        back_populates="segments",
    )
    comments: Mapped[list["SegmentComment"]] = relationship("SegmentComment", back_populates="segment")
    revisions: Mapped[list["SegmentRevision"]] = relationship(
        "SegmentRevision",
        back_populates="segment",
        cascade="all, delete-orphan",
    )
    qa_issues: Mapped[list["SegmentQAIssue"]] = relationship(
        "SegmentQAIssue",
        back_populates="segment",
        cascade="all, delete-orphan",
    )


class TermQAReport(Base):
    __tablename__ = "term_qa_reports"
    __table_args__ = (
        Index("ix_term_qa_reports_project_id", "project_id"),
        Index("ix_term_qa_reports_file_record_id", "file_record_id"),
        Index("ix_term_qa_reports_created_by_id", "created_by_id"),
        Index("ix_term_qa_reports_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="project")
    file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    term_base_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    language_pairs: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    checked_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", server_default=text("'completed'"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    items: Mapped[list["TermQAReportItem"]] = relationship(
        "TermQAReportItem",
        back_populates="report",
        cascade="all, delete-orphan",
    )


class TermQAReportItem(Base):
    __tablename__ = "term_qa_report_items"
    __table_args__ = (
        Index("ix_term_qa_report_items_report_id", "report_id"),
        Index("ix_term_qa_report_items_project_id", "project_id"),
        Index("ix_term_qa_report_items_file_record_id", "file_record_id"),
        Index("ix_term_qa_report_items_segment_id", "segment_id"),
        Index("ix_term_qa_report_items_term_base_id", "term_base_id"),
        Index("ix_term_qa_report_items_ignored_at", "ignored_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("term_qa_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    term_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("term_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    term_base_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_term: Mapped[str] = mapped_column(Text, nullable=False)
    expected_target_term: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    block_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cell_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ignored_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ignored_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    report: Mapped["TermQAReport"] = relationship("TermQAReport", back_populates="items")
    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord"] = relationship("FileRecord")
    segment: Mapped["Segment | None"] = relationship("Segment")
    term_base: Mapped["TermBase | None"] = relationship("TermBase")
    ignored_by: Mapped["User | None"] = relationship("User", foreign_keys=[ignored_by_id])


class NumberCheckReport(Base):
    __tablename__ = "number_check_reports"
    __table_args__ = (
        Index("ix_number_check_reports_project_id", "project_id"),
        Index("ix_number_check_reports_file_record_id", "file_record_id"),
        Index("ix_number_check_reports_created_by_id", "created_by_id"),
        Index("ix_number_check_reports_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="file")
    file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    checked_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    program_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    ai_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    source_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    ai_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", server_default=text("'completed'"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])
    items: Mapped[list["NumberCheckReportItem"]] = relationship(
        "NumberCheckReportItem",
        back_populates="report",
        cascade="all, delete-orphan",
    )


class NumberCheckReportItem(Base):
    __tablename__ = "number_check_report_items"
    __table_args__ = (
        Index("ix_number_check_report_items_report_id", "report_id"),
        Index("ix_number_check_report_items_project_id", "project_id"),
        Index("ix_number_check_report_items_file_record_id", "file_record_id"),
        Index("ix_number_check_report_items_segment_id", "segment_id"),
        Index("ix_number_check_report_items_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("number_check_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    target_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    source_numbers: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    target_numbers: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    error_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    ai_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    ai_is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    ai_errors: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    ai_source_issues: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    replace_anchor: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    suggested_value: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    is_source_consistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    ai_error_status: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default=text("''"))
    original_target_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    applied_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default=text("'open'"))
    ignored_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ignored_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cell_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    report: Mapped["NumberCheckReport"] = relationship("NumberCheckReport", back_populates="items")
    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord"] = relationship("FileRecord")
    segment: Mapped["Segment | None"] = relationship("Segment")
    ignored_by: Mapped["User | None"] = relationship("User", foreign_keys=[ignored_by_id])


class SegmentQAIssue(Base):
    __tablename__ = "segment_qa_issues"
    __table_args__ = (
        Index("ix_segment_qa_issues_project_id", "project_id"),
        Index("ix_segment_qa_issues_file_record_id", "file_record_id"),
        Index("ix_segment_qa_issues_segment_id", "segment_id"),
        Index("ix_segment_qa_issues_segment_rule_status", "segment_id", "rule_key", "status"),
        Index("ix_segment_qa_issues_status", "status"),
        Index("ix_segment_qa_issues_rule_key", "rule_key"),
        Index("ix_segment_qa_issues_target_hash", "target_text_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    rule_key: Mapped[str] = mapped_column(String(40), nullable=False, default="spelling_grammar")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="languagetool")
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    short_message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    rule_category: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    issue_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    context_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    replacements: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    target_text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default=text("'open'"))
    ignored_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ignored_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped["Project | None"] = relationship("Project")
    file_record: Mapped["FileRecord"] = relationship("FileRecord", back_populates="qa_issues")
    segment: Mapped["Segment"] = relationship("Segment", back_populates="qa_issues")
    ignored_by: Mapped["User | None"] = relationship("User", foreign_keys=[ignored_by_id])


class SegmentRevision(Base):
    __tablename__ = "segment_revisions"
    __table_args__ = (
        Index("ix_segment_revisions_file_record_id", "file_record_id"),
        Index("ix_segment_revisions_segment_id", "segment_id"),
        Index("ix_segment_revisions_sentence_id", "sentence_id"),
        Index("ix_segment_revisions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    after_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )

    file_record: Mapped["FileRecord"] = relationship(
        "FileRecord",
        back_populates="revisions",
    )
    segment: Mapped["Segment"] = relationship(
        "Segment",
        back_populates="revisions",
    )
    author: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[author_id],
    )
    resolved_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[resolved_by_id],
    )


class RevisionDisplaySetting(Base):
    __tablename__ = "revision_display_settings"
    __table_args__ = (
        UniqueConstraint("file_record_id", name="uq_revision_display_settings_file_record_id"),
        Index("ix_revision_display_settings_updated_by_id", "updated_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    show_author_time: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("TRUE"),
    )
    show_others_revisions: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("TRUE"),
    )
    default_insert_color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="#2563eb",
        server_default=text("'#2563eb'"),
    )
    default_delete_color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="#dc2626",
        server_default=text("'#dc2626'"),
    )
    author_colors: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    file_record: Mapped["FileRecord"] = relationship(
        "FileRecord",
        back_populates="revision_display_setting",
    )
    updated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[updated_by_id],
    )


class TranslationMetricEvent(Base):
    __tablename__ = "translation_metric_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_translation_metric_events_event_key"),
        Index("ix_translation_metric_events_created_at", "created_at"),
        Index("ix_translation_metric_events_source", "source"),
        Index("ix_translation_metric_events_language_pair", "source_language", "target_language"),
        Index("ix_translation_metric_events_file_record_id", "file_record_id"),
        Index("ix_translation_metric_events_segment_id", "segment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    event_key: Mapped[str | None] = mapped_column(String(140), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    target_was_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("TRUE"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    project: Mapped["Project | None"] = relationship("Project", foreign_keys=[project_id])
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord", foreign_keys=[file_record_id])
    segment: Mapped["Segment | None"] = relationship("Segment", foreign_keys=[segment_id])
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id])


class UserActivityDaily(Base):
    __tablename__ = "user_activity_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_user_activity_daily_user_date"),
        Index("ix_user_activity_daily_activity_date", "activity_date"),
        Index("ix_user_activity_daily_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_date: Mapped[Date] = mapped_column(Date, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    first_seen_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class SegmentComment(Base):
    __tablename__ = "segment_comments"
    __table_args__ = (
        Index("ix_segment_comments_file_record_id", "file_record_id"),
        Index("ix_segment_comments_segment_id", "segment_id"),
        Index("ix_segment_comments_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    anchor_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="sentence")
    range_start_offset: Mapped[int | None] = mapped_column(nullable=True)
    range_end_offset: Mapped[int | None] = mapped_column(nullable=True)
    anchor_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segment_comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )

    file_record: Mapped["FileRecord"] = relationship("FileRecord", back_populates="comments")
    segment: Mapped["Segment | None"] = relationship("Segment", back_populates="comments")
    author: Mapped["User"] = relationship("User", back_populates="comments")
    parent: Mapped["SegmentComment | None"] = relationship(
        "SegmentComment",
        remote_side=lambda: SegmentComment.id,
        back_populates="replies",
    )
    replies: Mapped[list["SegmentComment"]] = relationship(
        "SegmentComment",
        back_populates="parent",
        cascade="all, delete-orphan",
    )


class IssueMarker(Base):
    __tablename__ = "issue_markers"
    __table_args__ = (
        Index("ix_issue_markers_project_id", "project_id"),
        Index("ix_issue_markers_file_record_id", "file_record_id"),
        Index("ix_issue_markers_status", "status"),
        Index("ix_issue_markers_reporter_id", "reporter_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="other", server_default=text("'other'"))
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default=text("'medium'"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default=text("'open'"))
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="issue_markers")
    file_record: Mapped["FileRecord | None"] = relationship("FileRecord", back_populates="issue_markers")
    reporter: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reporter_id],
        back_populates="issue_markers",
    )
    resolved_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[resolved_by_id],
        back_populates="resolved_issue_markers",
    )


class ResourceImportBatch(Base):
    __tablename__ = "resource_import_batches"
    __table_args__ = (
        Index("ix_resource_import_batches_resource", "resource_type", "resource_id"),
        Index("ix_resource_import_batches_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    file_format: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default=text("''"))
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tmx_header_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])


class TMCollection(Base):
    __tablename__ = "memory_bases"
    __table_args__ = (
        Index("uq_memory_bases_name", "name", unique=True),
        Index("ix_memory_bases_language_pair", "source_language", "target_language"),
        Index("ix_memory_bases_project_id", "project_id"),
        Index("ix_memory_bases_origin", "origin"),
        Index("ix_memory_bases_creator_id", "creator_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    origin: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="manual",
        server_default=text("'manual'"),
    )
    # 持久化条目总数，由数据库语句级触发器维护（见迁移 0015）。
    entry_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    translation_memories: Mapped[list["TranslationMemory"]] = relationship(
        "TranslationMemory",
        back_populates="collection",
    )
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[creator_id])


class TranslationMemory(Base):
    __tablename__ = "memory_entries"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "source_hash",
            "source_language",
            "target_language",
            name="uq_memory_entries_collection_source_hash_language_pair",
        ),
        Index("ix_memory_entries_collection_id", "collection_id"),
        Index("ix_memory_entries_collection_source_hash", "collection_id", "source_hash"),
        Index(
            "ix_memory_entries_collection_source_normalized",
            "collection_id",
            "source_normalized",
        ),
        Index("ix_memory_entries_source_hash", "source_hash"),
        Index("ix_memory_entries_source_text", "source_text"),
        Index("ix_memory_entries_source_normalized", "source_normalized"),
        Index("ix_memory_entries_language_pair", "source_language", "target_language"),
        Index(
            "ix_memory_entries_collection_language_pair",
            "collection_id",
            "source_language",
            "target_language",
        ),
        Index("ix_memory_entries_creator_id", "creator_id"),
        Index("ix_memory_entries_last_modified_by_id", "last_modified_by_id"),
        Index("ix_memory_entries_external_tuid", "external_tuid"),
        Index("ix_memory_entries_import_batch_id", "import_batch_id"),
        Index(
            "ix_memory_entries_source_text_trgm",
            "source_text",
            postgresql_using="gin",
            postgresql_ops={"source_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_memory_entries_source_normalized_trgm",
            "source_normalized",
            postgresql_using="gin",
            postgresql_ops={"source_normalized": "gin_trgm_ops"},
        ),
        Index(
            "ix_memory_entries_lang_collection_source_normalized_trgm",
            "source_language",
            "target_language",
            "collection_id",
            "source_normalized",
            postgresql_using="gin",
            postgresql_ops={"source_normalized": "gin_trgm_ops"},
            postgresql_where=text("source_normalized IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memory_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_modified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_tuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    tmx_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resource_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    collection: Mapped[TMCollection | None] = relationship(
        "TMCollection",
        back_populates="translation_memories",
    )
    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[creator_id]
    )
    last_modified_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[last_modified_by_id]
    )
    import_batch: Mapped["ResourceImportBatch | None"] = relationship(
        "ResourceImportBatch",
        foreign_keys=[import_batch_id],
    )


MemoryBase = TMCollection
MemoryEntry = TranslationMemory


class AutoTMOutbox(Base):
    __tablename__ = "auto_tm_outbox"
    __table_args__ = (
        UniqueConstraint(
            "file_record_id",
            "segment_id",
            "collection_id",
            name="uq_auto_tm_outbox_file_segment_collection",
        ),
        Index("ix_auto_tm_outbox_status_created_at", "status", "created_at"),
        Index("ix_auto_tm_outbox_file_record_id", "file_record_id"),
        Index("ix_auto_tm_outbox_collection_id", "collection_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
    )
    sentence_id: Mapped[str] = mapped_column(String(100), nullable=False)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memory_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_language: Mapped[str] = mapped_column(String(20), nullable=False)
    target_language: Mapped[str] = mapped_column(String(20), nullable=False)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    last_enqueued_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FileSegmentStats(Base):
    """文件级句段统计，由数据库语句级触发器维护（见迁移 0017）。

    应用侧只读；缺行按全零处理（文件尚无句段或历史环境未回填）。
    """

    __tablename__ = "file_segment_stats"

    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        primary_key=True,
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    exact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    fuzzy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    none_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    empty_target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProjectSegmentSyncOutbox(Base):
    """项目重复句段同步 outbox：同一 (项目, 语言对, source_hash) 的任务合并去重。"""

    __tablename__ = "project_segment_sync_outbox"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_language",
            "target_language",
            "source_hash",
            name="uq_project_sync_outbox_scope",
        ),
        Index("ix_project_sync_outbox_status_enqueued", "status", "last_enqueued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_language: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default=text("''"))
    target_language: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default=text("''"))
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    last_enqueued_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AutoTMRematchQueue(Base):
    __tablename__ = "auto_tm_rematch_queue"
    __table_args__ = (
        UniqueConstraint("file_record_id", name="uq_auto_tm_rematch_queue_file_record"),
        Index("ix_auto_tm_rematch_queue_status", "status"),
        Index("ix_auto_tm_rematch_queue_first_pending_at", "first_pending_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memory_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    pending_entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default=text("'pending'"))
    first_pending_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_pending_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TermBase(Base):
    __tablename__ = "term_bases"
    __table_args__ = (
        Index("uq_term_bases_name", "name", unique=True),
        Index("ix_term_bases_language_pair", "source_language", "target_language"),
        Index("ix_term_bases_creator_id", "creator_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str] = mapped_column(String(20), nullable=False)
    target_language: Mapped[str] = mapped_column(String(20), nullable=False)
    # 持久化条目总数，由数据库语句级触发器维护（见迁移 0020）。
    entry_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    term_entries: Mapped[list["TermEntry"]] = relationship(
        "TermEntry",
        back_populates="term_base",
        cascade="all, delete-orphan",
    )
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[creator_id])


class TermEntry(Base):
    __tablename__ = "term_entries"
    __table_args__ = (
        Index("ix_term_entries_term_base_id", "term_base_id"),
        Index("ix_term_entries_term_base_source_text", "term_base_id", "source_text"),
        Index(
            "ix_term_entries_term_base_source_normalized",
            "term_base_id",
            "source_normalized",
        ),
        Index("ix_term_entries_language_pair", "source_language", "target_language"),
        Index("ix_term_entries_creator_id", "creator_id"),
        Index("ix_term_entries_last_modified_by_id", "last_modified_by_id"),
        Index("ix_term_entries_external_tuid", "external_tuid"),
        Index("ix_term_entries_import_batch_id", "import_batch_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    term_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("term_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str] = mapped_column(String(20), nullable=False)
    target_language: Mapped[str] = mapped_column(String(20), nullable=False)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_modified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_tuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    tmx_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resource_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    term_base: Mapped[TermBase] = relationship(
        "TermBase",
        back_populates="term_entries",
    )
    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[creator_id]
    )
    last_modified_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[last_modified_by_id]
    )
    import_batch: Mapped["ResourceImportBatch | None"] = relationship(
        "ResourceImportBatch",
        foreign_keys=[import_batch_id],
    )


class GlossaryBase(Base):
    __tablename__ = "glossary_bases"
    __table_args__ = (
        Index("uq_glossary_bases_name", "name", unique=True),
        Index("ix_glossary_bases_language_pair", "source_language", "target_language"),
        Index("ix_glossary_bases_project_id", "project_id"),
        Index("ix_glossary_bases_origin", "origin"),
        Index("ix_glossary_bases_creator_id", "creator_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str] = mapped_column(String(20), nullable=False)
    target_language: Mapped[str] = mapped_column(String(20), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    origin: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="manual",
        server_default=text("'manual'"),
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    entries: Mapped[list["GlossaryEntry"]] = relationship(
        "GlossaryEntry",
        back_populates="glossary_base",
        cascade="all, delete-orphan",
    )
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[creator_id])


class GlossaryEntry(Base):
    __tablename__ = "glossary_entries"
    __table_args__ = (
        Index("ix_glossary_entries_glossary_base_id", "glossary_base_id"),
        Index("ix_glossary_entries_base_source_text", "glossary_base_id", "source_text"),
        Index(
            "ix_glossary_entries_base_source_normalized",
            "glossary_base_id",
            "source_normalized",
        ),
        Index("ix_glossary_entries_language_pair", "source_language", "target_language"),
        Index("ix_glossary_entries_creator_id", "creator_id"),
        Index("ix_glossary_entries_last_modified_by_id", "last_modified_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    glossary_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("glossary_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_language: Mapped[str] = mapped_column(String(20), nullable=False)
    target_language: Mapped[str] = mapped_column(String(20), nullable=False)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_modified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    glossary_base: Mapped[GlossaryBase] = relationship(
        "GlossaryBase",
        back_populates="entries",
    )
    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[creator_id]
    )
    last_modified_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[last_modified_by_id]
    )


# ================ 参考分析相关模型 ================

class ReferenceProfile(Base):
    """参考文件分析结果"""
    __tablename__ = "reference_profiles"
    __table_args__ = (
        Index("ix_reference_profiles_file_record_id", "file_record_id"),
        Index("ix_reference_profiles_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    file_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    glossary_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("glossary_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    memory_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("memory_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_files: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    terminology: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    translation_memory: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    style_guide: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_confidence: Mapped[float] = mapped_column(
        nullable=False, default=0.0, server_default=text("0.0")
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    file_record: Mapped["FileRecord | None"] = relationship("FileRecord")
    reference_files: Mapped[list["ReferenceFile"]] = relationship(
        "ReferenceFile",
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class ReferenceFile(Base):
    """上传的参考文件记录"""
    __tablename__ = "reference_files"
    __table_args__ = (
        Index("ix_reference_files_profile_id", "profile_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reference_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_bilingual_source: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_bilingual_target: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    bilingual_pair_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    profile: Mapped["ReferenceProfile"] = relationship(
        "ReferenceProfile", back_populates="reference_files"
    )
