export UV_CACHE_DIR := .cache/uv

.PHONY: setup check lint format typecheck test run hooks git-check

setup:
	uv sync --frozen

check: lint typecheck test

hooks:
	uv run --frozen pre-commit install

git-check:
	uv run --frozen pre-commit run --all-files --hook-stage pre-commit

lint:
	uv run --frozen ruff check policypilot tests
	uv run --frozen ruff format --check policypilot tests

format:
	uv run --frozen ruff format policypilot tests

typecheck:
	uv run --frozen mypy

test:
	uv run --frozen pytest --cov=policypilot --cov-report=term-missing

run:
	uv run --frozen uvicorn policypilot.app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
