"""Tests du rapport de validation : comparaison, bornes à 95 % et document VAL-ELASTIFLOW-001."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from elastiflow_validation import cli, rapport

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
    assert a.borne_pct == pytest.approx(196.0 / math.sqrt(1000.0))
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
