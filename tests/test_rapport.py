# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests du rapport de validation : comparaison, bornes à 95 % et document VAL-ELASTIFLOW-001."""

from __future__ import annotations

import csv
import math
import random
import statistics
from pathlib import Path

import pytest
import yaml

from elastiflow_validation import cli, lecture, rapport

COLONNES_RESULTATS = (
    "id,repetition,serie,groupe,protocole,debit,taille,tos,ports,sampling,debut,fin,"
    "paquets,octets,client,serveur,port_destination"
).split(",")
COLONNES_LECTURES = (
    "id,repetition,exportateur,enregistrements,octets,paquets,octets_utiles,paquets_utiles,"
    "duree_utile_s,debit_utile_bps,sampling,dscp,debut_fenetre,fin_fenetre"
).split(",")


def _resultats(tmp_path: Path, lignes: list[dict]) -> Path:
    """Écrit un ``resultats.csv`` complet à partir de lignes partielles."""
    chemin = tmp_path / "resultats.csv"
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.DictWriter(f, fieldnames=COLONNES_RESULTATS)
        ecrivain.writeheader()
        for ligne in lignes:
            ecrivain.writerow({c: ligne.get(c, "") for c in COLONNES_RESULTATS})
    return chemin


def _lectures(tmp_path: Path, lignes: list[dict]) -> Path:
    """Écrit un ``lectures.csv`` complet à partir de lignes partielles."""
    chemin = tmp_path / "lectures.csv"
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.DictWriter(f, fieldnames=COLONNES_LECTURES)
        ecrivain.writeheader()
        for ligne in lignes:
            ecrivain.writerow({c: ligne.get(c, "") for c in COLONNES_LECTURES})
    return chemin


def _test_reference(
    id: str,
    repetition: str = "1",
    sampling: str = "8",
    paquets: str = "8000",
    octets: str = "8000000",
) -> dict:
    """Ligne de référence nft : 1000 paquets/s pendant 30 min, base IP."""
    return {
        "id": id,
        "repetition": repetition,
        "sampling": sampling,
        "debut": "1000",
        "fin": "2800",
        "paquets": paquets,
        "octets": octets,
        "protocole": "udp",
        "debit": "5%",
        "taille": "1000",
        "tos": "0x00",
    }


def _lecture(
    id: str,
    repetition: str = "1",
    exportateur: str = "sw-a",
    octets: str = "8040000",
    paquets: str = "8040",
    sampling: str = "8",
) -> dict:
    """Ligne lue dans ElastiFlow, un exportateur."""
    return {
        "id": id,
        "repetition": repetition,
        "exportateur": exportateur,
        "enregistrements": "1000",
        "octets": octets,
        "paquets": paquets,
        "sampling": sampling,
    }


def test_analyser_calcul_erreur_borne_conformite(tmp_path):
    # +0,5 % d'octets, n attendu = 8000 ÷ 8 = 1000 échantillons, borne = 196/√1000 ≈ 6,2 %
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    analyses, non_lus = rapport.analyser(resultats, lectures)
    assert non_lus == []
    assert len(analyses) == 1
    a = analyses[0]
    assert a.erreur_pct == pytest.approx(0.5)
    assert a.n_attendu == pytest.approx(1000.0)
    # m = 1 mesure : la borne Bonferroni se confond avec la borne à 95 % (196/√n).
    assert a.borne_pct == pytest.approx(rapport._z_bonferroni(1) * 100.0 / math.sqrt(1000.0))
    assert a.borne95_pct == pytest.approx(196.0 / math.sqrt(1000.0))
    assert a.conforme is True
    assert a.cle == "A3 (1)"


def test_analyser_non_conforme_hors_borne(tmp_path):
    # +25 % d'octets avec n = 1000 échantillons (borne ≈ 6,2 %) : non conforme.
    resultats = _resultats(tmp_path, [_test_reference("A4")])
    lectures = _lectures(tmp_path, [_lecture("A4", octets="10000000")])
    analyses, _ = rapport.analyser(resultats, lectures)
    assert analyses[0].erreur_pct == pytest.approx(25.0)
    assert analyses[0].conforme is False


def test_analyser_par_exportateur_et_non_lus(tmp_path):
    resultats = _resultats(
        tmp_path, [_test_reference("A2"), _test_reference("A2", "2"), _test_reference("A9")]
    )
    lectures = _lectures(
        tmp_path, [_lecture("A2", exportateur="sw-a"), _lecture("A2", exportateur="sw-b")]
    )
    analyses, non_lus = rapport.analyser(resultats, lectures)
    assert sorted(a.exportateur for a in analyses if a.id == "A2") == ["sw-a", "sw-b"]
    assert non_lus == ["A2 (2)", "A9 (1)"]


def test_analyser_sampling_repli_sur_la_lecture(tmp_path):
    # sampling absent du plan : premier sampling lu dans ElastiFlow (16)
    resultats = _resultats(tmp_path, [_test_reference("B1", sampling="", paquets="16000")])
    lectures = _lectures(tmp_path, [_lecture("B1", sampling="16")])
    analyses, _ = rapport.analyser(resultats, lectures)
    assert analyses[0].sampling == 16
    assert analyses[0].n_attendu == pytest.approx(1000.0)


def test_analyser_sans_reference_octets_non_calculable(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A1", octets="0")])
    lectures = _lectures(tmp_path, [_lecture("A1")])
    analyses, _ = rapport.analyser(resultats, lectures)
    assert analyses[0].erreur_pct is None
    assert analyses[0].conforme is None


def test_biais_moyen_sur_a2_a3_a5(tmp_path):
    lignes = (
        [_test_reference("A2", str(i), octets="1000000") for i in (1, 2, 3)]
        + [_test_reference("A3", str(i), octets="1000000") for i in (1, 2, 3)]
        + [_test_reference("A5", str(i), octets="1000000") for i in (1, 2, 3)]
        + [_test_reference("A4", octets="1000000")]
    )
    lectures_lignes = (
        [_lecture("A2", str(i), octets="1010000") for i in (1, 2, 3)]
        + [_lecture("A3", str(i), octets="990000") for i in (1, 2, 3)]
        + [_lecture("A5", str(i), octets="1000000") for i in (1, 2, 3)]
        + [_lecture("A4", octets="2000000")]
    )
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    # biais = moyenne de (+1 %, -1 %, 0 %) sur A2/A3/A5, A4 (100 %) exclu
    assert rapport.biais_moyen(analyses) == pytest.approx(0.0)
    assert rapport.biais_moyen([]) is None


def test_comparaison_sampling_apparie_4096(tmp_path):
    lignes = [_test_reference("A2-4096", sampling="4096"), _test_reference("A2", sampling="8")]
    lectures_lignes = [_lecture("A2-4096", octets="1000000"), _lecture("A2", octets="1010000")]
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    paires = rapport.comparaison_sampling(analyses)
    assert len(paires) == 1
    assert paires[0][0].id == "A2-4096"
    assert paires[0][1].id == "A2"


def test_construire_document_sections_remplies(tmp_path):
    lignes = [_test_reference("A2", str(i), octets="1000000") for i in (1, 2)] + [
        _test_reference("A9")
    ]
    lectures_lignes = [_lecture("A2", str(i), octets="1010000") for i in (1, 2)]
    analyses, non_lus = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_document(analyses, non_lus)
    assert "## 7. Fiches de test" in texte
    assert "A2 (1) — sw-a" in texte
    assert "## 8. Synthèse chiffrée des erreurs" in texte
    assert "| A2 (1) | sw-a | 1000000 | 1010000 |" in texte
    assert "**Biais moyen**" in texte
    assert "A9 (1)" in texte  # test sans lecture listé
    assert "## 10. Conclusion et décision" in texte
    assert "| Série A : débit constant | 2 / 2 | |" in texte
    assert "Verdict** : à compléter et signer" in texte


def test_construire_document_comparaison_sampling(tmp_path):
    lignes = [
        _test_reference("A1-4096", sampling="4096", octets="1000000"),
        _test_reference("A1", sampling="8", octets="1000000"),
    ]
    lectures_lignes = [_lecture("A1-4096", octets="990000"), _lecture("A1", octets="1000000")]
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_document(analyses, [])
    assert "Comparaison avant et après correction du sampling" in texte
    assert "| A1-4096 (1) | -1,00 | 0,00 |" in texte


def test_rapport_ecrit_le_document(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    sortie = tmp_path / "rapport-validation.md"
    assert rapport.rapport(resultats, lectures, sortie) == 1
    assert "## 8. Synthèse chiffrée des erreurs" in sortie.read_text(encoding="utf-8")


# --- Verdict global (protocole §10.3, issue #9 : option B, Bonferroni) ----------


def _mesure(
    erreur: float | None,
    n_attendu: float = 1000.0,
    id: str = "A1",
    m: int = 1,
) -> rapport.Analyse:
    """Analyse minimale : conformité à la borne Bonferroni z(m)·100/√n."""
    z = rapport._z_bonferroni(m)
    borne = z * 100.0 / n_attendu**0.5 if n_attendu else None
    borne95 = 196.0 / n_attendu**0.5 if n_attendu else None
    conforme = None if erreur is None or borne is None else abs(erreur) <= borne
    return rapport.Analyse(
        id=id,
        repetition="1",
        exportateur="sw-a",
        protocole="udp",
        debit="5%",
        taille="1000",
        tos="0x00",
        debut=0,
        fin=0,
        paquets_reference=1,
        octets_reference=1,
        octets_elastiflow=1,
        paquets_elastiflow=1,
        enregistrements=1,
        sampling=1,
        sampling_lu="",
        n_attendu=n_attendu,
        erreur_pct=erreur,
        borne_pct=borne,
        borne95_pct=borne95,
        conforme=conforme,
    )


def _tirage_sans_biais(nb: int, alea: random.Random) -> list[rapport.Analyse]:
    """Mesures d'un instrument juste : erreur ~ N(0, 100/√n), dépassement à 5 % par mesure."""
    return [
        _mesure(alea.gauss(0.0, 100.0 / 1000.0**0.5), id=f"T{i}", m=nb) for i in range(nb)
    ]


def test_z_bonferroni_selon_le_nombre_de_mesures():
    # z(1) = 1,96 (borne à 95 % simple) ; z(22) ≈ 3,05 (décision du 28/10/2026).
    assert rapport._z_bonferroni(1) == pytest.approx(1.96, abs=0.01)
    assert rapport._z_bonferroni(22) == pytest.approx(3.05, abs=0.01)
    assert rapport._z_bonferroni(0) == pytest.approx(1.96)


def test_verdict_toutes_dans_la_borne_bonferroni():
    # 22 mesures dont une au-delà de la borne à 95 % seule mais conforme à la
    # borne Bonferroni (critère de conformité et de verdict) : Validé.
    mesures = [_mesure(0.0, m=22)] * 21 + [_mesure(7.0, m=22)]
    v = rapport.verdict_global(mesures)
    assert v.verdict is True
    assert v.nb_mesures == 22
    assert v.nb_hors_borne == 0
    assert v.z_bonferroni == pytest.approx(3.05, abs=0.01)
    assert mesures[-1].borne95_pct < mesures[-1].erreur_pct < mesures[-1].borne_pct


def test_verdict_une_mesure_hors_borne_bonferroni_refuse():
    # 10,5 % > borne Bonferroni 3,05·100/√1000 ≈ 9,6 % : refusé.
    mesures = [_mesure(0.0, m=22)] * 21 + [_mesure(10.5, m=22)]
    v = rapport.verdict_global(mesures)
    assert v.verdict is False
    assert v.nb_hors_borne == 1


def test_verdict_jeu_incomplet_non_lus_non_calculable():
    mesures = [_mesure(0.0)] * 3
    v = rapport.verdict_global(mesures, non_lus=["A9 (1)"])
    assert v.verdict is None
    assert "sans lecture" in v.non_calculable


def test_verdict_mesure_non_calculable():
    v = rapport.verdict_global([_mesure(0.0), _mesure(None)])
    assert v.verdict is None
    assert "non calculable" in v.non_calculable


def test_verdict_aucune_mesure():
    assert rapport.verdict_global([]).verdict is None
    assert rapport.verdict_global([], non_lus=["A1 (1)"]).verdict is None


def test_verdict_jeu_complet_22_mesures_sans_biais():
    # Critère d'acceptation de l'issue #9, prouvé exact : P(toutes dans la borne
    # Bonferroni) = (1 − 2Φ(−z))²² ≥ 95 %.
    z = rapport._z_bonferroni(22)
    p_mesure = 2 * (1 - statistics.NormalDist().cdf(z))
    assert (1 - p_mesure) ** 22 >= 0.95


def test_verdict_monte_carlo_calibre_sur_la_probabilite_exacte():
    # Contrôle de calibration du tirage (graine fixe) : le taux observé sur
    # 2000 tirages est compatible avec la probabilité exacte (±3σ binomial).
    alea = random.Random(20261029)
    z = rapport._z_bonferroni(22)
    p_exact = (1 - 2 * (1 - statistics.NormalDist().cdf(z))) ** 22
    valides = sum(
        1 for _ in range(2000) if rapport.verdict_global(_tirage_sans_biais(22, alea)).verdict
    )
    sigma = (p_exact * (1 - p_exact) / 2000) ** 0.5
    assert abs(valides / 2000 - p_exact) <= 3 * sigma


def test_construire_document_contient_le_verdict(tmp_path):
    lignes = [_test_reference("A2", str(i), octets="1000000") for i in (1, 2)]
    lectures_lignes = [_lecture("A2", str(i), octets="1010000") for i in (1, 2)]
    analyses, non_lus = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_document(analyses, non_lus)
    assert "**Verdict global**" in texte
    assert "| 2 | 0 | 2.24 | Validé |" in texte  # z(2) = 2,24
    assert "**Verdict** : à compléter et signer" in texte  # la décision reste humaine


def test_construire_document_verdict_jeu_incomplet(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A1"), _test_reference("A9")])
    lectures = _lectures(tmp_path, [_lecture("A1")])  # A9 sans lecture
    analyses, non_lus = rapport.analyser(resultats, lectures)
    texte = rapport.construire_document(analyses, non_lus)
    assert "non calculable" in texte
    assert "sans lecture" in texte


def test_cli_rapport(tmp_path, capsys):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    sortie = tmp_path / "rapport.md"
    code = cli.main(
        [
            "rapport",
            "--resultats",
            str(resultats),
            "--lectures",
            str(lectures),
            "--sortie",
            str(sortie),
        ]
    )
    assert code == 0
    assert "1 analyse(s)" in capsys.readouterr().out
    assert sortie.exists()


def test_cli_rapport_fichier_absent(tmp_path, capsys):
    code = cli.main(
        [
            "rapport",
            "--resultats",
            str(tmp_path / "rien.csv"),
            "--lectures",
            str(tmp_path / "rien2.csv"),
        ]
    )
    assert code == 2


def test_construire_calibration_yaml_structure(tmp_path):
    """Le brouillon reprend P4 (champs traduits), P6 inféré et P2 (exportateurs, samplings)."""
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    analyses, _ = rapport.analyser(resultats, lectures)
    texte = rapport.construire_calibration_yaml(analyses, lecture.CHAMPS_DEFAUT, 7)
    cal = yaml.safe_load(texte)
    assert cal["reference"] == "VAL-ELASTIFLOW-001"
    assert cal["validated_on"] is None
    assert cal["elastiflow"]["fields"]["exporter"] == "flow.export.host.name"
    assert cal["elastiflow"]["fields"]["bytes"] == "network.bytes"
    assert cal["elastiflow"]["ingest_lag_minutes"] == 7
    # octets ElastiFlow ≈ référence : le facteur d'échantillonnage est déjà appliqué
    assert cal["elastiflow"]["sampling_already_applied"] is True
    assert cal["exporters"]["sw-a"]["sampling_direction"] == "inbound"
    assert cal["exporters"]["sw-a"]["validation"]["sampling_rates"] == [8]
    assert cal["exporters"]["sw-a"]["interfaces"] == []


def test_construire_calibration_p6_non_applique(tmp_path):
    """Octets ElastiFlow ≈ référence ÷ sampling : le facteur n'est pas appliqué."""
    lignes = [_test_reference("A2", sampling="8", octets="8000000", paquets="64000")]
    lectures_lignes = [_lecture("A2", octets="1000000", paquets="8000")]
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_calibration_yaml(analyses, {}, 10)
    assert yaml.safe_load(texte)["elastiflow"]["sampling_already_applied"] is False


def test_construire_calibration_p6_ambigu(tmp_path):
    """Ni l'un ni l'autre : la valeur reste à trancher (null), jamais devinée."""
    lignes = [_test_reference("A2", sampling="8", octets="8000000", paquets="64000")]
    lectures_lignes = [_lecture("A2", octets="4000000", paquets="32000")]
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_calibration_yaml(analyses, {}, 10)
    assert yaml.safe_load(texte)["elastiflow"]["sampling_already_applied"] is None


def test_construire_calibration_p5_octets_par_paquet(tmp_path):
    """Marge B/C : +14 octets par paquet (base trame) → bytes_overhead_per_packet = 14."""
    lignes = [_test_reference("B1", sampling="1", octets="1000000", paquets="10000")]
    lectures_lignes = [_lecture("B1", octets="1140000", paquets="10000", sampling="1")]
    analyses, _ = rapport.analyser(
        _resultats(tmp_path, lignes), _lectures(tmp_path, lectures_lignes)
    )
    texte = rapport.construire_calibration_yaml(analyses, {}, 10)
    assert yaml.safe_load(texte)["elastiflow"]["bytes_overhead_per_packet"] == 14


def test_ecrire_calibration_cree_les_dossiers(tmp_path):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    chemin = tmp_path / "calibrations" / "site-a.yaml"
    assert rapport.ecrire_calibration(resultats, lectures, chemin, lecture.CHAMPS_DEFAUT, 10) == 1
    assert "reference: VAL-ELASTIFLOW-001" in chemin.read_text(encoding="utf-8")


def test_cli_rapport_calibration(tmp_path, capsys):
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    sortie = tmp_path / "rapport.md"
    calibration = tmp_path / "calibrations" / "site-a.yaml"
    code = cli.main(
        [
            "rapport",
            "--resultats",
            str(resultats),
            "--lectures",
            str(lectures),
            "--sortie",
            str(sortie),
            "--calibration",
            str(calibration),
            "--ingest-lag-minutes",
            "5",
        ]
    )
    assert code == 0
    assert sortie.exists() and calibration.exists()
    assert (
        yaml.safe_load(calibration.read_text(encoding="utf-8"))["elastiflow"]["ingest_lag_minutes"]
        == 5
    )


def test_calibration_chargee_par_qos_audit(tmp_path):
    """Contrat de l'issue #15 : le fichier produit se charge sans erreur par qos_audit.

    Le test ne tourne que là où le paquet voisin est installé (les deux dépôts sont privés : la CI
    d'Elastiflow-Validation ne l'installe pas) ; ailleurs il est sauté, la structure étant vérifiée
    par ``test_construire_calibration_yaml_structure``.
    """
    qos_audit = pytest.importorskip("qos_audit.calibration")
    resultats = _resultats(tmp_path, [_test_reference("A3")])
    lectures = _lectures(tmp_path, [_lecture("A3")])
    chemin = tmp_path / "calibrations" / "site-a.yaml"
    rapport.ecrire_calibration(resultats, lectures, chemin, lecture.CHAMPS_DEFAUT, 10)
    cal = qos_audit.load_calibration(chemin)
    assert cal.reference == "VAL-ELASTIFLOW-001"
    assert cal.fields["exporter"] == "flow.export.host.name"
    assert cal.sampling_already_applied is True
    assert cal.exporters["sw-a"].validation is not None
    assert cal.exporters["sw-a"].validation.sampling_rates == (8,)
