# SPDX-License-Identifier: GPL-3.0-or-later
"""Le runner change sans changer les contrôles requis (#50)."""

import json
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[1]


def test_runner_surchargeable_et_noms_proteges():
    ci = yaml.safe_load((RACINE / ".github/workflows/ci.yml").read_text())
    garde = yaml.safe_load((RACINE / ".github/workflows/guard-main.yml").read_text())
    qualite = ci["jobs"]["quality"]
    controle = garde["jobs"]["garde-dev-vers-main"]
    runner = "${{ fromJSON(vars.RUNS_ON_LINUX || '\"ubuntu-latest\"') }}"
    assert qualite["runs-on"] == controle["runs-on"] == runner
    for branche in ("dev", "main"):
        protection = json.loads((RACINE / f".github/branch-protection/{branche}.json").read_text())
        requis = {c["context"] for c in protection["required_status_checks"]["checks"]}
        assert qualite["name"] in requis
        if branche == "main":
            assert controle["name"] in requis
    commandes = [e.get("run", "") for e in qualite["steps"]]
    assert "make lint" in commandes and "make test" in commandes
    assert any("yamllint==1.38.0 --strict" in c for c in commandes)
    assert any("py_compile scripts/*.py" in c for c in commandes)
    assert '"${{ github.head_ref }}" != "dev"' in controle["steps"][0]["run"]
