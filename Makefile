.PHONY: help install test lint format typecheck migrate run docker-up docker-down clean

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

help:
	@echo "Targets: install test lint format typecheck migrate run docker-up docker-down clean"

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev,redis,postgres]"

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck:
	$(BIN)/mypy leadcapture

migrate:
	$(BIN)/leadcapture migrate

run:
	$(BIN)/leadcapture serve --reload

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache *.egg-info build dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
