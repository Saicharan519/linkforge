# Amazon RDS CA bundle (decision D85)

`rds-global-bundle.pem` is the bundle Amazon publishes for verifying the TLS certificate of ANY RDS instance
in any region (114 public CA certificates, no private keys). The image copies it to
`/etc/ssl/rds/global-bundle.pem`; with `DATABASE_SSL_MODE=verify-full` and
`DATABASE_SSL_CA_FILE=/etc/ssl/rds/global-bundle.pem` the API, the worker and the migration Job refuse a
database whose certificate does not chain to this bundle or does not name the host they connected to.

| | |
|---|---|
| Source | https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem |
| Downloaded | 2026-11-07 |
| Size | 174,560 bytes |
| sha256 | `90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b` |

Why it is committed instead of downloaded during the image build: the build is reproducible and offline-safe,
the bytes are reviewed in a pull request, and a changed file at AWS cannot silently change what the image trusts.

## Updating it (when AWS rotates certificates, or yearly)
1. `curl -fsSL -o backend/certs/rds-global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`
2. Check: `grep -c PRIVATE backend/certs/rds-global-bundle.pem` must print 0; `sha256sum` it and update the table above.
3. Open a pull request (CI rebuilds the image and runs the TLS tests).

The file is an explicit exception to the repository-wide `*.pem` ignore rule (`.gitignore`, `backend/.dockerignore`).
