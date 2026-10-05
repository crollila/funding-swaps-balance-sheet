.PHONY: install test run lint reproduce

install:
	pip install -e ".[dev]"

test:
	python -m pytest

run:
	python -m fsbs

lint:
	ruff check src tests

# One command: install, regenerate every result/figure/doc, then validate.
reproduce: install run test
