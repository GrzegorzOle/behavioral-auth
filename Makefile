PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
export PYTHONPATH := src

.PHONY: venv install run status report test lint demo demo-impostor bundle appimage clean

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

# Walk LEARNING -> MONITORING in a couple of minutes, without typing for hours.
# --mode dev is what makes it a demo: it merges config.dev.yaml (shrunk gates)
# and lifts the prod refusal on synthetic input.
#
# It stops at MONITORING, because the synthetic user never stops being the user.
# For the ALARM leg, run `make demo-impostor` from another terminal once the log
# says MONITORING — that swaps the person at the keyboard mid-run.
#
# NOTE: this writes to the real data_dir from config.yaml, same as `make run`.
demo:
	$(VENV)/bin/behavioral-authd --mode dev --synthetic-input user --synthetic-speed 40

demo-impostor:
	$(VENV)/bin/behavioral-auth set-profile impostor

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
