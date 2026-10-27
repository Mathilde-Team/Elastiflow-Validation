"""Rapport de validation : comparaison ElastiFlow / référence et document VAL-ELASTIFLOW-001 (§10,
issue #6).

Pour chaque test de ``resultats.csv`` lu dans ``lectures.csv`` (jointure sur ``id`` et
``repetition``, une ligne par exportateur) :

- erreur (%) = (octets ElastiFlow − octets de référence) ÷ octets de référence × 100 ;
- n attendu = paquets réels ÷ sampling-rate du test ;
- borne à 95 % = 196 ÷ √n (protocole §10.3) ;
- conforme : |erreur| ≤ borne.

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

from loguru import logger

__all__ = [
    "Analyse",
    "analyser",
    "biais_moyen",
    "comparaison_sampling",
    "construire_document",
    "rapport",
]

BORNE = 196.0
SERIES_BIAIS = ("A2", "A3", "A5")
SUFFIXE_ANCIEN_SAMPLING = "-4096"


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


def analyser(resultats: Path, lectures: Path) -> tuple[list[Analyse], list[str]]:
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
        vus.add(cle)
        octets_reference = int(reference["octets"] or 0)
        paquets_reference = int(reference["paquets"] or 0)
        sampling = _sampling_effectif(reference, lecture)
        octets_elastiflow = int(lecture["octets"] or 0)
        n_attendu = paquets_reference / sampling if sampling and paquets_reference else 0.0
        borne = BORNE / n_attendu**0.5 if n_attendu else None
        erreur = (
            (octets_elastiflow - octets_reference) / octets_reference * 100.0
            if octets_reference
            else None
        )
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
                octets_elastiflow=octets_elastiflow,
                paquets_elastiflow=int(lecture["paquets"] or 0),
                enregistrements=int(lecture["enregistrements"] or 0),
                sampling=sampling,
                sampling_lu=str(lecture.get("sampling") or ""),
                n_attendu=n_attendu,
                erreur_pct=erreur,
                borne_pct=borne,
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
        f"| Borne à 95 % (%) | {_pct(a.borne_pct)} |",
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
        "Erreur observée | Borne à 95 % | Conforme |",
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
