"""Request/response models for API keys (contract: docs/openapi.skeleton.yaml)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)


class ApiKeyOut(BaseModel):
    """What a key looks like AFTER creation: never the key itself, only its identifying prefix."""

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    """The only response that ever contains the plaintext key."""

    key: str
