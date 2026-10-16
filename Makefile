# Thin wrappers over docker compose. Everything runs in containers -
# nothing here needs Python installed on the host.
DC := docker compose
APP := $(DC) exec -T app

.DEFAULT_GOAL := help
.PHONY: help up down logs ps build shell psql lint fmt check migrate migrate-down migration revision ping

help:  ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

up:  ## Start the whole stack
	$(DC) up -d
down:  ## Stop the stack (data volumes survive)
	$(DC) stop
ps:  ## Show container status
	$(DC) ps
logs:  ## Follow logs (make logs s=warehouse)
	$(DC) logs -f $(s)
build:  ## Rebuild the app image
	$(DC) build app

shell:  ## Interactive shell in the app container
	$(DC) exec app bash
psql:  ## Interactive psql against the warehouse
	$(DC) exec warehouse psql -U twin -d twin
ping:  ## Report DB version, extensions and migration revision
	$(APP) twin ping

lint:  ## Ruff lint
	$(APP) ruff check .
fmt:  ## Ruff format (writes)
	$(APP) ruff format .
check: lint  ## Lint + formatting check, no writes
	$(APP) ruff format --check .

migrate:  ## Apply all pending migrations
	$(APP) alembic upgrade head
migrate-down:  ## Roll back one migration
	$(APP) alembic downgrade -1
revision:  ## Show current + history (make revision)
	$(APP) alembic current
	$(APP) alembic history
migration:  ## Scaffold a migration (make migration m="add foo")
	$(APP) alembic revision -m "$(m)"

profile:  ## Run the G2 style profile and persist results
	$(APP) twin profile

freeze-holdout:  ## G1 - freeze the held-out evaluation set (refuses to overwrite)
	$(APP) twin freeze-holdout

holdout:  ## Show what is held out
	$(APP) twin holdout

ingest:  ## Parse, redact and load all sources into the warehouse
	$(APP) twin ingest

chunk:  ## Split articles into chunks and reconcile into the warehouse
	$(APP) twin chunk

triggers:  ## List every trigger, its definition and its function source
	$(DC) exec -T warehouse psql -U twin -d twin -f /sql/list_triggers.sql

trigger-vars:  ## Demo: print every variable a trigger function receives
	$(DC) exec -T warehouse psql -U twin -d twin -f /sql/demo_trigger_variables.sql

embed:  ## Embed chunks that have no vector yet (needs COHERE_API_KEY)
	$(APP) twin embed

embed-dry:  ## Report what would be embedded, call nothing
	$(APP) twin embed --dry-run

pipeline:  ## Run the ZenML data pipeline (ingest -> chunk -> embed)
	$(APP) python run.py data

runs:  ## List recent ZenML pipeline runs
	$(APP) zenml pipeline runs list
