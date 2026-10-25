# Python used to create the virtual environment. Override on the command line
# when neither python3 (POSIX) nor python (Windows) is on PATH.
ifeq ($(OS),Windows_NT)
PYTHON ?= python
PY := .venv/Scripts/python.exe
else
PYTHON ?= python3
PY := .venv/bin/python
endif

# The virtual environment is the only source of dependencies. Clearing an
# inherited PYTHONPATH prevents unrelated global packages shadowing it.
export PYTHONPATH :=

.PHONY: setup seed train-model run demo eval test lint docker-up \
        verify-isolation tamper-demo clean-data frontend dashboard init

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

seed:
	$(PY) scripts/seed_workspace.py
	$(PY) scripts/seed_db.py

init:
	$(PY) -m backend.main --init

train-model:
	$(PY) eval/train_content_model.py

run:
	$(PY) -m backend.main

demo:
	$(PY) demo/run_demo.py --auto-approve --assert

full-demo:
	$(PY) demo/full_demo.py

eval:
	$(PY) eval/run_eval.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check backend scripts demo eval

frontend:
	cd frontend && npm install && npm run build

dashboard:
	cd frontend && npm run dev

docker-up:
	docker compose up --build

# The five isolation checks only mean something inside the agent container.
verify-isolation:
	docker compose exec -T agent python scripts/verify_isolation.py

tamper-demo:
	$(PY) scripts/tamper_demo.py

clean-data:
	$(PY) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('data','workspace')]"
