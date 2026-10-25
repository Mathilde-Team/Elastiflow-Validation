# Commandes du socle : le job Qualité (.github/workflows/ci.yml) appelle les
# mêmes cibles, pour qu'un test local et le CI ne divergent jamais.
.PHONY: test

# Tests et couverture : seuil (95 %) et branches lus dans pyproject.toml.
test:
	uv run --locked pytest --cov --cov-report=term-missing
