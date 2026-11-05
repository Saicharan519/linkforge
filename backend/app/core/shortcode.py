"""Short codes: random generation, user-chosen aliases, and collision retry."""

import re
import secrets
import string
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import IntegrityError

BASE62 = string.ascii_letters + string.digits
CODE_LENGTH = 7  # 62**7 is about 3.5e12 possible codes
MAX_ATTEMPTS = 5

# PRD F2: 3-32 characters, letters/digits/underscore/hyphen. Case-sensitive, stored as typed.
ALIAS_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,32}")

# ARCHITECTURE §4. These collide with real routes or SPA pages; compared case-insensitively.
RESERVED = frozenset(
    {
        "api",
        "healthz",
        "readyz",
        "metrics",
        "docs",
        "openapi.json",
        "login",
        "register",
        "dashboard",
        "assets",
        "static",
        "admin",
    }
)

# Name of the UNIQUE constraint on links.code (see the migration).
LINK_CODE_CONSTRAINT = "uq_links_code"


class InvalidAlias(ValueError):
    """The alias is not allowed; the message is safe to show to the user."""


class CodeGenerationError(RuntimeError):
    """No free code was found within the attempt budget (practically never happens)."""


def validate_alias(alias: str) -> str:
    """Return the alias unchanged (case preserved) if it is allowed, else raise InvalidAlias."""
    if not ALIAS_PATTERN.fullmatch(alias):
        raise InvalidAlias("the alias must be 3-32 characters: letters, digits, '_' or '-'")
    if alias.lower() in RESERVED:  # "Admin" and "ADMIN" are as reserved as "admin"
        raise InvalidAlias("this alias is reserved")
    return alias


def _random_code() -> str:
    # `secrets`, not `random`: codes must not be predictable from earlier ones.
    return "".join(secrets.choice(BASE62) for _ in range(CODE_LENGTH))


def generate_code() -> str:
    """A random 7-character base62 code that is not a reserved word (decision D33)."""
    for _ in range(MAX_ATTEMPTS):
        code = _random_code()
        if code.lower() not in RESERVED:
            return code
    raise CodeGenerationError("could not generate a non-reserved code")


def is_code_collision(exc: IntegrityError) -> bool:
    """True if this IntegrityError is the UNIQUE(links.code) constraint, not some other failure."""
    # SQLAlchemy wraps the driver error; asyncpg's own exception is its __cause__ and carries the
    # name of the violated constraint.
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None) == LINK_CODE_CONSTRAINT


async def with_unique_code[T](
    attempt: Callable[[str], Awaitable[T]],
    *,
    code_factory: Callable[[], str] = generate_code,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Call `attempt(code)` with fresh random codes until one is not already taken.

    Uniqueness is decided by the database (the UNIQUE constraint), not by a racy "SELECT then
    INSERT" check: two requests may pick the same code at once and only one INSERT can win.
    `attempt` must insert the row and leave its transaction clean when it fails (the caller
    uses a savepoint or a fresh session per attempt). Any other IntegrityError is re-raised.
    """
    for _ in range(max_attempts):
        code = code_factory()
        try:
            return await attempt(code)
        except IntegrityError as exc:
            if not is_code_collision(exc):
                raise
    raise CodeGenerationError(f"no unique code found after {max_attempts} attempts")
