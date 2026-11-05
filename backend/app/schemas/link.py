"""Request/response models for links (contract: docs/openapi.skeleton.yaml)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# disabled if is_active=false; else expired if expires_at <= now; else active. (PRD F2)
LinkStatus = Literal["active", "disabled", "expired"]


class LinkCreate(BaseModel):
    # forbid: a typo such as "tittle" is an error instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")

    destination_url: str
    alias: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # AwareDatetime: a time without a timezone is ambiguous, so it is rejected.
    expires_at: AwareDatetime | None = None


class LinkPatch(BaseModel):
    """Partial update. Which fields were SENT matters, not only their values:

    - omitted field          -> unchanged
    - explicit null          -> clears `title` / `expires_at`
    - null destination_url / is_active -> rejected (checked in the service via model_fields_set)
    """

    model_config = ConfigDict(extra="forbid")

    destination_url: str | None = None
    is_active: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    expires_at: AwareDatetime | None = None


class LinkOut(BaseModel):
    id: uuid.UUID
    code: str
    short_url: str
    destination_url: str
    title: str | None
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    status: LinkStatus
    total_clicks: int


class LinkList(BaseModel):
    items: list[LinkOut]
    total: int
    page: int
    page_size: int
