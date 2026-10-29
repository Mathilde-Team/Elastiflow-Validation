# SPDX-License-Identifier: GPL-3.0-or-later
"""Rapport de validation : comparaison ElastiFlow / référence et document VAL-ELASTIFLOW-001 (§10,
issue #6).

Pour chaque test de ``resultats.csv`` lu dans ``lectures.csv`` (jointure sur ``id`` et
``repetition``, une ligne par exportateur) :

- erreur (%) = (octets ElastiFlow − octets de référence) ÷ octets de référence × 100 ;
- n attendu = paquets réels ÷ sampling-rate du test ;
- borne Bonferroni = z(m)·100 ÷ √n, commune aux m mesures évaluées (issue #9) ;
- conforme : |erreur| ≤ borne (la borne à 95 % seule, 196 ÷ √n, est une
  information secondaire).

Le verdict global (issue #9, décision d'équipe du 28/10/2026 : option B) applique la
correction de Bonferroni : borne z(m)·100/√n commune aux m mesures évaluées —
z(22) = 3,05 — le critère de conformité de chaque mesure et le verdict global
(toutes les mesures doivent y rester). La borne à 95 % par mesure (196/√n) reste
une information secondaire de diagnostic ; un jeu incomplet (test sans lecture,
mesure non calculable) ne peut pas être validé : verdict non calculable.

Le biais moyen couvre les répétitions de A2, A3 et A5 (VAL §8) ; la comparaison avant/après
correction du sampling apparie chaque test ``X-4096`` à son homologue ``X``. Le document produit
remplit les sections 7 (fiches de test), 8 (synthèse chiffrée) et 10 (conclusion) du
VAL-ELASTIFLOW-001 ; les décisions qui restent humaines (verdict signé, QoS contractuelle) y
sont laissées vides.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist

import yaml
from loguru import logger

__all__ = [
    "Analyse",
    "VerdictGlobal",
    "analyser",
    "biais_moyen",
    "comparaison_sampling",
    "construire_calibration_yaml",
    "construire_document",
    "ecrire_calibration",
    "rapport",
    "verdict_global",
]

BORNE = 196.0
# Risque global d'échec à tort du verdict (protocole §10.3, décision du 28/10/2026,
# issue #9) : correction de Bonferroni, borne z(m)·100/√n au lieu de 196/√n.
RISQUE_GLOBAL = 0.05
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
    borne95_pct: float | None
    conforme: bool | None

    @property
    def cle(self) -> str:
        """Identifiant lisible : ``A2 (2)``."""
        return f"{self.id} ({self.repetition})"


@dataclass(frozen=True)
class VerdictGlobal:
    """Verdict global : toutes les mesures dans la borne Bonferroni (issue #9).

    Décision d'équipe du 28/10/2026 (MathildeDec) : option B. La conformité de chaque
    mesure et le verdict global suivent la borne Bonferroni z(m)·100/√n —
    z(m) = Φ⁻¹(1 − 0,05/2m), soit 3,05 pour les 22 mesures de la campagne — et
    exigent que TOUTES les mesures y restent : par l'inégalité de Boole,
    P(au moins un dépassement par hasard) ≤ m·(0,05/m) = 5 %, sans hypothèse
    d'indépendance. La borne à 95 % seule (196/√n, ``borne95_pct``) reste une
    information secondaire de diagnostic. Un jeu incomplet (mesure sans lecture
    ou non calculable) ne peut pas être validé : le verdict est alors ``None``
    avec son motif.
    """

    nb_mesures: int
    nb_hors_borne: int
    z_bonferroni: float
    verdict: bool | None
    non_calculable: str | None


def _z_bonferroni(nb_mesures: int) -> float:
    """z de la borne Bonferroni : Φ⁻¹(1 − 0,05/2m) ; 1,96 pour m = 1, 3,05 pour m = 22."""
    logger.debug("_z_bonferroni : entrée | {} mesure(s)", nb_mesures)
    if nb_mesures <= 0:
        z = 1.96
        logger.debug("_z_bonferroni : sortie | z = {}", z)
        return z
    z = NormalDist().inv_cdf(1 - RISQUE_GLOBAL / (2 * nb_mesures))
    logger.debug("_z_bonferroni : sortie | z = {:.4f}", z)
    return z


def verdict_global(
    analyses: Sequence[Analyse], non_lus: Sequence[str] = ()
) -> VerdictGlobal:
    """Toutes les mesures dans la borne Bonferroni (protocole §10.3, issue #9).

    Un jeu incomplet ne peut pas être validé : un test sans lecture ElastiFlow ou une
    mesure non calculable rend le verdict ``None`` (motif dans ``non_calculable``) —
    un sous-ensemble prévu et un jeu incomplet ne sont pas équivalents.
    """
    logger.debug(
        "verdict_global : entrée | {} analyse(s), {} non lu(s)", len(analyses), len(non_lus)
    )
    if not analyses:
        logger.debug("verdict_global : sortie | aucune mesure")
        return VerdictGlobal(0, 0, 1.96, None, "aucune mesure")
    if non_lus:
        logger.debug("verdict_global : sortie | jeu incomplet (non lus)")
        return VerdictGlobal(
            len(analyses), 0, 1.96, None, f"{len(non_lus)} test(s) sans lecture ElastiFlow"
        )
    non_calculables = [a for a in analyses if a.erreur_pct is None or a.borne_pct is None]
    if non_calculables:
        logger.debug("verdict_global : sortie | mesure(s) non calculable(s)")
        return VerdictGlobal(
            len(analyses), 0, 1.96, None, f"{len(non_calculables)} mesure(s) non calculable(s)"
        )
    z = _z_bonferroni(len(analyses))
    hors_borne = [a for a in analyses if a.conforme is False]
    verdict = not hors_borne
    resultat = VerdictGlobal(len(analyses), len(hors_borne), z, verdict, None)
    logger.debug("verdict_global : sortie | {}", resultat.verdict)
    return resultat


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


def analyser(resultats: Path, lectures: Path) -> tuple[list[Analyse], list[str]]:
    """Joint ``resultats.csv`` et ``lectures.csv`` et calcule chaque mesure.

    La conformité suit la règle du verdict global (issue #9, décision du 28/10/2026) :
    borne Bonferroni z(m)·100/√n commune à la famille des m mesures évaluées —
    z(m) = Φ⁻¹(1 − 0,05/2m), soit 3,05 pour 22 mesures. La borne à 95 % par mesure
    (196/√n) reste une information secondaire de diagnostic (``borne95_pct``).

    Returns:
        Les analyses (une par test et exportateur lu) et les identifiants des tests sans
        lecture ElastiFlow (fenêtre pas encore close ou rien d'indexé).
    """
    logger.debug("analyser : entrée | {} + {}", resultats, lectures)
    with resultats.open(encoding="utf-8", newline="") as f:
        lignes_reference = {(t["id"], t["repetition"]): t for t in csv.DictReader(f)}
    with lectures.open(encoding="utf-8", newline="") as f:
        lignes_lues = list(csv.DictReader(f))
    brutes: list[tuple[dict[str, str], dict[str, str], float, float | None]] = []
    vus: set[tuple[str, str]] = set()
    for lecture in lignes_lues:
        cle = (lecture["id"], lecture["repetition"])
        reference = lignes_reference.get(cle)
        if reference is None:
            logger.warning("lecture sans résultat injecté : {}#{}", cle[0], cle[1])
            continue
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
        brutes.append((reference, lecture, n_attendu, erreur))
    # Borne Bonferroni commune à la famille des m mesures évaluées (issue #9).
    z = _z_bonferroni(len(brutes))
    analyses: list[Analyse] = []
    for reference, lecture, n_attendu, erreur in brutes:
        octets_reference = int(reference["octets"] or 0)
        paquets_reference = int(reference["paquets"] or 0)
        borne = z * 100.0 / n_attendu**0.5 if n_attendu else None
        borne95 = BORNE / n_attendu**0.5 if n_attendu else None
        conforme: bool | None
        if erreur is None or borne is None:
            conforme = None
        else:
            conforme = abs(erreur) <= borne
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
                octets_elastiflow=int(lecture["octets"] or 0),
                paquets_elastiflow=int(lecture["paquets"] or 0),
                enregistrements=int(lecture["enregistrements"] or 0),
                sampling=_sampling_effectif(reference, lecture),
                sampling_lu=str(lecture.get("sampling") or ""),
                n_attendu=n_attendu,
                erreur_pct=erreur,
                borne_pct=borne,
                borne95_pct=borne95,
                conforme=conforme,
            )
        )
    non_lus = [f"{i} ({r})" for (i, r) in lignes_reference if (i, r) not in vus]
    logger.debug("analyser : sortie | {} analyse(s), {} non lu(s)", len(analyses), len(non_lus))
    return analyses, non_lus


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
        f"| ElastiFlow : enregistrements, paquets, octets | {a.enregistrements}, "
        f"{a.paquets_elastiflow}, {a.octets_elastiflow} |",
        f"| Échantillons attendus n | {a.n_attendu:.0f} |",
        f"| Erreur observée (%) | {_pct(a.erreur_pct)} |",
        f"| Borne Bonferroni z·100/√n (%) | {_pct(a.borne_pct)} |",
        f"| Borne à 95 % seule (diagnostic) | {_pct(a.borne95_pct)} |",
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
        "Erreur observée | Borne Bonferroni | Conforme |",
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
    lignes.append(
        "**Périmètre de validité** (protocole §10.4) : le verdict ne vaut que pour les "
        "exportateurs, interfaces, sampling-rates et versions ElastiFlow listés dans les "
        "fiches ci-dessus ; toute évolution listée dans le tableau de revalidation du "
        "protocole impose une revalidation allégée ou complète."
    )
    logger.debug("_conclusion : sortie | {} ligne(s)", len(lignes))
    return lignes


def _verdict(analyses: Sequence[Analyse], non_lus: Sequence[str]) -> list[str]:
    """Bloc du verdict global : toutes les mesures dans la borne Bonferroni (§10.3, #9)."""
    logger.debug("_verdict : entrée | {} analyse(s)", len(analyses))
    v = verdict_global(analyses, non_lus)
    if v.verdict is None:
        lignes = [
            "**Verdict global** (protocole §10.3) : non calculable — "
            f"{v.non_calculable} ; un jeu incomplet ne peut pas être validé.",
            "",
        ]
        logger.debug("_verdict : sortie | {} ligne(s)", len(lignes))
        return lignes
    lignes = [
        "**Verdict global** (protocole §10.3, issue #9) :",
        "",
        "| Mesures | Hors borne | z Bonferroni | Verdict |",
        "| --- | --- | --- | --- |",
        f"| {v.nb_mesures} | {v.nb_hors_borne} | {v.z_bonferroni:.2f} | "
        f"{'Validé' if v.verdict else 'Refusé'} |",
        "",
        f"Toutes les mesures doivent rester dans la borne Bonferroni z·100/√n "
        f"(z = {v.z_bonferroni:.2f} pour {v.nb_mesures} mesures) : "
        "P(au moins un dépassement par hasard) ≤ 5 %. La borne à 95 % par mesure "
        "reste le diagnostic des fiches ci-dessus.",
        "",
    ]
    logger.debug("_verdict : sortie | {} ligne(s)", len(lignes))
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
    lignes += _verdict(analyses, non_lus)
    lignes.append("**Verdict** : à compléter et signer (VAL-ELASTIFLOW-001, section 10).")
    texte = "\n".join(lignes)
    logger.debug("construire_document : sortie | {} caractère(s)", len(texte))
    return texte


def rapport(resultats: Path, lectures: Path, sortie: Path) -> int:
    """Analyse les mesures et écrit le document de validation.

    Returns:
        Nombre d'analyses produites.
    """
    logger.debug("rapport : entrée | {} + {} -> {}", resultats, lectures, sortie)
    analyses, non_lus = analyser(resultats, lectures)
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
    resultats: Path, lectures: Path, chemin: Path, champs: dict[str, str], ingest_lag_minutes: int
) -> int:
    """Écrit le brouillon de calibration ; ``calibrations/<site>.yaml`` (issue #15).

    Returns:
        Nombre d'analyses ayant servi à la calibration.
    """
    logger.debug("ecrire_calibration : entrée | {} -> {}", lectures, chemin)
    analyses, _ = analyser(resultats, lectures)
    if chemin.parent != Path(""):
        chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        construire_calibration_yaml(analyses, champs, ingest_lag_minutes), encoding="utf-8"
    )
    logger.info("calibration -> {}", chemin)
    logger.debug("ecrire_calibration : sortie | {} analyse(s)", len(analyses))
    return len(analyses)
