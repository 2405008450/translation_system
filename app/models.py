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


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
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


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    comments: Mapped[list["SegmentComment"]] = relationship("SegmentComment", back_populates="author")


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


class TMCollection(Base):
    __tablename__ = "tm_collections"
    __table_args__ = (
        Index("uq_tm_collections_name", "name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class TermbaseCollection(Base):
    __tablename__ = "termbase_collections"
    __table_args__ = (
        Index("uq_termbase_collections_name", "name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=UUID_SQL_DEFAULT,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    terms: Mapped[list["Term"]] = relationship(
        "Term",
        back_populates="collection",
    )


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (
        Index("ix_terms_collection_id", "collection_id"),
        Index("ix_terms_source_text", "source_text"),
        Index(
            "ix_terms_source_text_trgm",
            "source_text",
            postgresql_using="gin",
            postgresql_ops={"source_text": "gin_trgm_ops"},
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
        ForeignKey("termbase_collections.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    collection: Mapped[TermbaseCollection | None] = relationship(
        "TermbaseCollection",
        back_populates="terms",
    )


class TranslationMemory(Base):
    __tablename__ = "translation_memory"
    __table_args__ = (
        Index("ix_translation_memory_collection_id", "collection_id"),
        Index("ix_translation_memory_collection_source_hash", "collection_id", "source_hash"),
        Index(
            "ix_translation_memory_collection_source_normalized",
            "collection_id",
            "source_normalized",
        ),
        Index("ix_translation_memory_source_hash", "source_hash"),
        Index("ix_translation_memory_source_text", "source_text"),
        Index("ix_translation_memory_source_normalized", "source_normalized"),
        Index(
            "ix_translation_memory_source_text_trgm",
            "source_text",
            postgresql_using="gin",
            postgresql_ops={"source_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_translation_memory_source_normalized_trgm",
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
        ForeignKey("tm_collections.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
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
