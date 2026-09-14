
NODE ?= node
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
ALEMBIC ?= .venv/bin/alembic

.PHONY: install install-locked dev migrate migration-check test test-backend test-web lint format

install:
	$(PYTHON) -m pip install -e "backend[dev]"

install-locked:
	$(PYTHON) -m pip install -r backend/requirements.lock

dev:
	$(PYTHON) -m uvicorn ari.main:app --app-dir backend/src --reload --port 8000

migrate:
	$(ALEMBIC) -c alembic.ini upgrade head

migration-check:
	$(ALEMBIC) -c alembic.ini current --check-heads

test: test-backend test-web

test-backend:
	$(PYTEST) backend/tests

test-web:
	$(NODE) --check web/practice-client.mjs
	$(NODE) --check web/practice-ui.mjs
	$(NODE) web/tests/practice-client.test.mjs
	$(NODE) --check web/app.js
	$(NODE) --check web/pcm-worklet.js
	$(NODE) web/tests/pcm-resampler.test.mjs
	$(NODE) web/tests/audio-delivery.test.mjs
	$(NODE) web/tests/voice-presentation.test.mjs
	$(NODE) web/tests/voice-dom.test.mjs
	$(NODE) web/tests/calendar.test.mjs
	$(NODE) web/tests/main-redesign-contract.test.mjs

lint:
	$(RUFF) check backend
	$(MYPY) backend/src

format:
	$(RUFF) format backend
	$(RUFF) check --fix backend
