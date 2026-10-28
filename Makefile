# Commandes du socle : le job Qualité (.github/workflows/ci.yml) appelle les
# mêmes cibles, pour qu'un test local et le CI ne divergent jamais.
.PHONY: test lint

# Tests et couverture : seuil (95 %) et branches lus dans pyproject.toml.
test:
	uv run --locked pytest --cov --cov-report=term-missing

# Lint et format Python : ruff, version figée dans uv.lock (groupe dev).
lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
