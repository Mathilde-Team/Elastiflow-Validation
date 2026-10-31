# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-régression du point de mesure de bout en bout (#30)."""

import csv
import json

import pytest
import yaml
from test_lecture import FauxClient, _ligne
from test_lecture_interfaces import CHAMPS, _avec_interfaces
from test_rapport import _lecture, _resultats, _test_reference

from elastiflow_validation import cli, injection, lecture, rapport


def _lectures_detaillees(tmp_path, double="oui"):
    volumes = {
        "12": {"octets": 8000000, "paquets": 8000, "enregistrements": 1000},
        "3": {"octets": 8000000, "paquets": 8000, "enregistrements": 1000},
    }
    ligne = _lecture("A3", octets="16000000", paquets="16000") | {
        "echantillonneurs": "12;3",
        "double_echantillonnage": double,
        "volumes_echantillonneurs": json.dumps(volumes),
    }
    chemin = tmp_path / "lectures.csv"
    with chemin.open("w", newline="") as fichier:
        writer = csv.DictWriter(fichier, fieldnames=list(ligne))
        writer.writeheader()
        writer.writerow(ligne)
    return chemin


def test_volumes_lus_par_echantillonneur(tmp_path):
    exp = _avec_interfaces("sw-a", {12: {3: 16000000}}, [12, 3])
    for b in exp["echantillonneurs"]["buckets"]:
        b.update(octets={"value": 8000000}, paquets={"value": 8000}, doc_count=1000)
    reponse = {"aggregations": {"exportateurs": {"buckets": [exp]}}}
    (ligne,) = lecture.lire_test(FauxClient([reponse]), "i", _ligne(), CHAMPS, injection.Banc())
    volumes = json.loads(
        dict(zip(lecture.COLONNES_LECTURE, ligne, strict=True))["volumes_echantillonneurs"]
    )
    assert volumes["12"] == {"octets": 8000000, "paquets": 8000, "enregistrements": 1000}
    agg = lecture.requete_test(_ligne(), CHAMPS, injection.Banc())["aggs"]["exportateurs"]["aggs"][
        "echantillonneurs"
    ]
    assert agg["terms"]["missing"] == -1
    assert set(agg["aggs"]) == {"octets", "paquets", "minutes"}


@pytest.mark.parametrize("indicateur", ["sum_other_doc_count", "doc_count_error_upper_bound"])
def test_agregation_tronquee_refusee(indicateur):
    with pytest.raises(ValueError, match="tronqués"):
        lecture._volumes_echantillonneurs({"echantillonneurs": {indicateur: 1}})


@pytest.mark.parametrize("double", ["oui", ""])
def test_double_non_selectionne_refuse(tmp_path, double):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    with pytest.raises(ValueError, match="point-mesure"):
        rapport.analyser(resultats, _lectures_detaillees(tmp_path, double))


def test_selection_et_calibration_cli(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures_detaillees(tmp_path)
    analyses, _ = rapport.analyser(resultats, lectures, {"sw-a": "12"})
    a = analyses[0]
    assert (a.point_mesure, a.octets_elastiflow, a.paquets_elastiflow) == ("12", 8000000, 8000)
    assert a.enregistrements == 1000 and a.conforme is True and a.erreur_pct == 0
    sortie, calibration = tmp_path / "rapport.md", tmp_path / "cal.yaml"
    assert (
        cli.main(
            [
                "rapport",
                "--resultats",
                str(resultats),
                "--lectures",
                str(lectures),
                "--sortie",
                str(sortie),
                "--point-mesure",
                "sw-a=12",
                "--calibration",
                str(calibration),
            ]
        )
        == 0
    )
    assert "ifindex échantillonneur) | 12" in sortie.read_text()
    assert yaml.safe_load(calibration.read_text())["elastiflow"]["sampling_already_applied"] is True


@pytest.mark.parametrize(
    "volume",
    [
        "",
        "{}",
        "[]",
        '{"12": null}',
        '{"12": {"octets": -1}}',
        '{"12": {"octets": true, "paquets": 1, "enregistrements": 1}}',
    ],
)
def test_selection_absente_ou_invalide(volume):
    with pytest.raises(ValueError):
        rapport._selectionner(
            {"exportateur": "sw-a", "volumes_echantillonneurs": volume}, {"sw-a": "12"}
        )


@pytest.mark.parametrize("choix", [["sw-a"], ["=12"], ["sw-a=-1"], ["sw-a=1", "sw-a=2"]])
def test_points_invalides(choix):
    with pytest.raises(ValueError, match="EXPORTATEUR=IFINDEX"):
        rapport.points_mesure(choix)


def test_points_valides():
    assert rapport.points_mesure([]) == {}
    assert rapport.points_mesure(["sw-a=12", "sw-b=3"]) == {"sw-a": "12", "sw-b": "3"}
