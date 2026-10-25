# pytest and pytest-cov are NOT covered by requirements.txt: they are not runtime
# dependencies, and mcrit declares them under its `dev` extra, so `pip install -r
# requirements.txt` does not bring them in. Pinned to the same versions CI installs -
# see .github/workflows/test.yml. ruff is likewise not a runtime dependency.
init:
	pip install -e ".[dev]"
lint:
	python3 -m ruff check .
test:
	python3 -m pytest
test-coverage:
	python3 -m pytest --cov=mcritweb --cov-report=html:coverage-html
clean:
	find . | grep -E "(__pycache__|\.pyc|\.pyo$\)" | xargs rm -rf
	rm -rf .coverage
	rm -rf coverage-html
	rm -rf dist/*
