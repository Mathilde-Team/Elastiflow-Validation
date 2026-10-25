"""Ligne de commande ``efv``.

Sous-commande disponible : ``sampling`` (#2). Les suivantes (plan, injecter,
lire, rapport) sont ajoutées module par module ; voir les issues du dépôt.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence

from loguru import logger

from elastiflow_validation import __version__, calcul
from elastiflow_validation.journal import configurer


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur principal et ses sous-commandes."""
    logger.debug("construction du parseur")
    parseur = argparse.ArgumentParser(
        prog="efv",
        description="Contre-expertise d'ElastiFlow par injection iperf2 et lecture sFlow.",
    )
    parseur.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parseur.add_argument("--debug", action="store_true", help="traces de debug (ou SOCLE_DEBUG=1)")
    sous = parseur.add_subparsers(dest="commande", metavar="COMMANDE")
    _ajouter_sampling(sous)
    logger.debug("parseur construit")
    return parseur


def main(argv: Sequence[str] | None = None) -> int:
    """Exécute la commande demandée et renvoie le code de sortie."""
    logger.debug("main : argv={}", argv)
    parseur = construire_parseur()
    arguments = parseur.parse_args(argv)
    configurer(arguments.debug)
    if arguments.commande is None:
        parseur.print_help()
        logger.debug("aucune commande : aide affichée")
        return 0
    code = arguments.action(arguments)
    logger.debug("commande {} terminée, code {}", arguments.commande, code)
    return code


# Débits du tableau 4.2 du protocole, en Mbit/s.
DEBITS_TABLEAU_MBPS = (1, 2, 4, 5, 10, 20, 50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000)
_SUFFIXES = {"K": 1e3, "M": 1e6, "G": 1e9}


def debit(texte: str) -> float:
    """Débit en bit/s depuis ``10M``, ``500k``, ``1G`` ou ``10e6``."""
    logger.debug("debit : {!r}", texte)
    brut = texte.strip().upper().removesuffix("BIT/S").removesuffix("BPS")
    facteur = _SUFFIXES.get(brut[-1:], 1.0)
    nombre = brut[:-1] if facteur != 1.0 else brut
    try:
        valeur = float(nombre.replace(",", ".")) * facteur
    except ValueError as exc:
        logger.debug("debit : illisible ({})", exc)
        raise argparse.ArgumentTypeError(
            f"débit illisible : {texte!r} (ex. 10M, 500k, 1G)"
        ) from exc
    if not math.isfinite(valeur) or valeur <= 0:
        logger.debug("debit : non positif")
        raise argparse.ArgumentTypeError(f"débit non positif : {texte!r}")
    logger.debug("debit : {} bit/s", valeur)
    return valeur


def _ajouter_sampling(sous: argparse._SubParsersAction) -> None:
    """Sous-commande ``efv sampling`` (§4 du protocole)."""
    logger.debug("ajout de la sous-commande sampling")
    p = sous.add_parser(
        "sampling",
        help="sampling-rate recommandé par débit de lien (§4)",
        description="Sampling-rate sFlow (puissance de 2) gardant l'erreur cible au plancher.",
    )
    p.add_argument(
        "debits",
        nargs="*",
        type=debit,
        metavar="DEBIT",
        help="débit effectif du lien (10M, 1G…) ; aucun : tableau 4.2",
    )
    p.add_argument("--plancher", type=float, default=5.0, help="plancher en %% du lien (défaut 5)")
    p.add_argument("--fenetre", type=float, default=300, help="fenêtre d'analyse en s (défaut 300)")
    p.add_argument(
        "--taille", type=int, default=1500, help="taille IP pour la précision (défaut 1500)"
    )
    p.add_argument("--erreur", type=float, default=5.0, help="erreur cible à 95 %% (défaut 5)")
    p.add_argument(
        "--taille-cout",
        type=int,
        default=100,
        help="taille IP pour le coût, lien saturé (défaut 100)",
    )
    p.set_defaults(action=commande_sampling)
    logger.debug("sous-commande sampling ajoutée")


def _format_ligne(ligne: calcul.Dimensionnement) -> str:
    """Une ligne du tableau, au format Markdown du protocole."""
    logger.debug("_format_ligne : {}", ligne)
    erreur = (
        "exhaustif" if ligne.erreur_pct is None else f"{ligne.erreur_pct:.1f} %".replace(".", ",")
    )
    texte = (
        f"| {ligne.debit_lien_bps / 1e6:g} | {ligne.plancher_bps / 1e3:g} | 1:{ligne.rate} "
        f"| {round(ligne.echantillons_plancher)} | {erreur} "
        f"| {round(ligne.echantillons_s_sature)} |"
    )
    logger.debug("_format_ligne : {}", texte)
    return texte


def commande_sampling(arguments: argparse.Namespace) -> int:
    """Affiche le dimensionnement de chaque lien demandé (ou du tableau 4.2)."""
    logger.debug("commande_sampling : {}", arguments)
    debits = arguments.debits or [m * 1e6 for m in DEBITS_TABLEAU_MBPS]
    try:
        lignes = [
            calcul.dimensionner(
                d,
                arguments.plancher / 100,
                arguments.fenetre,
                arguments.taille,
                arguments.erreur,
                arguments.taille_cout,
            )
            for d in debits
        ]
    except ValueError as exc:
        logger.error("sampling : {}", exc)
        return 2
    print(
        f"n cible : {calcul.n_cible(arguments.erreur)} échantillons "
        f"(erreur {arguments.erreur:g} % à 95 %)"
    )
    print(
        "| Débit du lien (Mbit/s) | Plancher (kbit/s) | Sampling-rate | Échantillons au plancher "
        "| Erreur 95 % | Échantillons/s lien saturé |"
    )
    print("|---|---|---|---|---|---|")
    for ligne in lignes:
        print(_format_ligne(ligne))
    logger.debug("commande_sampling : {} lignes", len(lignes))
    return 0
