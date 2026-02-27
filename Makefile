ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: dev dev-reload lint test ci db-migrate-up db-migrate-down db-migrate-status

dev:
	python scripts/dev.py --no-reload

dev-reload:
	python scripts/dev.py

lint:
	python -m flake8 services scripts tests main.py

test:
	python -m pytest

ci: lint test

db-migrate-up:
	python scripts/migrate.py up

db-migrate-down:
	python scripts/migrate.py down --steps 1

db-migrate-status:
	python scripts/migrate.py status
