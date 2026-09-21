
NODE ?= node
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
ALEMBIC ?= .venv/bin/alembic
LINT_IMPORTS ?= .venv/bin/lint-imports

.PHONY: install install-locked db-up db-down db-reset dev dev-fr backoffice worker migrate migrate-content migration-check migration-check-content test test-backend test-web test-backoffice-web lint format

install:
	$(PYTHON) -m pip install -e "backend[dev]"

install-locked:
	$(PYTHON) -m pip install -r backend/requirements.lock

# Local PostgreSQL (docker compose). Tests, the demo and dev-fr all need it.
db-up:
	docker compose up -d --wait postgres

db-down:
	docker compose down

# Drops the data volume: every local database, including the persistent dev-fr content.
db-reset:
	docker compose down -v
	docker compose up -d --wait postgres
	$(MAKE) migrate migrate-content

# Content pipeline worker (PDF → protocol drafts); needs the content database migrated.
worker:
	PYTHONPATH=backend/src $(PYTHON) -m ari.worker

# Back-office (accounts by invitation, uploads, review, validation) on http://localhost:8100.
# First account: PYTHONPATH=backend/src $(PYTHON) -m ari.backoffice_api.cli create-owner --email … --name …
backoffice:
	$(PYTHON) -m uvicorn ari.backoffice:app --app-dir backend/src --reload --port 8100

dev:
	$(PYTHON) -m uvicorn ari.main:app --app-dir backend/src --reload --port 8000

# Live providers (.env), persistent platform database, synthetic French voice case for testing.
dev-fr:
	PYTHONPATH=backend/src $(PYTHON) -m ari.demo --provider openai --bundles cases/dev

migrate:
	$(ALEMBIC) -c alembic.ini -n platform upgrade head

migration-check:
	$(ALEMBIC) -c alembic.ini -n platform current --check-heads

migrate-content:
	$(ALEMBIC) -c alembic.ini -n content upgrade head

migration-check-content:
	$(ALEMBIC) -c alembic.ini -n content current --check-heads

test: test-backend test-web test-backoffice-web

test-backoffice-web:
	$(NODE) --check backoffice-web/api.mjs
	$(NODE) --check backoffice-web/app.mjs
	$(NODE) backoffice-web/tests/api.test.mjs
	$(NODE) backoffice-web/tests/backoffice-dom.test.mjs

test-backend:
	$(PYTEST) backend/tests

test-web:
	$(NODE) --check web/practice-client.mjs
	$(NODE) --check web/practice-ui.mjs
	$(NODE) --check web/lexicon-client.mjs
	$(NODE) --check web/placement-client.mjs
	$(NODE) --check web/pcm-recorder.mjs
	$(NODE) web/tests/practice-client.test.mjs
	$(NODE) web/tests/lexicon-client.test.mjs
	$(NODE) web/tests/placement-client.test.mjs
	$(NODE) web/tests/practice-dom.test.mjs
	$(NODE) --check web/app.js
	$(NODE) --check web/pcm-worklet.js
	$(NODE) web/tests/pcm-resampler.test.mjs
	$(NODE) web/tests/audio-delivery.test.mjs
	$(NODE) web/tests/voice-presentation.test.mjs
	$(NODE) web/tests/voice-dom.test.mjs
	$(NODE) web/tests/calendar.test.mjs

lint:
	$(RUFF) check backend
	$(MYPY) backend/src
	PYTHONPATH=backend/src $(LINT_IMPORTS) --config backend/pyproject.toml

format:
	$(RUFF) format backend
	$(RUFF) check --fix backend
