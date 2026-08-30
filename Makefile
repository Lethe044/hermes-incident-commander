.PHONY: install install-dev smoke-test test lint dashboard watchdog demo

install:
	pip install -r requirements.txt psutil

install-dev:
	pip install -e ".[dev]"

smoke-test:
	python environments/incident_env.py --smoke-test

test:
	pytest tests/ -v

lint:
	ruff check .

demo:
	python demo/demo_incident.py --scenario disk-full-logs

watchdog:
	python -m monitor.watchdog --once

dashboard:
	python -m monitor.dashboard --open
