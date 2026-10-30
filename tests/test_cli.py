# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de la ligne de commande et de la configuration du journal."""

from __future__ import annotations

import argparse
import runpy
import sys

import pytest

from elastiflow_validation import __version__, cli, journal


def test_version(capsys):
    with pytest.raises(SystemExit) as fin:
        cli.main(["--version"])
    assert fin.value.code == 0
    assert capsys.readouterr().out.strip() == f"efv {__version__}"


def test_sans_commande_affiche_aide(capsys):
    assert cli.main([]) == 0
    assert "usage: efv" in capsys.readouterr().out


def test_commande_appelle_son_action():
    parseur = cli.construire_parseur()
    sous = next(a for a in parseur._actions if isinstance(a, argparse._SubParsersAction))
    sous.add_parser("essai").set_defaults(action=lambda args: 3)
    original = cli.construire_parseur
    cli.construire_parseur = lambda: parseur
    try:
        assert cli.main(["essai"]) == 3
    finally:
        cli.construire_parseur = original


@pytest.mark.parametrize(
    ("debug", "env", "attendu"),
    [(False, None, "INFO"), (True, None, "DEBUG"), (False, "1", "DEBUG"), (False, "0", "INFO")],
)
def test_niveau(monkeypatch, debug, env, attendu):
    monkeypatch.delenv("SOCLE_DEBUG", raising=False)
    if env is not None:
        monkeypatch.setenv("SOCLE_DEBUG", env)
    assert journal.niveau(debug) == attendu
    assert journal.configurer(debug) == attendu


def test_module_executable(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["efv"])
    with pytest.raises(SystemExit) as fin:
        runpy.run_module("elastiflow_validation", run_name="__main__")
    assert fin.value.code == 0
    assert "usage: efv" in capsys.readouterr().out
