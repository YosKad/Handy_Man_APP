.DEFAULT_GOAL := help
PY := .venv/bin/python
VENV := .venv

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(VENV): pyproject.toml
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"
	@touch $(VENV)

.PHONY: install
install: $(VENV) ## Create the venv and install dev dependencies

.PHONY: up
up: ## Start Postgres (pgvector) and Redis
	docker compose up -d

.PHONY: down
down: ## Stop the local datastores
	docker compose down

.PHONY: migrate
migrate: install ## Apply database migrations
	$(VENV)/bin/alembic upgrade head

.PHONY: run
run: install ## Run the API with reload
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

.PHONY: test
test: install ## Run the test suite
	$(PY) -m pytest

.PHONY: cov
cov: install ## Run tests with a coverage report
	$(PY) -m pytest --cov=app --cov-report=term-missing

.PHONY: lint
lint: install ## Lint and check formatting
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

.PHONY: format
format: install ## Apply formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

.PHONY: types
types: install ## Type-check with mypy --strict
	$(VENV)/bin/mypy app tests

.PHONY: arch
arch: install ## Enforce the architecture guards
	$(PY) scripts/check_architecture.py

.PHONY: check
check: lint types arch test ## Everything CI runs

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} +
