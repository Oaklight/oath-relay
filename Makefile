PYTHON ?= python3
VENV ?= .venv
IMAGE ?= oath-relay:dev

.PHONY: help venv test lint format build docker run clean

help:
	@printf '%s\n' \
	  'make venv     create .venv and install the package with dev extras' \
	  'make test     run the test suite' \
	  'make lint     ruff check' \
	  'make format   ruff format' \
	  'make build    build sdist and wheel into dist/' \
	  'make docker   build the container image' \
	  'make run      run the container against ./config.toml and ./state'

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -U pip
	$(VENV)/bin/pip install -e '.[dev]'

test:
	$(VENV)/bin/python -m pytest tests/ -q

lint:
	$(VENV)/bin/ruff check src/ tests/

format:
	$(VENV)/bin/ruff format src/ tests/

build:
	$(VENV)/bin/python -m build

docker:
	docker build -t $(IMAGE) .

run:
	docker run --rm -p 127.0.0.1:8790:8790 \
	  -v $(PWD)/config.toml:/config/config.toml:ro \
	  -v $(PWD)/state:/state $(IMAGE)

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
