# O's flow V2 Current Status

This document is the working source of truth for the current session.

## Verified So Far

- React-admin can connect to the local FastAPI backend.
- `/admin/ui` Seed demo pack works.
- `/cases/{id}/show` renders correctly in React-admin.
- `/admin/backends` currently reports InsForge as not ready when required config is missing.
- `python -m app.cli.insforge_smoke` exists as a diagnostic entrypoint and is intended to report `missing_config`, `connection_failed`, or `ready`.
- Direct InsForge connectivity is not yet verified in a real managed environment.

## Current Priority Order

1. InsForge repository adapter real-environment validation
2. InsForge storage adapter real-environment validation
3. Auth, customer separation, and permissions hardening
4. Billing, output, and document generation hardening
5. Production deploy, backup, monitoring, and OCR guidance
6. Real LINE / Discord end-to-end validation

## Current Implementation State

- SQLite and local file storage remain the working development/test adapters.
- InsForge configuration placeholders exist in `.env.example` and `app/config.py`.
- Runtime backend selection already supports `sqlite` and `insforge`.
- `app.cli.insforge_smoke` probes both repository and storage readiness without touching the SQLite/local path.
- The InsForge smoke probe now distinguishes missing config, transport failure, and generic HTTP reachability on the configured base URL.
- This probe is still generic reachability validation, not a confirmed InsForge API/schema check.
- Request customer scope is now propagated into operation and delivery logs when a scoped request is present.
- Mutating requests are rejected when they try to override a configured customer tenant.
- Customer tenant overrides are now rejected for read and write requests when a default tenant is configured.
- Cases and case-owned documents are tenant-scoped in SQLite when a customer scope is active.
- The tenant-scoping changes above are covered by focused unit tests and passed in the current workspace.
- InsForge repository and storage adapters still do not implement real repository/storage CRUD behavior in code.
- The admin backend status surfaces configuration readiness for repository, storage, auth, and customer scope.

## Important Constraints

- Do not treat missing configuration as proof that the managed backend is broken.
- Do not assume InsForge API or storage behavior without a real connection test.
- Keep SQLite/local adapters intact until InsForge-backed paths are actually working.
- Keep this document short and update it when a verified state changes.

## Next Useful Work

- Implement or wire the InsForge repository and storage adapters to a verifiable real backend path.
- Expand the InsForge repository and storage adapters beyond reachability probes into real CRUD / object operations.
- Once real connectivity exists, move on to auth and customer separation hardening.
