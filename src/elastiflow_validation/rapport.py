# SPDX-License-Identifier: GPL-3.0-or-later
"""Rapport de validation : comparaison ElastiFlow / référence et document VAL-ELASTIFLOW-001 (§10,
issue #6).

Pour chaque test de ``resultats.csv`` lu dans ``lectures.csv`` (jointure sur ``id`` et
``repetition``, une ligne par exportateur) :

- erreur (%) = (octets ElastiFlow − octets de référence) ÷ octets de référence × 100 ;
- n attendu = paquets réels ÷ sampling-rate du test ;
- borne globale à 95 % = z ÷ √n, avec z = 100·Φ⁻¹(1 − 0,05 ÷ 2m) pour les m mesures calculables
  (Bonferroni, protocole §10.3, #9 : ≈ 305 ÷ √n pour 22 mesures) ;
- conforme : |erreur| ≤ borne ; le verdict exige que toutes les mesures soient conformes.

Le biais moyen couvre les répétitions de A2, A3 et A5 (VAL §8) ; la comparaison avant/après
correction du sampling apparie chaque test ``X-4096`` à son homologue ``X``. Le document produit
remplit les sections 7 (fiches de test), 8 (synthèse chiffrée) et 10 (conclusion) du
VAL-ELASTIFLOW-001 ; les décisions qui restent humaines (verdict signé, QoS contractuelle) y
sont laissées vides.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import yaml
from loguru import logger

from elastiflow_validation.calcul import z_bonferroni

__all__ = [
    "Analyse",
    "analyser",
    "bornes_bonferroni",
    "biais_moyen",
    "comparaison_sampling",
    "construire_calibration_yaml",
    "construire_document",
    "ecrire_calibration",
    "rapport",
]

SERIES_BIAIS = ("A2", "A3", "A5")
SUFFIXE_ANCIEN_SAMPLING = "-4096"
# Clés de lecture (CHAMPS_DEFAUT) traduites vers les clés de ``elastiflow.FIELDS`` de qos-audit.
CHAMPS_VERS_QOS_AUDIT = {
    "exportateur": "exporter",
    "ip_source": "src_ip",
    "ip_destination": "dst_ip",
    "port_source": "src_port",
    "port_destination": "dst_port",
    "protocole": "proto",
    "octets": "bytes",
    "paquets": "packets",
    "sampling": "sampling",
    "dscp": "dscp",
}


@dataclass(frozen=True)
class Analyse:
    """Un test lu : la mesure ElastiFlow d'un exportateur confrontée à la référence nft."""

    id: str
    repetition: str
    exportateur: str
    protocole: str
    debit: str
    taille: str
    tos: str
    debut: int
    fin: int
    paquets_reference: int
    octets_reference: int
    octets_elastiflow: int
    paquets_elastiflow: int
    enregistrements: int
    sampling: int
    sampling_lu: str
    n_attendu: float
    erreur_pct: float | None
    borne_pct: float | None
    conforme: bool | None
    point_mesure: str = ""

    @property
    def cle(self) -> str:
        """Identifiant lisible : ``A2 (2)``."""
        return f"{self.id} ({self.repetition})"


def _sampling_effectif(resultat: dict[str, str], lecture: dict[str, str]) -> int:
    """Sampling du test : celui du plan (``resultats.csv``), sinon le premier lu dans ElastiFlow."""
    logger.debug("_sampling_effectif : entrée | {}", resultat.get("id"))
    brut = str(resultat.get("sampling") or "").strip()
    valeur = int(brut) if brut.isdigit() and int(brut) > 0 else 0
    if valeur <= 0:
        lus = [p for p in str(lecture.get("sampling") or "").split(";") if p.strip().isdigit()]
        valeur = int(lus[0]) if lus else 0
    logger.debug("_sampling_effectif : sortie | {}", valeur)
    return valeur


def points_mesure(choix: Sequence[str]) -> dict[str, str]:
    """Parse les choix répétés ``EXPORTATEUR=IFINDEX``, sans doublon ambigu."""
    logger.debug("points_mesure : entrée {} choix", len(choix))
    points: dict[str, str] = {}
    for texte in choix:
        nom, separateur, interface = texte.rpartition("=")
        if not separateur or not nom.strip() or not interface.isdigit() or nom in points:
            logger.debug("points_mesure : choix invalide")
            raise ValueError(
                "point de mesure attendu : EXPORTATEUR=IFINDEX, unique par exportateur"
            )
        points[nom] = interface
    logger.debug("points_mesure : sortie {} choix", len(points))
    return points


def _selectionner(lecture: dict[str, str], points: dict[str, str]) -> tuple[dict[str, str], str]:
    """Retient le volume du point demandé ; refuse un double comptage non résolu."""
    logger.debug("_selectionner : entrée")
    point = points.get(lecture["exportateur"], "")
    echantillonneurs = set(filter(None, lecture.get("echantillonneurs", "").split(";")))
    double = lecture.get("double_echantillonnage") == "oui" or len(echantillonneurs) > 1
    if double and not point:
        logger.debug("_selectionner : double comptage non résolu")
        raise ValueError(f"{lecture['exportateur']} : choisir --point-mesure EXPORTATEUR=IFINDEX")
    if point:
        volumes = json.loads(lecture.get("volumes_echantillonneurs") or "{}")
        if not isinstance(volumes, dict) or point not in volumes:
            logger.debug("_selectionner : volume du point absent")
            raise ValueError(f"{lecture['exportateur']} : point {point} absent ; relancer efv lire")
        volume = volumes[point]
        if not isinstance(volume, dict) or any(
            not isinstance(volume.get(c), int) or isinstance(volume.get(c), bool) or volume[c] < 0
            for c in ("octets", "paquets", "enregistrements")
        ):
            logger.debug("_selectionner : volume invalide")
            raise ValueError("volumes_echantillonneurs : entiers positifs ou nuls attendus")
        lecture = lecture | {c: str(v) for c, v in volume.items()}
    logger.debug("_selectionner : sortie point {}", point or "total")
    return lecture, point


def analyser(
    resultats: Path, lectures: Path, points: dict[str, str] | None = None
) -> tuple[list[Analyse], list[str]]:
    """Joint ``resultats.csv`` et ``lectures.csv`` et calcule chaque mesure.

    Returns:
        Les analyses (une par test et exportateur lu) et les identifiants des tests sans
        lecture ElastiFlow (fenêtre pas encore close ou rien d'indexé).
    """
    logger.debug("analyser : entrée | {} + {}", resultats, lectures)
    with resultats.open(encoding="utf-8", newline="") as f:
        lignes_reference = {(t["id"], t["repetition"]): t for t in csv.DictReader(f)}
    with lectures.open(encoding="utf-8", newline="") as f:
        lignes_lues = list(csv.DictReader(f))
    analyses: list[Analyse] = []
    vus: set[tuple[str, str]] = set()
    for lecture in lignes_lues:
        cle = (lecture["id"], lecture["repetition"])
        reference = lignes_reference.get(cle)
        if reference is None:
            logger.warning("lecture sans résultat injecté : {}#{}", cle[0], cle[1])
            continue
        lecture, point = _selectionner(lecture, points or {})
        vus.add(cle)
        octets_reference = int(reference["octets"] or 0)
        paquets_reference = int(reference["paquets"] or 0)
        sampling = _sampling_effectif(reference, lecture)
        octets_elastiflow = int(lecture["octets"] or 0)
        n_attendu = paquets_reference / sampling if sampling and paquets_reference else 0.0
        erreur = (
            (octets_elastiflow - octets_reference) / octets_reference * 100.0
            if octets_reference
            else None
        )
        analyses.append(
            Analyse(
                id=reference["id"],
                repetition=reference["repetition"],
                exportateur=lecture["exportateur"],
                protocole=reference.get("protocole", ""),
                debit=reference.get("debit", ""),
                taille=reference.get("taille", ""),
                tos=reference.get("tos", ""),
                debut=int(reference["debut"] or 0),
                fin=int(reference["fin"] or 0),
                paquets_reference=paquets_reference,
                octets_reference=octets_reference,
                octets_elastiflow=octets_elastiflow,
                paquets_elastiflow=int(lecture["paquets"] or 0),
                enregistrements=int(lecture["enregistrements"] or 0),
                sampling=sampling,
                sampling_lu=str(lecture.get("sampling") or ""),
                n_attendu=n_attendu,
                erreur_pct=erreur,
                borne_pct=None,
                conforme=None,
                point_mesure=point,
            )
        )
    analyses = bornes_bonferroni(analyses)
    non_lus = [f"{i} ({r})" for (i, r) in lignes_reference if (i, r) not in vus]
    logger.debug("analyser : sortie | {} analyse(s), {} non lu(s)", len(analyses), len(non_lus))
    return analyses, non_lus


def bornes_bonferroni(analyses: Sequence[Analyse]) -> list[Analyse]:
    """Borne et conformité de chaque mesure, corrigées de Bonferroni pour l'ensemble (#9).

    Les m mesures calculables (erreur et n connus) sont jugées ensemble : chacune reçoit la borne
    ``z_bonferroni(m) ÷ √n``. Une mesure non calculable garde ``borne_pct`` et ``conforme`` à
    ``None`` et n'entre pas dans m.
    """
    logger.debug("bornes_bonferroni : entrée | {} analyse(s)", len(analyses))
    calculables = [a for a in analyses if a.erreur_pct is not None and a.n_attendu > 0]
    if not calculables:
        logger.debug("bornes_bonferroni : sortie | aucune mesure calculable")
        return list(analyses)
    z = z_bonferroni(len(calculables))
    resultat: list[Analyse] = []
    for a in analyses:
        if a.erreur_pct is None or a.n_attendu <= 0:
            resultat.append(a)
            continue
        borne = z / a.n_attendu**0.5
        resultat.append(replace(a, borne_pct=borne, conforme=abs(a.erreur_pct) <= borne))
    logger.debug("bornes_bonferroni : sortie | m={} z={:.1f}", len(calculables), z)
    return resultat


def biais_moyen(analyses: Sequence[Analyse]) -> float | None:
    """Moyenne des erreurs des répétitions de A2, A3 et A5 (VAL §8), ou ``None`` si aucune."""
    logger.debug("biais_moyen : entrée | {} analyse(s)", len(analyses))
    erreurs = [a.erreur_pct for a in analyses if a.id in SERIES_BIAIS and a.erreur_pct is not None]
    biais = sum(erreurs) / len(erreurs) if erreurs else None
    logger.debug("biais_moyen : sortie | {}", biais)
    return biais


def comparaison_sampling(analyses: Sequence[Analyse]) -> list[tuple[Analyse, Analyse]]:
    """Apparie chaque test ``X-4096`` (ancien sampling) à son ``X`` au sampling recommandé."""
    logger.debug("comparaison_sampling : entrée | {} analyse(s)", len(analyses))
    paires: list[tuple[Analyse, Analyse]] = []
    par_cle: dict[tuple[str, str], list[Analyse]] = {}
    for a in analyses:
        par_cle.setdefault((a.id, a.repetition), []).append(a)
    for (ident, repetition), groupe in sorted(par_cle.items()):
        if not ident.endswith(SUFFIXE_ANCIEN_SAMPLING):
            continue
        base = ident[: -len(SUFFIXE_ANCIEN_SAMPLING)]
        recommandes = par_cle.get((base, repetition), [])
        for ancien in groupe:
            for recommande in recommandes:
                paires.append((ancien, recommande))
    logger.debug("comparaison_sampling : sortie | {} paire(s)", len(paires))
    return paires


def _pct(valeur: float | None) -> str:
    """Formate un pourcentage à la française : ``-1,23``."""
    logger.debug("_pct : entrée | {}", valeur)
    texte = "" if valeur is None else f"{valeur:.2f}".replace(".", ",")
    logger.debug("_pct : sortie | {}", texte)
    return texte


def _fiche(a: Analyse) -> list[str]:
    """Fiche de résultat d'un test (protocole §10.2)."""
    logger.debug("_fiche : entrée | {} @ {}", a.cle, a.exportateur)
    conforme = "non calculable" if a.conforme is None else ("oui" if a.conforme else "non")
    lignes = [
        f"### {a.cle} — {a.exportateur}",
        "",
        "| Champ | Valeur |",
        "| --- | --- |",
        f"| ID du test | {a.id} (répétition {a.repetition}) |",
        f"| Protocole, débit, taille IP, TOS | {a.protocole}, {a.debit}, {a.taille} o, {a.tos} |",
        f"| Début / fin (epoch) | {a.debut} / {a.fin} |",
        f"| Référence : paquets / octets IP (nft) | {a.paquets_reference} / {a.octets_reference} |",
        f"| ElastiFlow : exportateur, sampling-rate lu | {a.exportateur}, {a.sampling_lu} |",
        f"| Point de mesure (ifindex échantillonneur) | {a.point_mesure or 'total exportateur'} |",
        f"| ElastiFlow : enregistrements, paquets, octets | {a.enregistrements}, "
        f"{a.paquets_elastiflow}, {a.octets_elastiflow} |",
        f"| Échantillons attendus n | {a.n_attendu:.0f} |",
        f"| Erreur observée (%) | {_pct(a.erreur_pct)} |",
        f"| Borne globale à 95 % (%) | {_pct(a.borne_pct)} |",
        f"| Conforme | {conforme} |",
        "",
    ]
    logger.debug("_fiche : sortie | {} ligne(s)", len(lignes))
    return lignes


def _synthese(analyses: Sequence[Analyse]) -> list[str]:
    """Tableau de synthèse des erreurs (VAL §8), une ligne par test et exportateur."""
    logger.debug("_synthese : entrée | {} analyse(s)", len(analyses))
    lignes = [
        "| ID | Exportateur | Octets de référence | Octets ElastiFlow | Échantillons n | "
        "Erreur observée | Borne globale à 95 % | Conforme |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for a in sorted(analyses, key=lambda x: (x.id, x.repetition, x.exportateur)):
        lignes.append(
            f"| {a.cle} | {a.exportateur} | {a.octets_reference} | {a.octets_elastiflow} | "
            f"{a.n_attendu:.0f} | {_pct(a.erreur_pct)} | {_pct(a.borne_pct)} | "
            f"{'oui' if a.conforme else 'non' if a.conforme is False else '—'} |"
        )
    logger.debug("_synthese : sortie | {} ligne(s)", len(lignes))
    return lignes


def _conclusion(analyses: Sequence[Analyse]) -> list[str]:
    """Synthèse par domaine : tests conformes / total (VAL §10)."""
    logger.debug("_conclusion : entrée | {} analyse(s)", len(analyses))
    domaines = [
        ("Phase 0 (calibrage)", ("P",)),
        ("Série A : débit constant", ("A",)),
        ("Série B : taille des paquets", ("B",)),
        ("Série C : TCP", ("C",)),
        ("Séries D et E : répétition et saturation", ("D", "E")),
    ]
    lignes = ["| Domaine | Tests conformes / total | Commentaire |", "| --- | --- | --- |"]
    for nom, series in domaines:
        groupe = [a for a in analyses if a.id[:1] in series and a.conforme is not None]
        passes = sum(1 for a in groupe if a.conforme)
        lignes.append(f"| {nom} | {passes} / {len(groupe)} | |")
    lignes.append("")
    jugees = [a for a in analyses if a.conforme is not None]
    if jugees:
        conformes = sum(1 for a in jugees if a.conforme)
        z = f"{z_bonferroni(len(jugees)) / 100:.2f}".replace(".", ",")
        lignes.append(
            f"**Critère global** (protocole §10.3, Bonferroni sur m = {len(jugees)} mesures, "
            f"z = {z}) : "
            f"{conformes} / {len(jugees)} mesures dans leur borne ; "
            + (
                "toutes conformes, critère rempli."
                if conformes == len(jugees)
                else "critère non rempli."
            )
        )
        lignes.append("")
    lignes.append(
        "**Périmètre de validité** (protocole §10.4) : le verdict ne vaut que pour les "
        "exportateurs, interfaces, sampling-rates et versions ElastiFlow listés dans les "
        "fiches ci-dessus ; toute évolution listée dans le tableau de revalidation du "
        "protocole impose une revalidation allégée ou complète."
    )
    logger.debug("_conclusion : sortie | {} ligne(s)", len(lignes))
    return lignes


def construire_document(analyses: Sequence[Analyse], non_lus: Sequence[str]) -> str:
    """Sections 7, 8 et 10 du VAL-ELASTIFLOW-001 remplies avec les mesures."""
    logger.debug("construire_document : entrée | {} analyse(s)", len(analyses))
    lignes: list[str] = [
        "# Rapport de validation ElastiFlow — sections 7, 8 et 10 de VAL-ELASTIFLOW-001",
        "",
        f"Généré par `efv rapport` le {datetime.now(tz=UTC).strftime('%d/%m/%Y %H:%M UTC')}.",
        "",
    ]
    lignes += ["## 7. Fiches de test", ""]
    for a in sorted(analyses, key=lambda x: (x.id, x.repetition, x.exportateur)):
        lignes += _fiche(a)
    lignes += ["## 8. Synthèse chiffrée des erreurs", ""]
    lignes += _synthese(analyses)
    lignes.append("")
    biais = biais_moyen(analyses)
    lignes.append(f"**Biais moyen** (moyenne des erreurs de A2, A3 et A5) : {_pct(biais)} %.")
    lignes.append("")
    paires = comparaison_sampling(analyses)
    if paires:
        lignes += [
            "**Comparaison avant et après correction du sampling** :",
            "",
            "| Test | Erreur en 1:4096 | Erreur au sampling recommandé |",
            "| --- | --- | --- |",
        ]
        for ancien, recommande in paires:
            lignes.append(
                f"| {ancien.cle} | {_pct(ancien.erreur_pct)} | {_pct(recommande.erreur_pct)} |"
            )
        lignes.append("")
    if non_lus:
        lignes.append(
            f"**Tests sans lecture ElastiFlow** (fenêtre non close ou rien d'indexé) : "
            f"{', '.join(non_lus)}."
        )
        lignes.append("")
    lignes += ["## 10. Conclusion et décision", ""]
    lignes += ["**Synthèse des résultats**", ""]
    lignes += _conclusion(analyses)
    lignes.append("")
    lignes.append("**Verdict** : à compléter et signer (VAL-ELASTIFLOW-001, section 10).")
    texte = "\n".join(lignes)
    logger.debug("construire_document : sortie | {} caractère(s)", len(texte))
    return texte


def rapport(
    resultats: Path, lectures: Path, sortie: Path, points: dict[str, str] | None = None
) -> int:
    """Analyse les mesures et écrit le document de validation.

    Returns:
        Nombre d'analyses produites.
    """
    logger.debug("rapport : entrée | {} + {} -> {}", resultats, lectures, sortie)
    analyses, non_lus = analyser(resultats, lectures, points)
    texte = construire_document(analyses, non_lus)
    sortie.write_text(texte, encoding="utf-8")
    logger.info("{} analyse(s), {} test(s) sans lecture -> {}", len(analyses), len(non_lus), sortie)
    logger.debug("rapport : sortie | {} analyse(s)", len(analyses))
    return len(analyses)


def _sampling_deja_applique(analyses: Sequence[Analyse]) -> bool | None:
    """P6 inféré : octets ≈ référence (appliqué) ou ≈ référence ÷ sampling (non appliqué)."""
    logger.debug("_sampling_deja_applique : entrée | {} analyse(s)", len(analyses))
    mesures = [
        a
        for a in analyses
        if a.id[:1] == "A" and a.octets_reference and a.sampling and a.erreur_pct is not None
    ]
    inference: bool | None = None
    if mesures:
        directs = [a.octets_elastiflow / a.octets_reference for a in mesures]
        corriges = [a.octets_elastiflow * a.sampling / a.octets_reference for a in mesures]
        if all(0.98 <= r <= 1.02 for r in directs):
            inference = True
        elif all(0.98 <= r <= 1.02 for r in corriges):
            inference = False
    logger.debug("_sampling_deja_applique : sortie | {}", inference)
    return inference


def _octets_par_paquet(analyses: Sequence[Analyse]) -> int | None:
    """P5 inféré : marge moyenne (octets ElastiFlow − référence) ÷ paquets, sur les tests B et C."""
    logger.debug("_octets_par_paquet : entrée | {} analyse(s)", len(analyses))
    marges = [
        (a.octets_elastiflow - a.octets_reference) / a.paquets_reference
        for a in analyses
        if a.id[:1] in ("B", "C") and a.paquets_reference and a.octets_reference
    ]
    marge = max(0, round(sum(marges) / len(marges))) if marges else None
    logger.debug("_octets_par_paquet : sortie | {}", marge)
    return marge


def construire_calibration_yaml(
    analyses: Sequence[Analyse], champs: dict[str, str], ingest_lag_minutes: int
) -> str:
    """Brouillon de ``calibrations/<site>.yaml`` au format lu par ``qos_audit.calibration``.

    P4 (champs), P6 (facteur appliqué), P5 (octets par paquet) et P2 (exportateurs et samplings
    observés) sont remplis depuis les mesures ; ``model``, ``comware_version``, ``interfaces``
    (display sflow), la version ElastiFlow et ``validated_on`` restent à compléter à la main.
    """
    logger.debug(
        "construire_calibration_yaml : entrée | {} analyse(s), délai {} min",
        len(analyses),
        ingest_lag_minutes,
    )
    par_exportateur: dict[str, set[int]] = {}
    for a in analyses:
        par_exportateur.setdefault(a.exportateur, set()).add(a.sampling)
    document = {
        "reference": "VAL-ELASTIFLOW-001",
        "validated_on": None,
        "elastiflow": {
            "version": "à renseigner",
            "fields": {
                CHAMPS_VERS_QOS_AUDIT[c]: v for c, v in champs.items() if c in CHAMPS_VERS_QOS_AUDIT
            },
            "counters_index": "elastiflow-telemetry_flow-*",
            "counter_fields": {
                "ifindex": "netif.index",
                "bytes_in": "netif.bytes.in",
                "bytes_out": "netif.bytes.out",
            },
            "sampling_already_applied": _sampling_deja_applique(analyses),
            "bytes_overhead_per_packet": _octets_par_paquet(analyses) or 0,
            "ingest_lag_minutes": ingest_lag_minutes,
        },
        "exporters": {
            nom: {
                "model": "à renseigner",
                "comware_version": "à renseigner",
                "sampling_direction": "inbound",
                "counter_interval_s": 30,
                "interfaces": [],
                "validation": {
                    "sampling_direction": "inbound",
                    "sampling_rates": sorted(taux),
                },
            }
            for nom, taux in sorted(par_exportateur.items())
        },
    }
    texte = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    texte = (
        "# Calibration issue de la validation d'ElastiFlow (VAL-ELASTIFLOW-001), générée par "
        "`efv rapport`.\n"
        "# Brouillon : P5/P6 sont inférés des mesures, counters_* sont les défauts "
        "à vérifier sur la télémétrie.\n"
        "# À compléter avant exploitation par qos-audit : model, comware_version, interfaces "
        "(display sflow),\n"
        "# version ElastiFlow, validated_on (date de signature du verdict).\n"
    ) + texte
    logger.debug("construire_calibration_yaml : sortie | {} caractère(s)", len(texte))
    return texte


def ecrire_calibration(
    resultats: Path,
    lectures: Path,
    chemin: Path,
    champs: dict[str, str],
    ingest_lag_minutes: int,
    points: dict[str, str] | None = None,
) -> int:
    """Écrit le brouillon de calibration ; ``calibrations/<site>.yaml`` (issue #15).

    Returns:
        Nombre d'analyses ayant servi à la calibration.
    """
    logger.debug("ecrire_calibration : entrée | {} -> {}", lectures, chemin)
    analyses, _ = analyser(resultats, lectures, points)
    if chemin.parent != Path(""):
        chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        construire_calibration_yaml(analyses, champs, ingest_lag_minutes), encoding="utf-8"
    )
    logger.info("calibration -> {}", chemin)
    logger.debug("ecrire_calibration : sortie | {} analyse(s)", len(analyses))
    return len(analyses)
