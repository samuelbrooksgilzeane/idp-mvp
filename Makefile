.PHONY: setup dev-mock test check backend-test frontend-test deploy bootstrap grants deploy-first

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
	uv run --project backend ruff check backend scripts databricks_etl/src
	uv run --project backend mypy backend/src scripts
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend run build
	uv run --project backend python scripts/validate_configuration.py

# Deploys. TARGET=dev needs no variables (the dev target holds the live values); another target or
# workspace passes PROFILE=... BUNDLE_VARS="--var catalog=... --var warehouse_id=...".
TARGET ?= dev
PROFILE ?= idp-mvp
BUNDLE_VARS ?=
BUNDLE = cd databricks_etl && databricks bundle

deploy: check
	$(BUNDLE) deploy -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS)
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) idp_app

# Migrations and the App's direct grants. Replacing views also drops the App's bound grants, which
# the deploy afterwards restores.
bootstrap:
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) governed_data_bootstrap
	$(BUNDLE) deploy -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS)

grants:
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) app_access_grants

# A workspace's first deployment. The first deploy creates the Jobs but not the App, whose bound
# grants name tables the bootstrap creates, so its failure is expected.
deploy-first: check
	-$(BUNDLE) deploy -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS)
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) governed_data_bootstrap
	$(BUNDLE) deploy -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS)
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) app_access_grants
	$(BUNDLE) run -t $(TARGET) -p $(PROFILE) $(BUNDLE_VARS) idp_app
