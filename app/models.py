import uuid

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func, text
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
    sentence_id: Mapped[str] = mapped_column(String(20), nullable=False)
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
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    matched_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_collection_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    matched_creator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    matched_created_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    matched_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="tm")
    source_word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    block_type: Mapped[str] = mapped_column(String(20), nullable=False, default="paragraph")
    block_index: Mapped[int] = mapped_column(nullable=False, default=0)
    row_index: Mapped[int | None] = mapped_column(nullable=True)
    cell_index: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    file_record: Mapped["FileRecord"] = relationship("FileRecord", back_populates="segments")
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
    sentence_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
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
    sentence_id: Mapped[str] = mapped_column(String(20), nullable=False)
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


class TMCollection(Base):
    __tablename__ = "memory_bases"
    __table_args__ = (
        Index("uq_memory_bases_name", "name", unique=True),
        Index("ix_memory_bases_language_pair", "source_language", "target_language"),
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
    sentence_id: Mapped[str] = mapped_column(String(20), nullable=False)
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


class GlossaryBase(Base):
    __tablename__ = "glossary_bases"
    __table_args__ = (
        Index("uq_glossary_bases_name", "name", unique=True),
        Index("ix_glossary_bases_language_pair", "source_language", "target_language"),
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
