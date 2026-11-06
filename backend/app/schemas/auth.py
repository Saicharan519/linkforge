"""Request/response models for authentication (contract: docs/openapi.skeleton.yaml)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# PRD F1 / ARCHITECTURE §6.2: 10-128 characters. The upper bound also stops someone from sending
# megabytes to be hashed.
PASSWORD_MIN = 10
PASSWORD_MAX = 128


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Syntax and normalisation are checked in the service (core/emails.py), so the error names
    # the field the same way as every other 422.
    email: str = Field(max_length=254)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    # No minimum: a too-short password is simply a wrong password (401), not a different error,
    # so the response never hints at the password rules to someone guessing.
    password: str = Field(max_length=PASSWORD_MAX)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(max_length=PASSWORD_MAX)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime
