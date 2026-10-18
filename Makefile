ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

# The virtual environment is the only source of dependencies. Clearing an
# inherited PYTHONPATH prevents unrelated global packages shadowing it.
export PYTHONPATH :=

.PHONY: setup seed train-model run demo eval test lint docker-up verify-isolation tamper-demo clean-data

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

seed:
	$(PY) scripts/seed_workspace.py
	$(PY) scripts/seed_db.py

train-model:
	$(PY) eval/train_content_model.py

run:
	$(PY) -m backend.main

demo:
	$(PY) demo/run_demo.py --auto-approve --assert

eval:
	$(PY) eval/run_eval.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check backend scripts demo eval

docker-up:
	docker compose up --build

verify-isolation:
	$(PY) scripts/verify_isolation.py

tamper-demo:
	$(PY) scripts/tamper_demo.py

clean-data:
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ('data','workspace')]"
