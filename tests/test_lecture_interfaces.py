# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture par interface et échantillonneur (#30) : double échantillonnage, champs facultatifs."""

from __future__ import annotations

import copy
import csv
from pathlib import Path

import pytest
from test_lecture import DEBUT, MAPPING, FauxClient, _exportateur, _ligne, _minute, _resultats

from elastiflow_validation import injection, lecture

CHAMPS = lecture.CHAMPS_DEFAUT | {
    "interface_entree": "flow.input.ifindex",
    "interface_sortie": "flow.output.ifindex",
    "echantillonneur": "flow.sampler.ifindex",
}


def _avec_interfaces(
    nom: str, couples: dict[int, dict[int, float]], echantillonneurs: list[int]
) -> dict:
    exp = _exportateur(nom, [_minute(DEBUT - 10 + 120, 500.0, 5.0)])
    exp["entrees"] = {
        "buckets": [
            {
                "key": e,
                "sorties": {
                    "buckets": [{"key": s, "octets": {"value": v}} for s, v in sorties.items()]
                },
            }
            for e, sorties in couples.items()
        ]
    }
    exp["echantillonneurs"] = {"buckets": [{"key": k, "doc_count": 6} for k in echantillonneurs]}
    return exp


def _colonnes(ligne: list[str]) -> dict[str, str]:
    return dict(zip(lecture.COLONNES_LECTURE, ligne, strict=True))


def test_requete_avec_et_sans_champs_facultatifs():
    banc = injection.Banc()
    aggs = lecture.requete_test(_ligne(), CHAMPS, banc)["aggs"]["exportateurs"]["aggs"]
    assert aggs["entrees"]["terms"] == {"field": "flow.input.ifindex", "size": 10, "missing": -1}
    sorties = aggs["entrees"]["aggs"]["sorties"]
    assert sorties["terms"]["field"] == "flow.output.ifindex" and "octets" in sorties["aggs"]
    assert aggs["echantillonneurs"]["terms"]["field"] == "flow.sampler.ifindex"
    nues = lecture.requete_test(_ligne(), lecture.CHAMPS_DEFAUT, banc)["aggs"]["exportateurs"][
        "aggs"
    ]
    assert "entrees" not in nues and "echantillonneurs" not in nues
    # une interface seule ne suffit pas pour les couples
    seule = lecture.CHAMPS_DEFAUT | {"interface_entree": "flow.input.ifindex"}
    assert (
        "entrees" not in lecture.requete_test(_ligne(), seule, banc)["aggs"]["exportateurs"]["aggs"]
    )


def test_double_echantillonnage_signale():
    reponse = {
        "aggregations": {
            "exportateurs": {
                "buckets": [
                    _avec_interfaces(
                        "sw-a", {12: {3: 4500.0}}, [12, 3]
                    ),  # entrée de 12 ET sortie de 3
                    _avec_interfaces("sw-b", {7: {9: 300.0, -1: 20.0}}, [7]),
                ]
            }
        }
    }
    a, b = (
        _colonnes(x)
        for x in lecture.lire_test(FauxClient([reponse]), "i", _ligne(), CHAMPS, injection.Banc())
    )
    assert (a["interfaces"], a["echantillonneurs"], a["double_echantillonnage"]) == (
        "12>3=4500",
        "12;3",
        "oui",
    )
    assert (b["interfaces"], b["echantillonneurs"], b["double_echantillonnage"]) == (
        "7>9=300;7>-1=20",
        "7",
        "",
    )


def test_sans_champs_facultatifs_colonnes_vides():
    reponse = {"aggregations": {"exportateurs": {"buckets": [_exportateur("sw-a", [])]}}}
    (a,) = lecture.lire_test(
        FauxClient([reponse]), "i", _ligne(), lecture.CHAMPS_DEFAUT, injection.Banc()
    )
    c = _colonnes(a)
    assert (c["interfaces"], c["echantillonneurs"], c["double_echantillonnage"]) == ("", "", "")


def _mapping_avec_interfaces() -> dict:
    m = copy.deepcopy(MAPPING)
    flow = m["elastiflow-flow-2026.10.26"]["mappings"]["properties"]["flow"]["properties"]
    flow["input"] = {"properties": {"ifindex": {"type": "long"}}}
    flow["output"] = {"properties": {"ifindex": {"type": "long"}}}
    return m


def test_lire_ajoute_les_interfaces_presentes_et_ignore_les_absentes(tmp_path: Path):
    resultats = _resultats(tmp_path, [_ligne()])
    reponse = {
        "aggregations": {
            "exportateurs": {"buckets": [_avec_interfaces("sw-a", {12: {3: 1.0}}, [])]}
        }
    }
    # mapping avec interfaces : défauts ajoutés, échantillonneur sans défaut
    client = FauxClient([reponse], mapping=_mapping_avec_interfaces())
    sortie = tmp_path / "lectures.csv"
    assert lecture.lire(resultats, sortie, client, index="i") == 1
    aggs = client.appels[1][2]["aggs"]["exportateurs"]["aggs"]
    assert "entrees" in aggs and "echantillonneurs" not in aggs
    with sortie.open(encoding="utf-8", newline="") as f:
        assert next(csv.DictReader(f))["interfaces"] == "12>3=1"
    # mapping sans interfaces ni échantillonneur demandé : lecture quand même, sans sous-totaux
    client = FauxClient([reponse])
    assert lecture.lire(resultats, sortie, client, index="i", champs=CHAMPS) == 1
    aggs = client.appels[1][2]["aggs"]["exportateurs"]["aggs"]
    assert "entrees" not in aggs and "echantillonneurs" not in aggs


def test_charger_champs_admet_les_cles_facultatives(tmp_path: Path):
    f = tmp_path / "champs.yaml"
    f.write_text("echantillonneur: flow.sampler.ifindex\n", "utf-8")
    assert lecture.charger_champs(f)["echantillonneur"] == "flow.sampler.ifindex"
    f.write_text("inconnu: x\n", "utf-8")
    with pytest.raises(ValueError, match="echantillonneur"):
        lecture.charger_champs(f)
