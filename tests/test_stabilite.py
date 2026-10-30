# SPDX-License-Identifier: GPL-3.0-or-later
"""Constance du débit (#31) : série par minute, critère, fichiers, branchement dans efv lire."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import pytest
from test_lecture import FIN, FauxClient, _exportateur, _ligne, _resultats

from elastiflow_validation import cli, lecture, stabilite
from elastiflow_validation.stabilite import Minute

TAILLE = 1000  # octets IP (série A)
SAMPLING = 8


def _serie_simulee(graine: int, minutes: int = 28, paquets_minute: int = 7500) -> list[Minute]:
    """Débit constant (1 Mbit/s, paquets de 1000 o), échantillonnage 1:8 tiré au hasard."""
    alea = random.Random(graine)
    serie = []
    for i in range(minutes):
        # Nombre d'échantillons de Poisson (cas le plus dispersé, grand sampling-rate) approché
        # par une normale de variance égale à la moyenne (937,5).
        moyenne = paquets_minute / SAMPLING
        n = max(0, round(alea.gauss(moyenne, math.sqrt(moyenne))))
        serie.append(Minute(1_800_000_060 + 60 * i, n, float(n * TAILLE), float(n)))
    return serie


def test_depassements_toleres():
    assert [stabilite.depassements_toleres(m) for m in (0, 1, 10, 22, 28)] == [0, 1, 2, 3, 4]
    # 22 mesures (#9) : P(X > 3) = 2,2 % ≤ 5 % < P(X > 2) = 9,5 %
    assert stabilite.depassements_toleres(22, risque=0.05) == 3
    assert stabilite.depassements_toleres(22, risque=0.10) == 2
    for mauvais in ((-1, 0.05, 0.05), (5, 1.5, 0.05), (5, 0.05, 0), (5, 0.05, 1)):
        with pytest.raises(ValueError):
            stabilite.depassements_toleres(*mauvais)


def test_facteur_pic():
    assert stabilite.facteur_pic(1) == 1.5
    assert stabilite.facteur_pic(28) == pytest.approx(1.82, abs=0.01)
    for mauvais in ((0, 0.01), (5, 0), (5, 1)):
        with pytest.raises(ValueError):
            stabilite.facteur_pic(*mauvais)


def test_serie_minutes_complete_les_trous():
    buckets = [
        {"key": 120_000, "doc_count": 3, "octets": {"value": 300.0}, "paquets": {"value": 3.0}},
        {"key": 240_000, "doc_count": 1, "octets": {"value": 100.0}, "paquets": {"value": 1.0}},
        {"key": 600_000, "doc_count": 9, "octets": {"value": 900.0}, "paquets": {"value": 9.0}},
    ]
    serie = stabilite.serie_minutes(buckets, 120, 300)
    assert serie == [
        Minute(120, 3, 300.0, 3.0),
        Minute(180, 0, 0.0, 0.0),
        Minute(240, 1, 100.0, 1.0),
    ]
    assert stabilite.serie_minutes(buckets, 120, 120) == []


def test_serie_stable_acceptee_dans_au_moins_95_pour_cent_des_tirages():
    acceptees = sum(stabilite.juger(_serie_simulee(g)).stable for g in range(1000))
    assert acceptees >= 950


def test_serie_stable_acceptee_et_details():
    v = stabilite.juger(_serie_simulee(1))
    assert v.stable and v.motif == ""
    assert v.minutes == 28 and v.minutes_vides == 0 and v.toleres == 4
    assert len(v.ecarts) == len(v.bornes) == 28
    assert abs(sum(v.ecarts)) < 1e-6  # écarts à la moyenne de la série
    assert v.bornes[0] == pytest.approx(196 / math.sqrt(_serie_simulee(1)[0].echantillons))


def test_minute_manquante_refusee():
    serie = _serie_simulee(2)
    serie[10] = Minute(serie[10].debut, 0, 0.0, 0.0)
    v = stabilite.juger(serie)
    assert not v.stable and v.minutes_vides == 1
    assert v.motif == "1 minute(s) sans échantillon"


def test_pic_hors_borne_refuse():
    serie = _serie_simulee(3)
    x = serie[5]
    serie[5] = Minute(x.debut, x.echantillons * 2, x.octets * 2, x.paquets * 2)
    v = stabilite.juger(serie)
    assert not v.stable and v.pics >= 1
    assert "au-delà de 1.82 fois la borne" in v.motif


def test_trop_de_depassements():
    # Écarts de ±8 % pour une borne de 196/√1000 ≈ 6,2 % : hors borne, sous le seuil des pics.
    base = [Minute(60 * i, 1000, 1000.0, 1.0) for i in range(20)]
    alterne = [
        Minute(x.debut, x.echantillons, x.octets * (1.08 if i % 2 else 0.92), x.paquets)
        for i, x in enumerate(base)
    ]
    v = stabilite.juger(alterne)
    assert not v.stable and v.depassements == 20 and v.pics == 0
    assert v.motif == f"20 minute(s) hors borne pour {v.toleres} tolérée(s)"
    # Autant de minutes hors borne que tolérées : hasard accepté.
    tolere = list(base)
    for i in range(v.toleres):
        tolere[i] = Minute(base[i].debut, 1000, 1080.0, 1.0)
    assert stabilite.juger(tolere).stable


def test_cv_elargit_la_borne():
    serie = [Minute(60 * i, 1000, 1000.0 * (1.07 if i % 2 else 0.93), 1.0) for i in range(20)]
    assert not stabilite.juger(serie).stable
    assert stabilite.juger(serie, cv=0.6).stable  # borne × √1,36 ≈ 7,2 %


def test_serie_vide_ou_sans_octets():
    for serie in ([], [Minute(0, 0, 0.0, 0.0)]):
        v = stabilite.juger(serie)
        assert not v.stable and v.motif == "aucune minute utile avec des octets"
        assert v.ecarts == () and v.bornes == ()


def _lire_csv(chemin: Path) -> list[dict]:
    with chemin.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_ecrire_series_et_verdicts(tmp_path: Path):
    stable = _serie_simulee(1, minutes=4)
    trou = [*stable[:2], Minute(stable[2].debut, 0, 0.0, 0.0), stable[3]]
    series = [("A3", "1", "sw-a", stable), ("A3", "1", "sw-b", trou), ("A1", "2", "sw-a", [])]
    chemin = tmp_path / "series.csv"
    stabilite.ecrire_series(chemin, series)
    lignes = _lire_csv(chemin)
    assert tuple(lignes[0]) == stabilite.COLONNES_SERIE
    assert len(lignes) == 8
    assert lignes[0]["id"] == "A3" and lignes[0]["hors_borne"] == "non"
    assert lignes[0]["octets"] == str(round(stable[0].octets))
    vide = lignes[6]
    assert vide["exportateur"] == "sw-b" and vide["echantillons"] == "0"
    assert vide["borne_pct"] == "" and vide["hors_borne"] == "vide"
    verdicts = tmp_path / "stabilite.csv"
    assert stabilite.ecrire_verdicts(verdicts, series) == 2
    lus = _lire_csv(verdicts)
    assert [v["stable"] for v in lus] == ["oui", "non", "non"]
    assert lus[1]["motif"] == "1 minute(s) sans échantillon"
    assert lus[2]["minutes"] == "0"


def _minute_es(debut_s: int, n: int, octets: float) -> dict:
    return {
        "key": debut_s * 1000,
        "doc_count": n,
        "octets": {"value": octets},
        "paquets": {"value": float(n)},
    }


def test_efv_lire_series_et_stabilite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    resultats = _resultats(tmp_path, [_ligne()])
    # Fenêtre utile de _ligne() (5 min, bords de 60 s) : 2 minutes ; la 2e manque chez sw-b.
    bas, haut = lecture.fenetre_utile(int(_ligne()["debut"]), FIN)
    minutes = [_minute_es(m, 1000, 1_000_000.0) for m in range(bas, haut, 60)]
    reponse = {
        "aggregations": {
            "exportateurs": {
                "buckets": [
                    _exportateur("sw-a", minutes),
                    _exportateur("sw-b", [minutes[0]]),
                ]
            }
        }
    }
    monkeypatch.setattr(cli.time, "time", lambda: FIN + 10_000)
    sortie, series, verdicts = (tmp_path / n for n in ("l.csv", "s.csv", "v.csv"))
    args = ["lire", "--resultats", str(resultats), "--sortie", str(sortie), "--index", "idx"]
    args += ["--series", str(series), "--stabilite", str(verdicts), "--cv", "0.1"]
    assert cli.commande_lire(cli.construire_parseur().parse_args(args), FauxClient([reponse])) == 0
    assert len(minutes) == 2 and len(_lire_csv(series)) == 4
    assert [(v["exportateur"], v["stable"]) for v in _lire_csv(verdicts)] == [
        ("sw-a", "oui"),
        ("sw-b", "non"),
    ]
    # --stabilite seul, séries toutes stables.
    stable = {"aggregations": {"exportateurs": {"buckets": [_exportateur("sw-a", minutes)]}}}
    lecture.lire(resultats, sortie, FauxClient([stable]), index="idx", verdicts=verdicts)
    assert [v["stable"] for v in _lire_csv(verdicts)] == ["oui"]
    # Sans --series ni --stabilite : aucun fichier de plus.
    autre = tmp_path / "autre"
    autre.mkdir()
    lecture.lire(resultats, autre / "l.csv", FauxClient([reponse]), index="idx")
    assert [p.name for p in autre.iterdir()] == ["l.csv"]
