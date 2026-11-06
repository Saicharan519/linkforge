"""Response models for analytics (contract: docs/openapi.skeleton.yaml, decisions D27-D29)."""

import uuid
from typing import Literal

from pydantic import BaseModel

# 7d / 30d / 90d = the last N UTC calendar days INCLUDING today (today is partial).
RangeName = Literal["7d", "30d", "90d"]
DeviceType = Literal["desktop", "mobile", "tablet", "other"]


class DayCount(BaseModel):
    day: str  # "YYYY-MM-DD", a UTC calendar date; the frontend renders it as-is
    clicks: int


class ReferrerCount(BaseModel):
    host: str  # "(direct)" when there was no referrer
    clicks: int


class DeviceCount(BaseModel):
    device_type: DeviceType
    clicks: int


class BrowserCount(BaseModel):
    browser: str
    clicks: int


class LinkAnalytics(BaseModel):
    total: int
    series: list[DayCount]
    referrers: list[ReferrerCount]
    devices: list[DeviceCount]
    browsers: list[BrowserCount]


class TopLink(BaseModel):
    id: uuid.UUID
    code: str
    title: str | None
    clicks: int


class Overview(BaseModel):
    total_links: int
    total_clicks: int
    series: list[DayCount]
    top_links: list[TopLink]
