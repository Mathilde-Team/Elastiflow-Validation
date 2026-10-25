"""Ligne de commande ``efv``.

Les sous-commandes (sampling, plan, injecter, lire, rapport) sont ajoutées
module par module ; voir les issues du dépôt.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from loguru import logger

from elastiflow_validation import __version__
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
    parseur.add_subparsers(dest="commande", metavar="COMMANDE")
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
