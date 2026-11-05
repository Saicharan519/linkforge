import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LinkDailyStat(Base):
    """Per-link, per-UTC-day click counter (bots excluded). Cheap time series for the charts."""

    __tablename__ = "link_daily_stats"

    link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
    )
    # A UTC calendar date. Never converted to another timezone (CLAUDE.md invariant).
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
