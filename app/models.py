from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TranslationMemory(Base):
    __tablename__ = "translation_memory"
    __table_args__ = (
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
