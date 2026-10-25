export UV_CACHE_DIR := .cache/uv

.PHONY: setup check lint format typecheck test run ui demo-seed eval-smoke hooks git-check

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

ui:
	uv run --frozen streamlit run policypilot/ui/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false --server.enableCORS true --server.enableXsrfProtection true --client.showErrorDetails none

demo-seed:
	uv run --frozen python -m policypilot.manage init-db
	uv run --frozen python -m policypilot.manage seed-demo

eval-smoke:
	uv run --frozen python -m policypilot.manage evaluate-retrieval --items docs/evaluation/smoke-items.jsonl --k 5
