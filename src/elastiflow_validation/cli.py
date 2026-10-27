"""Ligne de commande ``efv``.

Sous-commandes disponibles : ``sampling`` (#2), ``plan`` (#3), ``injecter`` (#4), ``lire`` (#5).
La suivante (rapport) est ajoutée module par module ; voir les issues du dépôt.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import math
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from elastiflow_validation import __version__, calcul, injection, lecture, plan
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
    _ajouter_plan(sous)
    _ajouter_injecter(sous)
    _ajouter_lire(sous)
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
    p.add_argument(
        "--cv",
        type=float,
        default=0.0,
        help="CV de la taille des paquets : erreur sur les octets du trafic réel (défaut 0)",
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
                arguments.cv,
            )
            for d in debits
        ]
    except ValueError as exc:
        logger.error("sampling : {}", exc)
        return 2
    print(
        f"n cible : {calcul.n_cible_octets(arguments.erreur, arguments.cv)} échantillons "
        f"(erreur {arguments.erreur:g} % à 95 %{f', CV {arguments.cv:g}' if arguments.cv else ''})"
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


def classe_debit(texte: str) -> tuple[str, float]:
    """Débit contractuel d'une classe : ``EF=2M``."""
    logger.debug("classe_debit : {!r}", texte)
    classe, sep, valeur = texte.partition("=")
    if not sep or not classe.strip():
        logger.debug("classe_debit : forme invalide")
        raise argparse.ArgumentTypeError(f"forme CLASSE=DEBIT attendue : {texte!r} (ex. EF=2M)")
    try:
        calcul.dscp(classe)
    except ValueError as exc:
        logger.debug("classe_debit : classe inconnue ({})", exc)
        raise argparse.ArgumentTypeError(str(exc)) from exc
    resultat = (classe.strip().upper(), debit(valeur))
    logger.debug("classe_debit : {}", resultat)
    return resultat


def _options_plan(p: argparse.ArgumentParser) -> None:
    """Options communes à ``efv plan`` et ``efv injecter`` : lien, plan, profil, CIR."""
    logger.debug("_options_plan : {}", p.prog)
    p.add_argument("--lien", type=debit, required=True, help="débit effectif du lien (10M, 1G…)")
    p.add_argument("--plan", type=Path, help="plan YAML (défaut : plan du protocole)")
    p.add_argument(
        "--profil",
        help="sous-ensemble déclaré dans le plan (minimal : environ 1 jour) ; défaut : complet",
    )
    p.add_argument(
        "--cir",
        type=classe_debit,
        action="append",
        default=[],
        metavar="CLASSE=DEBIT",
        help="débit contractuel d'une classe, pour Q2 et Q4 (répétable : --cir EF=2M)",
    )
    logger.debug("_options_plan : ajoutées")


def _charger(arguments: argparse.Namespace) -> tuple[list[plan.Test], list[plan.Execution]]:
    """Plan (fichier ou défaut, profil éventuel) et ses exécutions pour le lien demandé.

    Raises:
        OSError: Plan illisible.
        ValueError: Plan ou profil invalide.
    """
    logger.debug("_charger : plan={} profil={}", arguments.plan, arguments.profil)
    if arguments.plan:
        tests = plan.charger_plan(arguments.plan, arguments.profil)
    else:
        tests = plan.plan_par_defaut(arguments.profil)
    execs = plan.executions(tests, arguments.lien, dict(arguments.cir))
    logger.debug("_charger : {} exécutions", len(execs))
    return tests, execs


def _ajouter_plan(sous: argparse._SubParsersAction) -> None:
    """Sous-commande ``efv plan`` (§7.8 à §8.3 du protocole)."""
    logger.debug("ajout de la sous-commande plan")
    p = sous.add_parser(
        "plan",
        help="plan de tests converti en débits absolus pour un lien (§7.8 à §8.3)",
        description="Charge et valide le plan YAML, puis affiche chaque exécution pour ce lien.",
    )
    _options_plan(p)
    p.add_argument(
        "--format", choices=("table", "csv"), default="table", help="sortie (défaut table)"
    )
    p.set_defaults(action=commande_plan)
    logger.debug("sous-commande plan ajoutée")


_COLONNES = (
    "id",
    "rep",
    "serie",
    "groupe",
    "proto",
    "-b",
    "-l",
    "-S",
    "duree",
    "ports",
    "sampling",
    "attendu",
    "note",
)


def _ligne_plan(e: plan.Execution) -> list[str]:
    """Valeurs d'une exécution, dans l'ordre de ``_COLONNES``."""
    logger.debug("_ligne_plan : {}", e.test.id)
    t = e.test
    if e.debit_bps is not None:
        b = e.iperf_b or ""
    elif t.debit is not None and t.debit.base == "cir":
        b = "CIR ?"
    else:
        b = "max"
    duree = (
        f"{t.duree_s}s"
        if t.duree_s is not None
        else f"-n {plan.format_debit(t.volume_octets or 0)}"
    )
    ports = str(t.ports[0]) if len(t.ports) == 1 else f"{t.ports[0]}-{t.ports[-1]}"
    port = "" if t.port_destination is None else f"port destination {t.port_destination}"
    notes = [x for x in ("lab" if t.lab else "", port, t.prealable or "") if x]
    valeurs = [
        t.id,
        str(e.repetition),
        t.serie,
        t.groupe or "",
        t.protocole or "",
        b,
        "" if e.charge_udp is None else str(e.charge_udp),
        "" if t.tos is None else f"{t.tos:#04x}",
        duree,
        ports,
        "nominal" if t.sampling is None else f"1:{t.sampling}",
        ", ".join(f"{c}:{g}" for c, g in t.attendu),
        " ; ".join(notes),
    ]
    logger.debug("_ligne_plan : {}", valeurs)
    return valeurs


def commande_plan(arguments: argparse.Namespace) -> int:
    """Affiche le plan converti pour le lien demandé (table Markdown ou CSV)."""
    logger.debug("commande_plan : {}", arguments)
    try:
        tests, execs = _charger(arguments)
    except (OSError, ValueError) as exc:
        logger.error("plan : {}", exc)
        return 2
    lignes = [_ligne_plan(e) for e in execs]
    if arguments.format == "csv":
        ecrivain = csv.writer(sys.stdout, lineterminator="\n")
        ecrivain.writerow(_COLONNES)
        ecrivain.writerows(lignes)
    else:
        print("| " + " | ".join(_COLONNES) + " |")
        print("|" + "---|" * len(_COLONNES))
        for ligne in lignes:
            print("| " + " | ".join(ligne) + " |")
        manuels = [t.id for t in tests if t.manuel is not None]
        duree = plan.duree_totale(execs, arguments.lien)
        print()
        if arguments.profil == "recette":
            print(
                "Recette qos-audit : comparer ses verdicts à la colonne « attendu » "
                "(comparaison automatique : #6, qos-audit#36)"
            )
        elif arguments.profil:
            print(
                f"Profil {arguments.profil} : un verdict « Validé » sur ce profil "
                "ne vaut pas validation complète"
            )
        print(f"{len(execs)} exécutions, durée {duree / 3600:.1f} h (pauses de 5 min incluses)")
        if manuels:
            print(f"Tests manuels (hors iperf) : {', '.join(manuels)}")
        if any(row[5] == "CIR ?" for row in lignes):
            print("Débit contractuel manquant pour certains tests : préciser --cir CLASSE=DEBIT")
    logger.debug("commande_plan : {} exécutions", len(execs))
    return 0


def _ajouter_injecter(sous: argparse._SubParsersAction) -> None:
    """Sous-commande ``efv injecter`` (§7.7 du protocole)."""
    logger.debug("ajout de la sous-commande injecter")
    p = sous.add_parser(
        "injecter",
        help="injection iperf2 et relevé nft vers resultats.csv (§7.7)",
        description=(
            "Déroule le plan : compteur nft par port source, iperf2, horodatage, une ligne par "
            "test dans resultats.csv, pause entre deux tests. À lancer sur le client du banc."
        ),
    )
    _options_plan(p)
    p.add_argument(
        "--test",
        action="append",
        default=[],
        metavar="SELECTEUR",
        help="restreint aux tests désignés : identifiant, motif (A*) ou groupe (répétable)",
    )
    banc = injection.Banc()
    p.add_argument("--serveur", default=banc.serveur, help=f"serveur iperf (défaut {banc.serveur})")
    p.add_argument("--client", default=banc.client, help=f"IP source du client ({banc.client})")
    p.add_argument(
        "--port-destination",
        type=int,
        default=banc.port_destination,
        help=f"port du serveur iperf (défaut {banc.port_destination})",
    )
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path("resultats.csv"),
        help="fichier des résultats (complété)",
    )
    p.add_argument(
        "--pause", type=float, default=plan.PAUSE_S, help="pause entre deux tests, en s (300)"
    )
    p.add_argument("--lab", action="store_true", help="inclut les tests réservés au laboratoire")
    p.add_argument("--dry-run", action="store_true", help="affiche les commandes sans les exécuter")
    p.set_defaults(action=commande_injecter)
    logger.debug("sous-commande injecter ajoutée")


def _selectionner(execs: list[plan.Execution], selecteurs: list[str]) -> list[plan.Execution]:
    """Exécutions des tests désignés (identifiant, motif ou groupe) ; toutes sans sélecteur.

    Raises:
        ValueError: Sélecteur qui ne désigne aucun test.
    """
    logger.debug("_selectionner : {}", selecteurs)
    if not selecteurs:
        logger.debug("_selectionner : tout le plan")
        return execs
    choisies: list[plan.Execution] = []
    for sel in selecteurs:
        trouvees = [e for e in execs if fnmatch.fnmatchcase(e.test.id, sel) or e.test.groupe == sel]
        if not trouvees:
            logger.debug("_selectionner : {} sans test", sel)
            raise ValueError(f"--test {sel} : aucun test correspondant")
        choisies += trouvees
    retenues = [e for e in execs if e in choisies]
    logger.debug("_selectionner : {} exécutions", len(retenues))
    return retenues


def commande_injecter(arguments: argparse.Namespace) -> int:
    """Injecte le plan (ou sa sélection) et complète ``resultats.csv``."""
    logger.debug("commande_injecter : {}", arguments)
    banc = injection.Banc(arguments.serveur, arguments.client, arguments.port_destination)
    systeme = injection.SystemeReel()
    try:
        _, execs = _charger(arguments)
        execs = _selectionner(execs, arguments.test)
        n = injection.injecter(
            execs,
            arguments.sortie,
            systeme,
            banc=banc,
            pause_s=arguments.pause,
            lab=arguments.lab,
            dry_run=arguments.dry_run,
        )
    except (OSError, ValueError) as exc:
        logger.error("injecter : {}", exc)
        return 2
    except RuntimeError as exc:
        logger.error("injection interrompue : {}", exc)
        return 1
    finally:
        systeme.fermer()
    print(f"{n} lancement(s) {'simulé(s)' if arguments.dry_run else 'terminé(s)'}")
    logger.debug("commande_injecter : {} lancement(s)", n)
    return 0


def _ajouter_lire(sous: argparse._SubParsersAction) -> None:
    """Sous-commande ``efv lire`` (§9 du protocole)."""
    logger.debug("_ajouter_lire : entrée")
    p = sous.add_parser(
        "lire",
        help="lecture dans ElastiFlow des tests de resultats.csv vers lectures.csv (§9)",
        description=(
            "Une requête d'agrégation par test : sommes brutes d'octets et de paquets par "
            "exportateur, sommes hors minutes de bord, sampling-rate et DSCP lus. URL et "
            "identifiants dans l'environnement : EFV_ES_URL, EFV_ES_API_KEY ou EFV_ES_USER et "
            "EFV_ES_PASSWORD, EFV_ES_CA (facultatif)."
        ),
    )
    p.add_argument(
        "--resultats", type=Path, default=Path("resultats.csv"), help="sortie d'injecter"
    )
    p.add_argument("--sortie", type=Path, default=Path("lectures.csv"), help="fichier écrit")
    p.add_argument("--index", default=lecture.INDEX_DEFAUT, help="index ou motif des flux")
    p.add_argument("--champs", type=Path, help="YAML « clé: nom de champ » (§9.2, P4)")
    p.add_argument(
        "--delai",
        type=int,
        default=lecture.DELAI_S,
        help=f"délai d'agrégation après la fin du test, en s ({lecture.DELAI_S}, P7)",
    )
    p.add_argument(
        "--bord", type=int, default=lecture.BORD_S, help="bord exclu de la fenêtre utile, en s"
    )
    p.add_argument(
        "--series", type=Path, help="série par minute utile de chaque test (CSV, §9.3, #31)"
    )
    p.add_argument(
        "--stabilite", type=Path, help="verdict de constance du débit par test (CSV, §10.1)"
    )
    p.add_argument(
        "--cv", type=float, default=0.0, help="CV des tailles de paquets pour la borne (défaut 0)"
    )
    banc = injection.Banc()
    p.add_argument("--serveur", default=banc.serveur, help="à défaut de colonne serveur")
    p.add_argument("--client", default=banc.client, help="à défaut de colonne client")
    p.add_argument(
        "--port-destination",
        type=int,
        default=banc.port_destination,
        help="à défaut de colonne port_destination",
    )
    p.set_defaults(action=commande_lire)
    logger.debug("_ajouter_lire : sortie")


def commande_lire(arguments: argparse.Namespace, client: lecture.Client | None = None) -> int:
    """Lit dans ElastiFlow les tests dont le délai d'agrégation est écoulé."""
    logger.debug("commande_lire : entrée {} -> {}", arguments.resultats, arguments.sortie)
    banc = injection.Banc(arguments.serveur, arguments.client, arguments.port_destination)
    try:
        champs = lecture.charger_champs(arguments.champs)
        client = client or lecture.ClientHttp(lecture.connexion_depuis_env(os.environ))
        n = lecture.lire(
            arguments.resultats,
            arguments.sortie,
            client,
            index=arguments.index,
            champs=champs,
            banc=banc,
            delai_s=arguments.delai,
            bord_s=arguments.bord,
            maintenant=time.time,
            series=arguments.series,
            verdicts=arguments.stabilite,
            cv=arguments.cv,
        )
    except (OSError, ValueError) as exc:
        logger.error("lire : {}", exc)
        return 2
    except RuntimeError as exc:
        logger.error("lecture interrompue : {}", exc)
        return 1
    print(f"{n} test(s) lu(s) -> {arguments.sortie}")
    logger.debug("commande_lire : sortie {} test(s)", n)
    return 0
