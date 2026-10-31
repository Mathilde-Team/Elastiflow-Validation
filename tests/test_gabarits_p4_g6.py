# SPDX-License-Identifier: GPL-3.0-or-later
"""Gabarits P4/G6 : modèles vierges et plan de support sans action matérielle (#14)."""

import csv
from pathlib import Path

from test_injection import FauxSysteme

from elastiflow_validation import cli, injection, lecture, plan

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / "docs/releves-p4-g6"


def test_mapping_p4_couvre_les_champs_sans_resultat_invente():
    with (DOSSIER / "Mapping-P4.csv").open(encoding="utf-8", newline="") as fichier:
        lecteur = csv.DictReader(fichier)
        lignes = list(lecteur)
        assert len(lecteur.fieldnames) == len(set(lecteur.fieldnames)) == 12
    assert len(lignes) == 28
    assert all(None not in ligne and None not in ligne.values() for ligne in lignes)
    flux = {ligne["cle_logique"] for ligne in lignes if ligne["famille"] == "flux"}
    assert flux == set(lecture.CHAMPS_DEFAUT) | set(lecture.CHAMPS_OPTIONNELS)
    assert {ligne["famille"] for ligne in lignes} == {"flux", "compteurs"}
    assert all(ligne["statut"] == "A_RELEVER" for ligne in lignes)
    assert all(
        v == ""
        for ligne in lignes
        for c, v in ligne.items()
        if c not in {"famille", "cle_logique", "statut"}
    )
    assert len({(ligne["famille"], ligne["cle_logique"]) for ligne in lignes}) == len(lignes)


def test_csv_g6_ne_contient_que_les_entetes():
    with (DOSSIER / "Mesures-G6.csv").open(encoding="utf-8", newline="") as fichier:
        lecteur = csv.DictReader(fichier)
        assert list(lecteur) == []
        colonnes = lecteur.fieldnames
    assert len(colonnes) == len(set(colonnes)) == 26
    assert {
        "horodatage_mesure_utc",
        "horodatage_collecte_utc",
        "horodatage_ingestion_utc",
        "source",
        "sens",
        "ifindex",
        "compteur_octets",
        "compteur_paquets",
        "reset_ou_discontinuite",
        "largeur_compteur_bits",
        "base_octets",
        "preuve",
    } <= set(colonnes)


def test_plan_support_g6_une_seule_execution_a3():
    (test,) = plan.charger_plan(DOSSIER / "Plan-support-G6.yaml")
    (execution,) = plan.executions([test], 10e6)
    assert (test.id, test.protocole, test.repetitions, test.duree_s) == ("A3", "udp", 1, 1800)
    assert test.taille == 1000 and test.sampling is None
    assert execution.debit_bps == 1e6 and execution.charge_udp == 972
    assert execution.repetition == 1


def test_plan_support_g6_dry_run_sans_action_ni_resultat(tmp_path, monkeypatch, capsys):
    systeme = FauxSysteme()
    monkeypatch.setattr(systeme, "fermer", lambda: None, raising=False)
    monkeypatch.setattr(injection, "SystemeReel", lambda: systeme)
    sortie = tmp_path / "resultats.csv"
    assert (
        cli.main(
            [
                "injecter",
                "--plan",
                str(DOSSIER / "Plan-support-G6.yaml"),
                "--lien",
                "10M",
                "--test",
                "A3",
                "--client",
                "192.0.2.10",
                "--serveur",
                "192.0.2.20",
                "--sortie",
                str(sortie),
                "--dry-run",
            ]
        )
        == 0
    )
    texte = capsys.readouterr().out
    assert "1 lancement(s) simulé(s)" in texte
    assert "-t 1800" in texte and "-b 1M" in texte
    assert systeme.commandes == [] and systeme.lances == [] and systeme.confirmations == []
    assert systeme.pauses == [] and not sortie.exists()


def test_documentation_et_liens_des_gabarits():
    nom = "Releve-P4-G6-Elastiflow.md"
    fiche = (DOSSIER / nom).read_text(encoding="utf-8")
    assert f"docs/releves-p4-g6/{nom}" in (RACINE / "README.md").read_text(encoding="utf-8")
    for fichier in ("Mapping-P4.csv", "Mesures-G6.csv", "Plan-support-G6.yaml"):
        assert f"]({fichier})" in fiche and (DOSSIER / fichier).is_file()
    assert "--plan docs/releves-p4-g6/Plan-support-G6.yaml" in fiche
    assert "--dry-run" in fiche and "Aucune mesure réelle" in fiche
    assert "flow.sampling_interval" in fiche and "flow.sampling.interval" in fiche
