import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func, text
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
    term_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("term_bases.id", ondelete="SET NULL"),
        nullable=True,
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


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (Index("ix_segments_file_record_id", "file_record_id"),)

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
    sentence_id: Mapped[str] = mapped_column(String(20), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    matched_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_collection_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    matched_creator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    matched_created_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    matched_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="tm")
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
    comments: Mapped[list["SegmentComment"]] = relationship("SegmentComment", back_populates="segment")
    revisions: Mapped[list["SegmentRevision"]] = relationship(
        "SegmentRevision",
        back_populates="segment",
        cascade="all, delete-orphan",
    )


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
