# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration de loguru pour la ligne de commande (SOCLE §8).

Niveau INFO par défaut sur la sortie d'erreur ; DEBUG avec ``--debug`` ou la
variable d'environnement ``SOCLE_DEBUG=1``.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

FORMAT = "<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> {message}"


def niveau(debug: bool = False) -> str:
    """Niveau de journal demandé par l'option ou l'environnement."""
    logger.debug("niveau : debug={}", debug)
    actif = debug or os.environ.get("SOCLE_DEBUG", "") not in ("", "0")
    resultat = "DEBUG" if actif else "INFO"
    logger.debug("niveau retenu : {}", resultat)
    return resultat


def configurer(debug: bool = False) -> str:
    """Remplace les sorties de loguru par une seule, sur stderr. Renvoie le niveau."""
    logger.debug("configurer : debug={}", debug)
    choisi = niveau(debug)
    logger.remove()
    logger.add(sys.stderr, level=choisi, format=FORMAT)
    logger.debug("journal configuré au niveau {}", choisi)
    return choisi
