# Contributor and agent workflow

This document is the maintained, shareable source of truth for working in this
repository. Local `AGENTS.md` and `CLAUDE.md` files may add private environment
details, but must not duplicate this guidance or contain credentials.

## Architecture

- `backend/main.py` creates the FastAPI application and mounts 16 standard
  routers; the staging router is additionally enabled with
  `SPUD_ENABLE_STAGING`.
- The React SPA is in `frontend/`; Vite writes its production output to
  `dist/`. The backend serves installed assets from `static/`.
- The standard-library CLI is `backend/spud-cli` and communicates over SSH.
- Router state is persisted in `/etc/spud-router/state.json`. Normal state
  mutations use `load_state()` and `save_state()`; recovery/rollback paths are
  deliberately narrow exceptions and must preserve their atomicity guarantees.

## Validation

Run the backend suite from `backend/`:

```bash
python -m pytest tests/ -q
```

Build the production UI from `frontend/`:

```bash
npm run build
```

CI runs the backend suite on Python 3.11 and 3.12, executes privileged
network-namespace firewall tests, and runs a Firefox end-to-end browser smoke
test against the built SPA. Tests isolate their own state, but contributors
must still review fixtures before assuming an individual test has no external
side effects.

## Change and release process

1. Create or link an issue and work on a `feat/`, `fix/`, `docs/`, or `chore/`
   branch.
2. Run the relevant validation above and open a PR that links the issue.
3. Merge only after required CI and project approval policy are satisfied.
4. For a release, update `VERSION`, commit it, tag `vX.Y.Z`, and push the tag.
   The Release workflow builds, tests, packages, smoke-tests, and publishes the
   archive.
5. Apply the published artifact to the designated test device using its local
   runbook, then verify the service and HTTPS health endpoint. Do not put device
   addresses, SSH keys, passwords, or API tokens in tracked files or issues.

See [release.md](release.md) for recovery details. Hardware verification and
planned capabilities must remain distinguished from CI-tested behavior in
public documentation.
