# SPDX-License-Identifier: GPL-3.0-or-later
"""Verdict global avec correction de Bonferroni (#9) : borne z ÷ √n, z recalculé selon m."""

from __future__ import annotations

import math
import random

import pytest

from elastiflow_validation import rapport
from elastiflow_validation.calcul import z_bonferroni
from elastiflow_validation.rapport import Analyse, bornes_bonferroni


def _analyse(i: int, erreur: float | None, n: float = 1000.0) -> Analyse:
    return Analyse(
        id=f"A{i}",
        repetition="1",
        exportateur="sw1",
        protocole="udp",
        debit="10M",
        taille="1000",
        tos="0",
        debut=0,
        fin=1,
        paquets_reference=int(n),
        octets_reference=1,
        octets_elastiflow=1,
        paquets_elastiflow=1,
        enregistrements=1,
        sampling=1,
        sampling_lu="1",
        n_attendu=n,
        erreur_pct=erreur,
        borne_pct=None,
        conforme=None,
    )


def test_z_bonferroni_valeurs_de_reference():
    assert z_bonferroni(1) == pytest.approx(196.0, abs=0.01)
    assert z_bonferroni(22) == pytest.approx(305.0, abs=0.5)
    assert z_bonferroni(22) > z_bonferroni(11) > z_bonferroni(1)


@pytest.mark.parametrize(("mesures", "alpha"), [(0, 0.05), (-3, 0.05), (5, 0.0), (5, 1.0)])
def test_z_bonferroni_parametres_invalides(mesures, alpha):
    with pytest.raises(ValueError):
        z_bonferroni(mesures, alpha)


def test_borne_corrigee_pour_22_mesures():
    # 8 % d'erreur, n = 1000 : hors de 196/√n (6,2 %) mais dans 305/√n (9,6 %) pour 22 mesures
    analyses = bornes_bonferroni([_analyse(i, 8.0 if i == 0 else 0.0) for i in range(22)])
    assert analyses[0].borne_pct == pytest.approx(z_bonferroni(22) / math.sqrt(1000.0))
    assert analyses[0].conforme is True
    assert bornes_bonferroni([_analyse(0, 8.0)])[0].conforme is False


def test_mesures_non_calculables_hors_de_m():
    analyses = bornes_bonferroni([_analyse(0, 1.0), _analyse(1, None), _analyse(2, 1.0, n=0.0)])
    assert analyses[0].borne_pct == pytest.approx(z_bonferroni(1) / math.sqrt(1000.0))
    assert analyses[1].borne_pct is None and analyses[1].conforme is None
    assert analyses[2].borne_pct is None and analyses[2].conforme is None
    assert bornes_bonferroni([_analyse(0, None)])[0].conforme is None


def test_instrument_juste_valide_dans_au_moins_95_pourcent_des_tirages():
    # erreur ~ N(0, 100/√n) : la borne individuelle 196/√n vaut 1,96 σ
    tirages = 2000
    hasard = random.Random(9)
    valides = 0
    for _ in range(tirages):
        n = 1000.0
        mesures = [_analyse(i, hasard.gauss(0.0, 100.0 / math.sqrt(n)), n) for i in range(22)]
        if all(a.conforme for a in bornes_bonferroni(mesures)):
            valides += 1
    assert valides / tirages >= 0.95


def test_conclusion_indique_le_critere_global():
    ok = rapport._conclusion(bornes_bonferroni([_analyse(i, 0.0) for i in range(22)]))
    texte = "\n".join(ok)
    assert "(protocole §10.3, Bonferroni sur m = 22 mesures, z = 3,05) : " in texte
    assert "22 / 22 mesures dans leur borne ; toutes conformes, critère rempli." in texte
    ko = "\n".join(rapport._conclusion(bornes_bonferroni([_analyse(0, 50.0), _analyse(1, 0.0)])))
    assert "1 / 2 mesures dans leur borne ; critère non rempli." in ko
    assert "Critère global" not in "\n".join(rapport._conclusion([_analyse(0, None)]))
