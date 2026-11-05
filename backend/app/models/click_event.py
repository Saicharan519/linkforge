import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ClickEvent(Base):
    """One raw click, written by the worker. Bots are stored (is_bot=true) but never counted."""

    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # The idempotency key: the worker inserts with ON CONFLICT (event_id) DO NOTHING, so a
    # redelivered message can never create a second row (ARCHITECTURE §2.3).
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("links.id", ondelete="CASCADE"), nullable=False
    )
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str] = mapped_column(Text, nullable=False)  # HMAC, never the raw IP
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    browser: Mapped[str | None] = mapped_column(Text, nullable=True)
    os: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (Index("ix_click_events_link_id_clicked_at", "link_id", "clicked_at"),)
