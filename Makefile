PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
export PYTHONPATH := src

.PHONY: venv install schema features train infer report test lint clean

venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

install:
	bash src/scripts/fedora-install.sh

schema:
	PYTHONPATH=src $(PY) src/scripts/bootstrap_db.py

features:
	PYTHONPATH=src $(PY) -m behavioral_auth features

train:
	PYTHONPATH=src $(PY) -m behavioral_auth train

infer:
	PYTHONPATH=src $(PY) -m behavioral_auth infer

report:
	PYTHONPATH=src $(PY) -m behavioral_auth report

test:
	PYTHONPATH=src $(PY) -m pytest tests -q

lint:
	$(VENV)/bin/ruff check src tests

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
