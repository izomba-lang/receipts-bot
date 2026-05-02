.PHONY: install test lint run

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

lint:
	ruff check src/
	mypy --strict src/

run:
	python -m src.main
