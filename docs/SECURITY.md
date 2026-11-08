# LinkForge — Security notes (threat model & controls)

Scope: portfolio project. This is a lightweight threat model, not a certification. Update it when the design changes.

## Assets
User accounts and passwords, sessions, API keys, link data, click analytics (hashed IPs), AWS account/credentials, container images, Terraform state.

## Threats and controls
| Threat | Control | Where |
|---|---|---|
| Credential stuffing / brute force | argon2id; login limit 5/15 min per IP+email, **fails closed**; generic 401 | ARCH §2.4, §6.2 |
| Spoofed client IP defeats rate limits | `TRUSTED_PROXY_COUNT` rule; never read raw `X-Forwarded-For` | ARCH §6.3 |
| Session theft | server-side sessions, HttpOnly + Secure cookies, rotation on login, instant revocation, host-only cookie | ARCH §2.4, §6.1 |
| CSRF | double-submit token + `Origin`/`Sec-Fetch-Site` check on unsafe cookie-auth requests; SameSite=Lax | ARCH §2.4 |
| Account/link enumeration | cross-user access → 404; same 401 for bad email/password | PRD F1, F2 |
| Open redirect / redirect loops | destination must be http/https, not our own short domain; fixed-code redirect only | PRD F2 |
| Scanners hammering random codes | negative cache, redirect rate limit | ARCH §2.1 |
| Metrics/ops endpoints exposed publicly | metrics on separate internal port; `go` host serves only `GET /{code}`; no Ingress to Argo/Grafana | ARCH §1.2 |
| Leaked API key | only sha256 + prefix stored; shown once; revocable; not auto-revoked on password change (explicit) | ARCH §2.4 |
| PII in analytics | IPs stored only as HMAC; referrer host only; no raw IP in logs | ARCH §2.2 |
| Vulnerable dependencies/images | Trivy `fs` + `image` gates (HIGH/CRITICAL, ignore-unfixed), pinned bases; exceptions need justification + expiry | infra/CLAUDE.md |
| Supply-chain (CI) | actions pinned by SHA, least-privilege `GITHUB_TOKEN`, branch protection with required checks | DEVOPS_GUIDE §3 |
| Long-lived cloud keys leak | none exist: GitHub OIDC with `sub` condition limited to `main`; human uses SSO/MFA | DEVOPS_GUIDE §6 |
| Secrets in git | Sealed Secrets only; `.env` gitignored; no secrets in images | DEVOPS_GUIDE §10 |
| Terraform state exposure | private, encrypted, versioned S3 bucket; never commit plans | DEVOPS_GUIDE §5 |
| Cluster access | EKS endpoint restricted to the owner's IP; Argo/Grafana via port-forward | ARCH §1.2 |
| Container breakout | non-root, drop ALL capabilities, no privilege escalation, read-only FS where possible | infra/CLAUDE.md |
| DB traffic interception | TLS to RDS with certificate verification (`verify-full`, CA bundle in image) | ARCH §6.4 |
| Stale pinned dependencies | Dependabot PRs (actions, pip, npm, docker, terraform); Trivy + pip-audit gates | infra/CLAUDE.md |
| Insecure IaC | Checkov on Terraform in CI | infra/CLAUDE.md |
| Shortener abused for phishing/malware | account required to create links, per-user create limit, API-key revocation, URL validation; **no malicious-URL feed screening (known gap)**; keep public exposure short (EKS session is time-boxed) | PRD F2, F6 |
| Claude/agent tooling mishap | `.claude/settings.json` denies are a guardrail only; cloud credentials never exist in the shell Claude runs in; human runs all cloud commands | CLAUDE.md, D22 |

## Known gaps (deliberate, v1)
No NetworkPolicies, no WAF, no image signing (cosign), no Multi-AZ RDS, no email verification or password reset, no MFA for app users, single-replica Redis. Production would add these.

## Reporting
Personal project: open a GitHub issue marked "security" or email the repository owner. No bounty.
