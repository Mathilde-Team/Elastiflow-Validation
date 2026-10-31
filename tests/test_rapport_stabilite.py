# SPDX-License-Identifier: GPL-3.0-or-later
"""Le volume conforme ne masque pas un débit instable (#31)."""

import csv
from dataclasses import replace

import pytest
from test_lecture import FauxClient, _ligne
from test_lecture_interfaces import CHAMPS, _avec_interfaces
from test_rapport import _lecture, _lectures, _resultats, _test_reference
from test_rapport_interfaces import _lectures_detaillees

from elastiflow_validation import cli, injection, lecture, rapport, stabilite


def _analyses(tmp_path, ident="A3"):
    return rapport.analyser(
        _resultats(tmp_path, [_test_reference(ident)]),
        _lectures(tmp_path, [_lecture(ident)]),
    )[0]


def _minutes(a):
    bas, haut = lecture.fenetre_utile(a.debut, a.fin)
    return [stabilite.Minute(m, 1000, 1_000_000, 1000) for m in range(bas, haut, 60)]


def test_rapport_stable_et_instable_cli(tmp_path):
    a = _analyses(tmp_path)[0]
    series = tmp_path / "series.csv"
    sortie = tmp_path / "rapport.md"
    minutes = _minutes(a)
    args = [
        "rapport",
        "--resultats",
        str(tmp_path / "resultats.csv"),
        "--lectures",
        str(tmp_path / "lectures.csv"),
        "--sortie",
        str(sortie),
        "--series",
        str(series),
    ]
    stabilite.ecrire_series(series, [("A3", "1", "sw-a", minutes)])
    assert cli.main(args) == 0
    assert "| A3 (1) | sw-a |  | oui | 0 | 0 |" in sortie.read_text()
    # Retirer une ligne plutôt que la remplacer par zéro : elle doit être reconstituée.
    stabilite.ecrire_series(series, [("A3", "1", "sw-a", minutes[1:])])
    assert cli.main(args) == 0
    texte = sortie.read_text()
    assert "1 minute(s) sans échantillon" in texte
    assert "| A3 (1) | sw-a |  | non | 1 |" in texte
    assert "toutes conformes, critère rempli" in texte  # critère de volume uniquement
    minutes[4] = replace(minutes[4], octets=2_000_000)
    stabilite.ecrire_series(series, [("A3", "1", "sw-a", minutes)])
    assert rapport.ajouter_stabilite([a], series)[0].stabilite.pics > 0


def test_sans_serie_non_evalue(tmp_path):
    texte = rapport.construire_document(_analyses(tmp_path), [])
    assert "non évalué" in texte and "fournir --series" in texte


def test_jointure_ne_confond_pas_repetition_exportateur_et_point(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures_detaillees(tmp_path)
    a = rapport.analyser(resultats, lectures, {"sw-a": "12"})[0][0]
    series = tmp_path / "series.csv"
    minutes = _minutes(a)
    stabilite.ecrire_series(
        series,
        [
            ("A3", "1", "sw-a", minutes),  # total, jamais repli pour le point demandé
            ("A3", "2", "sw-a", minutes, "12"),
            ("A3", "1", "sw-b", minutes, "12"),
            ("A3", "1", "sw-a", minutes, "3"),
        ],
    )
    assert not rapport.ajouter_stabilite([a], series)[0].stabilite.stable
    stabilite.ecrire_series(series, [("A3", "1", "sw-a", minutes, "12")])
    assert rapport.ajouter_stabilite([a], series)[0].stabilite.stable


def test_anciens_csv_et_autres_series(tmp_path):
    a = _analyses(tmp_path)[0]
    path = tmp_path / "series.csv"
    stabilite.ecrire_series(path, [("A3", "1", "sw-a", _minutes(a))])
    with path.open() as f:
        lignes = list(csv.DictReader(f))
    colonnes = [c for c in stabilite.COLONNES_SERIE if c != "echantillonneur"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(lignes)
    assert rapport.ajouter_stabilite([a], path)[0].stabilite.stable
    c2 = replace(a, id="C2-4096")
    b1 = replace(a, id="B1")
    resultats = rapport.ajouter_stabilite([c2, b1], path)
    assert resultats[0].stabilite is not None
    assert resultats[1].stabilite is None


@pytest.mark.parametrize(
    "modification",
    [
        {"minute": "1081"},
        {"echantillons": "-1"},
        {"octets": "nan"},
        {"paquets": "inf"},
        {"octets": "-1"},
    ],
)
def test_minutes_invalides(tmp_path, modification):
    path = tmp_path / "series.csv"
    ligne = {
        "id": "A3",
        "repetition": "1",
        "exportateur": "sw-a",
        "minute": "1080",
        "echantillons": "1000",
        "octets": "1000000",
        "paquets": "1000",
    } | modification
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ligne))
        writer.writeheader()
        writer.writerow(ligne)
    with pytest.raises(ValueError, match="minute invalide"):
        rapport.ajouter_stabilite([], path)


def test_colonnes_et_minutes_dupliquees(tmp_path):
    a = _analyses(tmp_path)[0]
    path = tmp_path / "series.csv"
    path.write_text("")
    with pytest.raises(ValueError, match="colonnes"):
        rapport.ajouter_stabilite([a], path)
    minutes = _minutes(a)
    stabilite.ecrire_series(path, [("A3", "1", "sw-a", [*minutes, minutes[0]])])
    with pytest.raises(ValueError, match="dupliquée"):
        rapport.ajouter_stabilite([a], path)


@pytest.mark.parametrize(("bord", "cv"), [(-1, 0), (60, -1), (60, float("nan"))])
def test_parametres_invalides(tmp_path, bord, cv):
    with pytest.raises(ValueError, match="bord et CV"):
        rapport.ajouter_stabilite([], tmp_path / "absent", bord, cv)


def test_collecte_par_point_et_verdicts(tmp_path):
    exp = _avec_interfaces("sw-a", {12: {3: 16000000}}, [12, 3])
    for minute in exp["minutes"]["buckets"]:
        minute["doc_count"] = 1000
    ligne = _ligne()
    bas, haut = lecture.fenetre_utile(int(ligne["debut"]), int(ligne["fin"]))
    for point in exp["echantillonneurs"]["buckets"]:
        point.update(
            octets={"value": 8000000},
            paquets={"value": 8000},
            minutes={
                "buckets": [
                    {
                        "key": m * 1000,
                        "doc_count": 1000,
                        "octets": {"value": 1000000},
                        "paquets": {"value": 1000},
                    }
                    for m in range(bas, haut, 60)
                ]
            },
        )
    collecte = []
    lecture.lire_test(
        FauxClient([{"aggregations": {"exportateurs": {"buckets": [exp]}}}]),
        "i",
        ligne,
        CHAMPS,
        injection.Banc(),
        series=collecte,
    )
    assert len(collecte) == 3 and collecte[1][4] == "12" and collecte[2][4] == "3"
    path = tmp_path / "verdicts.csv"
    stabilite.ecrire_verdicts(path, collecte)
    with path.open() as f:
        lignes = list(csv.DictReader(f))
    assert [v["echantillonneur"] for v in lignes] == ["", "12", "3"]
    assert lignes[1]["stable"] == "oui"
