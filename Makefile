.PHONY: setup dev-mock test check backend-test frontend-test

UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export UV_CACHE_DIR

setup:
	uv sync --project backend --dev
	npm --prefix frontend ci

dev-mock:
	IDP_MODE=mock python3 scripts/dev_mock.py

backend-test:
	uv run --project backend pytest

frontend-test:
	npm --prefix frontend test

test: backend-test frontend-test

check: test
	uv run --project backend ruff check backend scripts
	uv run --project backend mypy backend/src scripts
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend run build
	uv run --project backend python scripts/validate_configuration.py
