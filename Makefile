.PHONY: dev dev-mock test lint format typecheck check

## Run with Entra ID authentication (real Azure login)
dev:
	AUTH_MODE=entra uv run uvicorn az_scout.app:app --reload --host 0.0.0.0 --port 8000

## Run with mock authentication (no Azure config needed)
dev-mock:
	AUTH_MODE=mock uv run uvicorn az_scout.app:app --reload --host 0.0.0.0 --port 8000

## Run tests
test:
	uv run pytest

## Lint
lint:
	uv run ruff check src/ tests/

## Format check
format:
	uv run ruff format --check src/ tests/

## Type check
typecheck:
	uv run mypy src/

## Run all checks
check: lint format typecheck test
