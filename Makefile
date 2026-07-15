PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
export PYTHONPATH := src

.PHONY: venv install run status report test lint demo bundle appimage clean

venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

install:
	bash src/scripts/fedora-install.sh

# The daemon creates and migrates the database itself on first start —
# there is no separate schema step any more.
run:
	$(VENV)/bin/behavioral-authd

status:
	$(VENV)/bin/behavioral-auth status

report:
	$(VENV)/bin/behavioral-report

# Walk the whole LEARNING -> MONITORING -> ALARM path in a couple of minutes,
# without typing for hours or recruiting someone to impersonate you.
demo:
	$(VENV)/bin/behavioral-authd --synthetic-input user --synthetic-speed 40

test:
	$(PY) -m pytest tests -q

lint:
	$(VENV)/bin/ruff check src tests

# Self-contained one-folder Linux bundle (torch CPU, no CUDA) with every
# dependency inside. Output: dist/behavioral-auth/. See packaging/.
bundle:
	PYINSTALLER=$(VENV)/bin/pyinstaller bash packaging/build-linux.sh

# Single-file AppImage wrapping the bundle. Needs appimagetool (set APPIMAGETOOL
# to its path). Output: dist/behavioral-auth-x86_64.AppImage.
appimage:
	PYINSTALLER=$(VENV)/bin/pyinstaller bash packaging/build-appimage.sh

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache build dist
