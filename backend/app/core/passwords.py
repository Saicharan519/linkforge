"""Password hashing: argon2id with the library defaults, off the event loop (ARCHITECTURE §6.2).

Why the extra machinery around `PasswordHasher`:
- Hashing is deliberately slow and memory hungry (64 MiB per hash). Run directly in a request
  handler it would freeze the whole async server, so it runs in a worker thread.
- Many logins at once would each want 64 MiB. A semaphore of 2 caps that at about 128 MiB per
  API pod (decision D10), which is what the pod's memory limit is sized for. Extra requests wait.
- Verifying against a user that does not exist must cost the same as a real check, or response
  time reveals which emails are registered. `verify_unknown` burns a real verification.

The service is created once per app in the lifespan (the semaphore belongs to that event loop).
"""

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Used only to spend the same time as a real verification; not a credential for anything.
_DUMMY_PASSWORD = "not-a-real-password-used-for-timing-only"


class PasswordService:
    def __init__(self, concurrency: int = 2, hasher: PasswordHasher | None = None) -> None:
        # Library defaults = RFC 9106 "low memory" profile (t=3, m=64 MiB, p=4); a test pins this.
        self.hasher = hasher or PasswordHasher()
        self._slots = asyncio.Semaphore(concurrency)
        self._dummy_hash: str | None = None

    async def hash(self, password: str) -> str:
        async with self._slots:
            return await asyncio.to_thread(self.hasher.hash, password)

    async def verify(self, stored_hash: str, password: str) -> bool:
        """True only if `password` matches. A wrong password, and a stored value that is not a
        valid hash at all (the placeholder users from tests and the dev user), both give False."""
        async with self._slots:
            return await asyncio.to_thread(self._verify_sync, stored_hash, password)

    def _verify_sync(self, stored_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(stored_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    async def verify_unknown(self, password: str) -> None:
        """Spend the time of one real verification (for an email that has no account)."""
        if self._dummy_hash is None:
            self._dummy_hash = await self.hash(_DUMMY_PASSWORD)
        await self.verify(self._dummy_hash, password)
