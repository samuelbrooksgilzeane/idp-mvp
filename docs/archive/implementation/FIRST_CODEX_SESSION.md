# First Codex Session

## What to prepare

1. Create an empty private remote repository named `idp-mvp` on the organisation-approved Git provider.
2. Do not initialise it with a README, licence or `.gitignore`.
3. Copy its HTTPS or SSH clone URL.
4. Attach the complete `IDP_MVP_Implementation_Pack.zip` to the Codex session.
5. Replace `<PASTE_REMOTE_REPOSITORY_URL_HERE>` in the prompt below.

Do not put credentials, access tokens, client documents or sensitive Databricks outputs in the prompt or repository. Authenticate to the Git provider through the environment's normal credential flow.

## Single copy-and-paste prompt

```text
You are implementing the first session of the IDP MVP. Work carefully and stop after the project-foundation increment.

REMOTE_REPOSITORY_URL=<PASTE_REMOTE_REPOSITORY_URL_HERE>
LOCAL_REPOSITORY_NAME=idp-mvp

The attached file IDP_MVP_Implementation_Pack.zip is authoritative. It contains the ordered implementation plan. Read these files completely before changing code:

- 00_START_HERE.md
- 01_TECHNICAL_CONTRACTS.md
- 02_COMMIT_PROJECT_FOUNDATION.md
- PROGRESS_TRACKER.md

Keep every file from the implementation pack under docs/implementation/. Do not merge the specification files or rewrite their requirements.

GOAL

Create a local Git clone linked to the supplied private remote repository, add the complete implementation pack as a documentation baseline, and implement Commit 1 only. Push the documentation baseline to main and push the project-foundation work on a separate review branch. Do not implement data storage, upload, parsing, extraction or validation.

REPOSITORY SAFETY

1. Confirm REMOTE_REPOSITORY_URL is not still a placeholder.
2. Never request or print credentials or access tokens.
3. Clone the remote into a new local directory named idp-mvp so origin is configured automatically.
4. If the local directory already exists, inspect it and do not overwrite or delete it.
5. If the remote contains material application code or history, stop and report what exists before making changes.
6. Do not force-push, rewrite history, delete remote branches or use destructive cleanup commands.
7. If authentication prevents cloning or pushing, preserve all local work, report the precise failed operation and provide the shortest manual authentication/push instructions. Do not pretend that the push succeeded.

GIT SEQUENCE

1. Clone REMOTE_REPOSITORY_URL into idp-mvp.
2. Enter the repository and verify origin with git remote -v.
3. Use main as the baseline branch.
4. Extract the complete attached implementation pack into docs/implementation/.
5. Create and push this documentation-only commit on main:

   docs: add ordered IDP MVP implementation plan

6. Create branch feat/01-project-foundation from that commit.
7. Implement only docs/implementation/02_COMMIT_PROJECT_FOUNDATION.md.
8. Create this implementation commit:

   chore(idp): scaffold deployable app and asset bundle

9. Push feat/01-project-foundation to origin with upstream tracking.
10. Do not merge the feature branch into main.

TECHNOLOGY BASELINE

- Python 3.11 or later.
- FastAPI, Pydantic and pytest.
- React, TypeScript and Vite.
- uv for Python dependency management.
- npm for frontend dependency management.
- IDP_MODE=mock as the default local mode.
- Vite proxies /api to the local FastAPI backend; do not use wildcard CORS.
- No Databricks connection, credentials, SDK calls, tables, volumes, Jobs or model calls in this increment.

REQUIRED REPOSITORY OUTPUTS

Create a clear structure similar to:

idp-mvp/
  backend/
    pyproject.toml
    src/idp_app/
      main.py
      api/
      core/
      services/
    tests/
  frontend/
    package.json
    src/
    tests/
  databricks_etl/
    databricks.yml
    resources/
  docs/implementation/
  fixtures/
  .env.example
  .gitignore
  app.yaml
  Makefile
  README.md

Implement:

1. A FastAPI application factory.
2. GET /api/health returning a typed, safe response with application status, mode and configuration-presence checks. Never return secrets.
3. A React application shell with a workflow header and an “MVP not configured” state.
4. Typed server-side configuration for catalog, project schema, table prefix, volume names, warehouse, validation endpoint and app name.
5. Strict simple-identifier validation that rejects dots, quotes, slashes, whitespace and SQL syntax where a single identifier is expected.
6. Mock mode that starts without any Databricks configuration or network access.
7. Databricks mode that fails clearly at startup when required configuration is absent, without attempting a connection.
8. app.yaml with a valid application entry point and no secrets.
9. databricks_etl/databricks.yml with dev and prod targets, trusted variables and no hardcoded workspace hostname.
10. Baseline backend tests for configuration and /api/health.
11. Baseline frontend tests, linting, type-checking and production build.
12. A root README explaining setup, mock development, tests, builds and the future Databricks handoff.
13. An .env.example containing placeholders only and a .gitignore that excludes .env files, credentials, dependency folders, build outputs, databases, local uploads and client documents.
14. Update docs/implementation/PROGRESS_TRACKER.md in the implementation commit only after its definition of done is satisfied. Record the resulting Git SHA if practical; if that would require amending the same commit, record the commit message and leave the SHA for the next documentation update rather than creating an inaccurate value.

REQUIRED COMMANDS

Provide working commands with these stable entry points:

- make setup       installs backend and frontend dependencies
- make dev-mock    starts FastAPI and Vite locally with clean shutdown behavior
- make test        runs backend and frontend tests
- make check       runs tests, linting, type-checking, frontend production build and configuration/YAML validation that does not require Databricks credentials

The local application must be viewable at http://localhost:5173 and its health endpoint must be reachable through the same origin at http://localhost:5173/api/health.

TEST AND QUALITY REQUIREMENTS

1. Run make test and make check successfully before committing.
2. Confirm make dev-mock starts without Databricks CLI, credentials or environment variables.
3. Verify the frontend can reach /api/health through the Vite proxy.
4. Do not add placeholder tests that always pass.
5. Do not suppress type, lint or test failures.
6. Keep routers, configuration and services in separate modules.
7. Do not introduce later-stage tables, PDF processing, extraction schemas, model prompts or correction workflows.

FINAL RESPONSE

Return a concise implementation report containing:

1. Local repository path.
2. Confirmed origin URL and current branch.
3. Documentation commit SHA.
4. Project-foundation commit SHA.
5. Remote push status for main and feat/01-project-foundation.
6. Exact commands run and whether each passed.
7. Local preview URL and health URL.
8. Important files created.
9. git status --short output, which should be clean.
10. git log --oneline --decorate -5 output.
11. Any deviation, failed check, authentication problem or unresolved risk.

Do not begin Commit 2. Stop after reporting the verified Commit 1 result.
```

## Expected result

The first session should leave:

- `main` containing only the complete implementation documentation.
- `feat/01-project-foundation` containing one additional, independently reviewable foundation commit.
- A remotely backed repository that can be cloned on another laptop.
- A Databricks-independent local mock shell available at `http://localhost:5173`.
- No document-processing or persistent-data functionality yet.
