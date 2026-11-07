"""A real TLS handshake with a real Postgres (decisions D14, D85).

Starts a throw-away Postgres 16 in Docker with TLS on and a server certificate signed by a CA we
generate here (SAN: DNS:localhost only). Then connects the way the app does (`create_engine`) in
every mode and checks what Postgres itself reports (`pg_stat_ssl`), and that the migration path
(`alembic upgrade head`) honours the same settings. Skipped when docker or openssl is missing; CI
has both.
"""

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import create_engine
from tests.integration.conftest import BACKEND_DIR

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None or shutil.which("openssl") is None,
    reason="needs docker and openssl",
)

PG_IMAGE = "postgres:16.15"  # the version the chart, compose and CI run
PASSWORD = "tls-test-only"


@dataclass
class TlsPostgres:
    port: int
    ca_file: str  # the CA that signed the server certificate
    other_ca_file: str  # an unrelated CA: must be rejected


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def make_ca(directory: Path, name: str) -> None:
    run(
        *("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2"),
        *("-keyout", f"{name}.key", "-out", f"{name}.crt", "-subj", f"/CN={name}"),
        cwd=directory,
    )


def make_server_certificate(directory: Path) -> None:
    (directory / "server.ext").write_text("subjectAltName=DNS:localhost\n")
    run(
        *("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost"),
        *("-keyout", "server.key", "-out", "server.csr"),
        cwd=directory,
    )
    run(
        *("openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key"),
        *("-CAcreateserial", "-out", "server.crt", "-days", "2", "-extfile", "server.ext"),
        cwd=directory,
    )


@pytest.fixture(scope="module")
def tls_postgres(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TlsPostgres]:
    d = tmp_path_factory.mktemp("tls")
    make_ca(d, "ca")
    make_ca(d, "other-ca")
    make_server_certificate(d)
    for f in d.iterdir():  # the container reads them through a read-only bind mount
        f.chmod(0o644)

    name = f"lf-tls-pg-{uuid.uuid4().hex[:8]}"
    # Postgres insists that the key is owned by the postgres user with mode 0600, which a bind
    # mount cannot provide, so the container copies the files first (as root) and then starts the
    # normal entrypoint.
    start = (
        "cp /certs/server.crt /certs/server.key /tmp/ && chown postgres:postgres /tmp/server.* "
        "&& chmod 600 /tmp/server.key && exec docker-entrypoint.sh postgres "
        "-c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key"
    )
    run(
        *("docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={PASSWORD}"),
        *("-p", "127.0.0.1::5432", "-v", f"{d}:/certs:ro", PG_IMAGE, "sh", "-c", start),
    )
    try:
        for _ in range(60):  # TCP readiness only (the init phase listens on the socket, not TCP)
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("the TLS Postgres did not become ready")
        port_line = run("docker", "port", name, "5432/tcp").stdout.splitlines()[0]
        yield TlsPostgres(
            int(port_line.rsplit(":", 1)[1]), str(d / "ca.crt"), str(d / "other-ca.crt")
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def settings_for(server: TlsPostgres, *, host: str, mode: str, ca_file: str | None) -> Settings:
    return Settings(
        database_url=f"postgresql+asyncpg://postgres:{PASSWORD}@{host}:{server.port}/postgres",
        database_ssl_mode=mode,  # type: ignore[arg-type]
        database_ssl_ca_file=ca_file,
    )


async def is_encrypted(settings: Settings) -> bool:
    """Ask POSTGRES whether this very connection is encrypted (not what the client believes)."""
    engine = create_engine(settings, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            sql = "select ssl from pg_stat_ssl where pid = pg_backend_pid()"
            return bool((await conn.execute(text(sql))).scalar_one())
    finally:
        await engine.dispose()


async def failure_text(settings: Settings) -> str:
    with pytest.raises(Exception) as caught:
        await is_encrypted(settings)
    return f"{caught.value!r} {caught.value.__cause__!r}".lower()


async def test_verify_full_with_the_right_ca_and_the_right_name_is_encrypted(
    tls_postgres: TlsPostgres,
) -> None:
    s = settings_for(
        tls_postgres, host="localhost", mode="verify-full", ca_file=tls_postgres.ca_file
    )
    assert await is_encrypted(s) is True


async def test_verify_full_rejects_a_server_signed_by_another_ca(tls_postgres: TlsPostgres) -> None:
    s = settings_for(
        tls_postgres, host="localhost", mode="verify-full", ca_file=tls_postgres.other_ca_file
    )
    assert "certificate verify failed" in await failure_text(s)


async def test_verify_full_rejects_a_host_name_the_certificate_does_not_name(
    tls_postgres: TlsPostgres,
) -> None:
    # Same server, same CA, valid chain; but we connect to the IP and the certificate names only
    # "localhost".
    s = settings_for(
        tls_postgres, host="127.0.0.1", mode="verify-full", ca_file=tls_postgres.ca_file
    )
    assert "mismatch" in await failure_text(s)


async def test_verify_ca_accepts_the_connection_that_verify_full_rejects(
    tls_postgres: TlsPostgres,
) -> None:
    s = settings_for(tls_postgres, host="127.0.0.1", mode="verify-ca", ca_file=tls_postgres.ca_file)
    assert await is_encrypted(s) is True


async def test_verify_ca_still_rejects_another_ca(tls_postgres: TlsPostgres) -> None:
    s = settings_for(
        tls_postgres, host="127.0.0.1", mode="verify-ca", ca_file=tls_postgres.other_ca_file
    )
    assert "certificate verify failed" in await failure_text(s)


async def test_require_encrypts_without_checking_the_certificate(tls_postgres: TlsPostgres) -> None:
    s = settings_for(tls_postgres, host="127.0.0.1", mode="require", ca_file=None)
    assert await is_encrypted(s) is True


async def test_disable_really_is_plain_text(tls_postgres: TlsPostgres) -> None:
    # The server offers TLS but does not insist. asyncpg's own default would take the offer
    # ("prefer"), so `disable` must say ssl=False explicitly (kind and compose rely on plain TCP).
    s = settings_for(tls_postgres, host="127.0.0.1", mode="disable", ca_file=None)
    assert await is_encrypted(s) is False


def alembic(server: TlsPostgres, *args: str, ca_file: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_URL": f"postgresql+asyncpg://postgres:{PASSWORD}@localhost:{server.port}/postgres",
        "DATABASE_SSL_MODE": "verify-full",
        "DATABASE_SSL_CA_FILE": ca_file,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_migration_refuses_an_untrusted_server_then_succeeds_with_the_right_ca(
    tls_postgres: TlsPostgres,
) -> None:
    bad = alembic(tls_postgres, "upgrade", "head", ca_file=tls_postgres.other_ca_file)
    assert bad.returncode != 0
    assert "certificate verify failed" in bad.stderr

    good = alembic(tls_postgres, "upgrade", "head", ca_file=tls_postgres.ca_file)
    assert good.returncode == 0, good.stderr
    current = alembic(tls_postgres, "current", ca_file=tls_postgres.ca_file)
    assert "(head)" in current.stdout
