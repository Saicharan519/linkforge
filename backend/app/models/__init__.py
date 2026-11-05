"""Importing this package registers every table on `Base.metadata` (Alembic relies on that)."""

from app.models.api_key import ApiKey
from app.models.base import Base
from app.models.click_event import ClickEvent
from app.models.link import Link
from app.models.link_daily_stat import LinkDailyStat
from app.models.user import User

__all__ = ["ApiKey", "Base", "ClickEvent", "Link", "LinkDailyStat", "User"]
