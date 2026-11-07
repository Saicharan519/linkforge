# Runbook: branch protection for `main` (P6.5)

Goal: nothing reaches `main` unless the whole CI passed, and nobody (including a typo) can force-push
over its history or delete it. The settings live in the repository as `.github/branch-protection.json`
so they are reviewable and can be re-applied; GitHub itself stores them in the repository settings.

## The checks, and why these exact strings
GitHub matches a required check by its NAME. A wrong name means the check never reports and every pull
request is blocked forever, so the names below were read from a real pull request (a throwaway PR,
2026-11-07), not guessed. On a PR the checks have no `ci / ` prefix (that prefix only appears when
`build-push.yml` calls the workflow after a merge), and the matrix job `images` appears once per image
with its matrix values appended.

| Required check | What it guards |
|---|---|
| `backend (ruff + pytest)` | lint, format, tests against real Postgres and Redis |
| `frontend (eslint + tsc + vitest + build)` | lint, types, tests, production build |
| `terraform (fmt + validate + checkov)` | Terraform format, validity and security rules |
| `trivy (repository files)` | known-vulnerable dependencies in the lock files |
| `images (build + trivy) (api, backend)` | the API image builds and passes the Trivy gate |
| `images (build + trivy) (frontend, frontend)` | the frontend image builds and passes the gate |
| `dependency audit (pip-audit + npm audit)` | known vulnerabilities in pinned dependencies |
| `secret scan (gitleaks)` | no secret anywhere in the git history |

If a job is renamed in `ci.yml`, change the name here and apply again, otherwise merges are blocked.

## Choices in the JSON
- `strict: true`: a pull request must be up to date with `main` before it merges, so the checks ran on what
  will really be merged. (Dependabot pull requests can be refreshed with the comment `@dependabot rebase`.)
- Pull request required, **0 approvals**: you are the only developer, so a review requirement would block
  you; the checks are the gate.
- `enforce_admins: false`: you, as the repository owner, can still push straight to `main` or merge with
  a red check in an emergency. That bypass is deliberate; use it rarely, GitHub records every use.
- No force-push, no deletion of `main`.

## Apply it (you, in your own terminal; this changes repository settings)
```bash
cd ~/linkforge
gh api -X PUT repos/Saicharan519/linkforge/branches/main/protection --input .github/branch-protection.json
```
Expect a JSON answer that contains `"required_status_checks"` with the eight names. Read it back any time:

```bash
gh api repos/Saicharan519/linkforge/branches/main/protection --jq '.required_status_checks.contexts, .enforce_admins.enabled, .allow_force_pushes.enabled'
```
Expect the eight names, `false`, `false`.

## Break it on purpose (proves the gate works)
1. `git switch -c break/lint` and add a clearly unused import to a Python file under `backend/app`.
2. Push the branch and open a pull request (`gh pr create --fill`).
3. Wait for the checks: `backend (ruff + pytest)` fails, and the merge button says **Required statuses must pass**.
4. Close the pull request and delete the branch.

## Undo
```bash
gh api -X DELETE repos/Saicharan519/linkforge/branches/main/protection
```
