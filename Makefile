# pytest, pytest-cov and pytest-xdist are NOT covered by requirements.txt: they are
# not runtime dependencies, and mcrit declares the first two under its `dev` extra, so
# `pip install -r requirements.txt` does not bring them in. Pinned to the same versions
# CI installs - see .github/workflows/test.yml. ruff is likewise not a runtime dependency.
init:
	pip install -r requirements.txt
	pip install "pytest==9.1.1" "pytest-cov==7.1.0" "pytest-xdist==3.8.0" "ruff==0.16.0"
lint:
	python3 -m ruff check .
test:
	python3 -m pytest -n auto
test-coverage:
	python3 -m pytest --cov=mcritweb --cov-report=html:coverage-html
clean:
	find . | grep -E "(__pycache__|\.pyc|\.pyo$\)" | xargs rm -rf
	rm -rf .coverage
	rm -rf coverage-html
	rm -rf dist/*
