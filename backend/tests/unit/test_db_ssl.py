"""TLS settings for the database connection (decisions D14, D85): what each mode configures.

The real handshake against a TLS Postgres is tested in tests/integration/test_db_tls.py; these
tests pin down the pure part: settings validation and the SSL context each mode produces.
"""

import ssl
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DatabaseSettings
from app.db import database_connect_args, database_ssl_context

URL = "postgresql+asyncpg://user:pw@db.example.test:5432/linkforge"
# Any valid PEM bundle works as "a CA file" here; the vendored RDS bundle is one (and is also the
# file the image ships, so this test fails if somebody breaks it).
BUNDLE = str(Path(__file__).resolve().parents[2] / "certs" / "rds-global-bundle.pem")


def make(**overrides: object) -> DatabaseSettings:
    return DatabaseSettings(database_url=URL, **overrides)  # type: ignore[arg-type]


def test_the_default_is_plain_tcp() -> None:
    settings = make()
    assert settings.database_ssl_mode == "disable"
    assert database_ssl_context(settings) is None
    # Explicitly False: asyncpg's own default ("prefer") takes TLS whenever the server offers it.
    assert database_connect_args(settings)["ssl"] is False


def test_the_connect_timeout_is_always_there() -> None:
    assert database_connect_args(make())["timeout"] == 3
    assert database_connect_args(make(database_ssl_mode="require"))["timeout"] == 3


def test_require_encrypts_but_does_not_verify() -> None:
    context = database_ssl_context(make(database_ssl_mode="require"))
    assert context is not None
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_verify_ca_verifies_the_chain_but_not_the_host_name() -> None:
    context = database_ssl_context(make(database_ssl_mode="verify-ca", database_ssl_ca_file=BUNDLE))
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is False


def test_verify_full_verifies_the_chain_and_the_host_name() -> None:
    context = database_ssl_context(
        make(database_ssl_mode="verify-full", database_ssl_ca_file=BUNDLE)
    )
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_verify_modes_trust_only_the_given_ca_file() -> None:
    context = database_ssl_context(
        make(database_ssl_mode="verify-full", database_ssl_ca_file=BUNDLE)
    )
    assert context is not None
    # 114 certificates in the RDS bundle and nothing from the system store.
    assert len(context.get_ca_certs()) == 114


def test_the_context_is_handed_to_the_driver() -> None:
    args = database_connect_args(make(database_ssl_mode="verify-full", database_ssl_ca_file=BUNDLE))
    assert isinstance(args["ssl"], ssl.SSLContext)


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_a_verify_mode_without_a_ca_file_refuses_to_start(mode: str) -> None:
    with pytest.raises(ValidationError, match="needs DATABASE_SSL_CA_FILE"):
        make(database_ssl_mode=mode)


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_a_missing_ca_file_refuses_to_start(mode: str) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        make(database_ssl_mode=mode, database_ssl_ca_file="/no/such/ca.pem")


def test_an_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(database_ssl_mode="prefer")


def test_the_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_SSL_MODE", "verify-full")
    monkeypatch.setenv("DATABASE_SSL_CA_FILE", BUNDLE)
    settings = DatabaseSettings()
    assert settings.database_ssl_mode == "verify-full"
    assert settings.database_ssl_ca_file == BUNDLE


def test_the_vendored_rds_bundle_is_what_the_readme_says() -> None:
    import hashlib

    data = Path(BUNDLE).read_bytes()
    assert (
        hashlib.sha256(data).hexdigest()
        == "90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b"
    )
    assert b"PRIVATE KEY" not in data
    assert data.count(b"BEGIN CERTIFICATE") == 114
