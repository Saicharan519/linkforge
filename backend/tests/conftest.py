"""Shared test setup.

Settings are read when `app.main` is imported, so the environment must point at the TEST database
BEFORE that import. Tests therefore never touch the development database (decision D36).
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://linkforge:linkforge_dev@localhost:5432/linkforge_test",
)
# Separate Redis logical database (15) so tests can never touch dev data in db 0; it is flushed
# before every integration test.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["ENV"] = "test"
