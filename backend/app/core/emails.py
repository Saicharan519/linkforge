"""Email normalisation and a deliberately plain syntax check (PRD F1).

We do not verify ownership (email verification is out of scope for v1), so the check only has to
reject obvious garbage and keep the stored value canonical. Rules: trimmed, lowercased, at most
254 characters, ASCII only (no internationalised addresses in v1), one `@`, a local part of at
most 64 characters, and a dotted domain whose labels are 1-63 letters, digits or hyphens.
"""

import re

MAX_EMAIL_LENGTH = 254
MAX_LOCAL_PART = 64

_PATTERN = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"  # local part: the characters RFC 5322 allows unquoted
    r"@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"  # domain labels, each followed by a dot
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"  # the last label
)


def normalize_email(raw: str) -> str | None:
    """The canonical form of `raw`, or None if it is not an acceptable email address."""
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not email.isascii():
        return None
    local, _, _ = email.partition("@")
    if len(local) > MAX_LOCAL_PART or ".." in email or local.startswith(".") or local.endswith("."):
        return None
    return email if _PATTERN.fullmatch(email) else None
