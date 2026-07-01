# Unified entry point for the monorepo.
# Python deps live in the repo-root .venv (vllm, fastapi, pytest, ...).

VENV ?= .venv
PY   := $(VENV)/bin/python
PYTEST := $(PY) -m pytest

COMPOSE := docker compose -f deploy/docker-compose.yaml
COMPOSE_MIXED := docker compose -f deploy/docker-compose.mixed.yaml

.PHONY: help test test-backend test-router test-schema \
        dev-backend dev-frontend build-frontend install-frontend \
        up down logs ps build up-mixed down-mixed logs-mixed

help:
	@echo "Targets:"
	@echo "  test            Run all Python test suites (backend + router + config-schema)"
	@echo "  test-backend    Backend pytest (apps/backend)"
	@echo "  test-router     Router pytest (apps/router-server)"
	@echo "  test-schema     Config-schema pytest (packages/config-schema)"
	@echo "  dev-backend     Run the dashboard backend (uvicorn, :5000)"
	@echo "  dev-frontend    Run the Vue dashboard dev server"
	@echo "  build-frontend  Production build of the frontend"
	@echo "  --- docker (deploy/docker-compose.yaml) ---"
	@echo "  up              Build + start the whole stack in the background"
	@echo "  down            Stop + remove the stack"
	@echo "  logs            Tail logs from all services"
	@echo "  ps              Show service status"
	@echo "  build           Build images without starting"
	@echo "  --- mixed-engine HA (deploy/docker-compose.mixed.yaml) ---"
	@echo "  up-mixed        vLLM + SGLang backends sharing one Postgres/router/dashboard"
	@echo "  down-mixed      Stop + remove the mixed stack"
	@echo "  logs-mixed      Tail logs from the mixed stack"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

build:
	$(COMPOSE) build

# --- mixed-engine HA deployment (vLLM + SGLang backends, shared Postgres) ---
up-mixed:
	$(COMPOSE_MIXED) up -d --build

down-mixed:
	$(COMPOSE_MIXED) down

logs-mixed:
	$(COMPOSE_MIXED) logs -f

test: test-backend test-router test-schema

test-backend:
	cd apps/backend && $(abspath $(PYTEST))

test-router:
	cd apps/router-server && $(abspath $(PYTEST))

test-schema:
	cd packages/config-schema && $(abspath $(PYTEST))

dev-backend:
	cd apps/backend && $(abspath $(PY)) -m uvicorn main:app --reload --host 0.0.0.0 --port 5000

dev-frontend:
	cd apps/frontend_llmops && npm run dev

build-frontend:
	cd apps/frontend_llmops && npm run build

install-frontend:
	cd apps/frontend_llmops && npm install
