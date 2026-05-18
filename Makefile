.PHONY: install install-dev serve lint typecheck test test-unit test-integration ingest stats clean frontend-install frontend-dev frontend-build

PYTHON := python
UV := uv

install:
	$(UV) pip install -e .

install-dev:
	$(UV) pip install -e ".[dev,pdf,embeddings,api,llm]"

serve:
	./scripts/serve.sh --reload

lint:
	ruff check src/ tests/ scripts/
	ruff format --check src/ tests/ scripts/

format:
	ruff format src/ tests/ scripts/
	ruff check --fix src/ tests/ scripts/

typecheck:
	mypy src/

test:
	pytest tests/ -m "not integration" -v

test-unit:
	pytest tests/ -m "not integration" -v --cov=research_station --cov-report=term-missing

test-integration:
	pytest tests/ -m integration -v -s

# ── Ingestion shortcuts ───────────────────────────────────────────────────
ingest:
	$(PYTHON) -m research_station.cli run

ingest-arxiv:
	$(PYTHON) -m research_station.cli run --source arxiv

ingest-dry:
	$(PYTHON) -m research_station.cli run --dry-run --verbose

stats:
	$(PYTHON) -m research_station.cli stats

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete
	rm -rf .mypy_cache .ruff_cache .pytest_cache

# ── Frontend (Vite + TypeScript) ─────────────────────────────────────────
frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build
