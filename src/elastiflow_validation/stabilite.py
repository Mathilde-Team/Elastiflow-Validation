# SPDX-License-Identifier: GPL-3.0-or-later
"""Constance du débit d'un test : série par minute utile et critère (§9.3, §10.1, #31).

Pour un test à débit constant (séries A et C2), la somme des octets de chaque minute utile est
comparée à la moyenne des minutes. Avec ``n`` échantillons dans la minute, l'écart dû au seul
échantillonnage reste, 95 fois sur 100, dans ``196/√n`` % (``196·√((1 + CV²)/n)`` sur des tailles
dispersées, §3.1).

Le critère refuse une série, avec au plus 5 % de refus à tort d'une série stable :

- s'il manque une minute (aucun échantillon : trou d'export, bucket perdu) ;
- si une minute sort de la borne élargie au nombre de minutes (pic ou creux franc) : borne
  multipliée par ``z / 1,96``, avec ``z`` tel qu'une série stable de ``m`` minutes n'y sorte
  qu'avec une probabilité de 1 % (correction de Bonferroni, ``z = Φ⁻¹(1 − 0,01 / 2m)``, au moins
  1,5 fois la borne) ;
- si les minutes hors borne sont plus nombreuses que ce que le hasard explique : sur ``m`` minutes
  indépendantes, chacune sort de sa borne avec une probabilité de 5 %, et l'on tolère le plus petit
  nombre ``k`` tel que ``P(X > k) ≤ 4 %`` (loi binomiale ; même raisonnement que la règle du
  verdict global proposée en #9, option A ; 4 % + 1 % de pics = 5 %).

Une dérive lente se voit par les minutes hors borne qu'elle accumule ; la règle « dépassements
tous du même signe » de #9 n'est pas reprise ici : avec deux minutes hors borne, elle refuserait
à tort une série stable une fois sur deux.

Le nombre d'échantillons d'une minute est le nombre d'enregistrements ElastiFlow (``doc_count``) :
un enregistrement par échantillon sFlow, à confirmer en phase 0 (P4).
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist

from loguru import logger

from elastiflow_validation.calcul import erreur_95_octets

__all__ = [
    "COLONNES_SERIE",
    "COLONNES_VERDICT",
    "FACTEUR_PIC",
    "Minute",
    "facteur_pic",
    "Verdict",
    "depassements_toleres",
    "ecrire_series",
    "ecrire_verdicts",
    "juger",
    "serie_minutes",
]

FACTEUR_PIC = 1.5  # facteur minimal de la borne des pics (#9, option A)
RISQUE = 0.04  # refus à tort par excès de minutes hors borne
RISQUE_PIC = 0.01  # refus à tort par un pic
P_MINUTE = 0.05  # probabilité qu'une minute stable sorte de sa borne à 95 %

COLONNES_SERIE = (
    "id",
    "repetition",
    "exportateur",
    "minute",
    "echantillons",
    "octets",
    "paquets",
    "ecart_pct",
    "borne_pct",
    "hors_borne",
)
COLONNES_VERDICT = (
    "id",
    "repetition",
    "exportateur",
    "minutes",
    "minutes_vides",
    "depassements",
    "toleres",
    "pics",
    "stable",
    "motif",
)


@dataclass(frozen=True)
class Minute:
    """Sommes d'une minute utile : ``debut`` en secondes epoch (multiple de 60)."""

    debut: int
    echantillons: int
    octets: float
    paquets: float


@dataclass(frozen=True)
class Verdict:
    """Résultat du critère de constance pour une série."""

    stable: bool
    minutes: int
    minutes_vides: int
    depassements: int
    toleres: int
    pics: int
    motif: str
    ecarts: tuple[float, ...]  # écart de chaque minute à la moyenne, en %
    bornes: tuple[float, ...]  # borne à 95 % de chaque minute, en %


def serie_minutes(buckets: Iterable[dict], utile_bas: int, utile_haut: int) -> list[Minute]:
    """Minutes de ``[utile_bas, utile_haut[`` depuis un ``date_histogram`` à 1 min.

    Les minutes absentes de l'histogramme (aucun enregistrement) sont rendues avec 0 échantillon :
    un trou dans la série doit se voir.
    """
    logger.debug("serie_minutes : entrée {} {}", utile_bas, utile_haut)
    par_minute = {b["key"] // 1000: b for b in buckets}
    serie: list[Minute] = []
    for debut in range(utile_bas, utile_haut, 60):
        b = par_minute.get(debut)
        if b is None:
            serie.append(Minute(debut, 0, 0.0, 0.0))
        else:
            serie.append(
                Minute(debut, int(b["doc_count"]), b["octets"]["value"], b["paquets"]["value"])
            )
    logger.debug("serie_minutes : sortie {} minute(s)", len(serie))
    return serie


def depassements_toleres(minutes: int, p: float = P_MINUTE, risque: float = RISQUE) -> int:
    """Plus petit ``k`` tel que ``P(X > k) ≤ risque`` pour ``X ~ B(minutes, p)``.

    Avec le risque par défaut (4 %) : 1 minute → 1, 10 → 2, 22 → 3, 28 → 4.
    """
    logger.debug("depassements_toleres : entrée {} {} {}", minutes, p, risque)
    if minutes < 0 or not 0 <= p <= 1 or not 0 < risque < 1:
        logger.debug("depassements_toleres : paramètres invalides")
        raise ValueError(
            f"minutes ≥ 0, 0 ≤ p ≤ 1 et 0 < risque < 1 attendus : {minutes} {p} {risque}"
        )
    k = 0
    cumul = (1 - p) ** minutes  # P(X ≤ 0)
    while k < minutes and 1 - cumul > risque + 1e-12:
        k += 1
        cumul += math.comb(minutes, k) * p**k * (1 - p) ** (minutes - k)
    logger.debug("depassements_toleres : sortie {}", k)
    return k


def facteur_pic(minutes: int, risque: float = RISQUE_PIC) -> float:
    """Facteur de la borne au-delà duquel une minute est un pic : ``max(1,5 ; z / 1,96)``.

    ``z = Φ⁻¹(1 − risque / 2m)`` : une série stable de ``m`` minutes n'a un pic qu'avec la
    probabilité ``risque`` (28 minutes → 1,82).
    """
    logger.debug("facteur_pic : entrée {} {}", minutes, risque)
    if minutes < 1 or not 0 < risque < 1:
        logger.debug("facteur_pic : paramètres invalides")
        raise ValueError(f"minutes ≥ 1 et 0 < risque < 1 attendus : {minutes} {risque}")
    z = NormalDist().inv_cdf(1 - risque / (2 * minutes))
    facteur = max(FACTEUR_PIC, z / 1.96)
    logger.debug("facteur_pic : sortie {}", facteur)
    return facteur


def juger(serie: Sequence[Minute], cv: float = 0.0) -> Verdict:
    """Critère de constance du débit (§10.1, « Débit constant ») sur une série par minute."""
    logger.debug("juger : entrée {} minute(s), CV {}", len(serie), cv)
    m = len(serie)
    vides = sum(1 for x in serie if x.echantillons == 0)
    moyenne = sum(x.octets for x in serie) / m if m else 0.0
    if not m or moyenne <= 0:
        logger.debug("juger : aucune minute utile ou aucun octet")
        return Verdict(False, m, vides, 0, 0, 0, "aucune minute utile avec des octets", (), ())
    ecarts = tuple((x.octets - moyenne) / moyenne * 100 for x in serie)
    bornes = tuple(erreur_95_octets(x.echantillons, cv) for x in serie)
    hors = sum(1 for e, b in zip(ecarts, bornes, strict=True) if abs(e) > b)
    facteur = facteur_pic(m)
    pics = sum(1 for e, b in zip(ecarts, bornes, strict=True) if abs(e) > facteur * b)
    toleres = depassements_toleres(m)
    if vides:
        motif = f"{vides} minute(s) sans échantillon"
    elif pics:
        motif = f"{pics} minute(s) au-delà de {facteur:.2f} fois la borne"
    elif hors > toleres:
        motif = f"{hors} minute(s) hors borne pour {toleres} tolérée(s)"
    else:
        motif = ""
    verdict = Verdict(not motif, m, vides, hors, toleres, pics, motif, ecarts, bornes)
    if motif:
        logger.info("débit non constant : {}", motif)
    logger.debug("juger : sortie stable={} {}", verdict.stable, motif)
    return verdict


def _pct(valeur: float) -> str:
    """Pourcentage textuel à 0,01 près ; vide pour une borne infinie (minute sans échantillon)."""
    logger.debug("_pct : entrée {}", valeur)
    texte = "" if math.isinf(valeur) else f"{valeur:.2f}"
    logger.debug("_pct : sortie {}", texte)
    return texte


def ecrire_series(
    chemin: Path, series: Sequence[tuple[str, str, str, Sequence[Minute]]], cv: float = 0.0
) -> None:
    """Écrit une ligne par minute utile : ``(id, répétition, exportateur, série)`` par test."""
    logger.debug("ecrire_series : entrée {} série(s) -> {}", len(series), chemin)
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f, lineterminator="\n")
        ecrivain.writerow(COLONNES_SERIE)
        for ident, repetition, exportateur, serie in series:
            v = juger(serie, cv)
            for i, x in enumerate(serie):
                ecart = _pct(v.ecarts[i]) if v.ecarts else ""
                borne = _pct(v.bornes[i]) if v.bornes else ""
                if not x.echantillons:
                    hors = "vide"
                else:
                    hors = "oui" if v.ecarts and abs(v.ecarts[i]) > v.bornes[i] else "non"
                ecrivain.writerow(
                    [ident, repetition, exportateur, x.debut, x.echantillons,
                     round(x.octets), round(x.paquets), ecart, borne, hors]
                )  # fmt: skip
    logger.debug("ecrire_series : sortie")


def ecrire_verdicts(
    chemin: Path, series: Sequence[tuple[str, str, str, Sequence[Minute]]], cv: float = 0.0
) -> int:
    """Écrit le verdict de constance de chaque série ; renvoie le nombre de séries non stables."""
    logger.debug("ecrire_verdicts : entrée {} série(s) -> {}", len(series), chemin)
    instables = 0
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f, lineterminator="\n")
        ecrivain.writerow(COLONNES_VERDICT)
        for ident, repetition, exportateur, serie in series:
            v = juger(serie, cv)
            instables += not v.stable
            ecrivain.writerow(
                [ident, repetition, exportateur, v.minutes, v.minutes_vides, v.depassements,
                 v.toleres, v.pics, "oui" if v.stable else "non", v.motif]
            )  # fmt: skip
    logger.debug("ecrire_verdicts : sortie {} instable(s)", instables)
    return instables
