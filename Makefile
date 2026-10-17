.PHONY: install test lint run batch seed-1000 docker-build docker-run clean help

PYTHON ?= python

help:
	@echo "Signalpost Agent - Management Commands"
	@echo "======================================"
	@echo "make install       Install dependencies in current virtual environment"
	@echo "make test          Run full test suite with pytest"
	@echo "make run ORGNR=... Run single company profile extraction (default: Equinor 923609016)"
	@echo "make batch FILE=.. Run batch extraction from a text file"
	@echo "make seed-1000     Execute 1,000 active company generation pipeline"
	@echo "make docker-build  Build Docker container image"
	@echo "make docker-run    Run agent inside Docker"
	@echo "make clean         Remove cache files and temporary output"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests -v --tb=short

run:
	$(PYTHON) -m src.cli run --orgnr $(or $(ORGNR),923609016)

batch:
	$(PYTHON) -m src.cli batch --file $(or $(FILE),orgnrs.txt) --concurrency 10

seed-1000:
	$(PYTHON) -m src.scripts.generate_1000 1000 25

docker-build:
	docker build -t signalpost-agent:latest .

docker-run:
	docker run --rm -v $$(pwd)/output:/app/output signalpost-agent:latest run --orgnr $(or $(ORGNR),923609016)

clean:
	rm -rf .pytest_cache .coverage htmlcov build dist *.egg-info output/profiles/*.tmp
