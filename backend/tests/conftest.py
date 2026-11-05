"""Shared test setup.

Settings are read when `app.main` is imported, so the environment must point at the TEST database
BEFORE that import. Tests therefore never touch the development database (decision D36).
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://linkforge:linkforge_dev@localhost:5432/linkforge_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENV"] = "test"
