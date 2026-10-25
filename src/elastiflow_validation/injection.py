"""Injection (protocole §7.7) : pilotage d'iperf2 et relevé de la vérité terrain nft.

Remplace ``run_test.sh``. Pour chaque lancement du plan (un test, ou les tests d'un groupe partis
ensemble, une répétition à la fois) :

1. une règle nft ``counter`` par port source, dans la chaîne ``output`` de la table ``inet t`` ;
2. un processus iperf2 par port (``-B CLIENT:PORT``), horodaté au début et à la fin ;
3. après 2 s, lecture des compteurs (``nft -j``), suppression des règles, une ligne par test dans
   ``resultats.csv`` : la **référence** de l'analyse (section 10) ;
4. pause de 5 min avant le lancement suivant.

Les changements de sampling sur l'exporteur, les commandes préalables (``tc … netem``) et les tests
réservés au laboratoire restent des gestes de l'opérateur : l'outil les annonce et attend sa
confirmation. Le mode simulation (``dry_run``) affiche les commandes sans rien exécuter.
"""

from __future__ import annotations

import csv
import json
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from loguru import logger

from elastiflow_validation import plan

__all__ = [
    "COLONNES",
    "Banc",
    "Lancement",
    "Systeme",
    "SystemeReel",
    "commande_iperf",
    "injecter",
    "lancements",
    "lire_compteurs",
]

TABLE = ("inet", "t")
CHAINE = "out"
ATTENTE_NFT_S = 2  # délai avant la lecture des compteurs (run_test.sh)
COLONNES = (
    "id",
    "repetition",
    "serie",
    "groupe",
    "protocole",
    "debit",
    "taille",
    "tos",
    "ports",
    "sampling",
    "debut",
    "fin",
    "paquets",
    "octets",
    "client",
    "serveur",
    "port_destination",
)


@dataclass(frozen=True)
class Banc:
    """Adresses du banc (§7.3) : serveur iperf, IP source du client, port de destination."""

    serveur: str = "10.20.0.2"
    client: str = "10.10.0.2"
    port_destination: int = 50001


@dataclass(frozen=True)
class Lancement:
    """Exécutions parties ensemble : un test, ou un groupe (Q3, Q4), pour une répétition."""

    cle: str
    repetition: int
    executions: tuple[plan.Execution, ...]

    @property
    def sampling(self) -> int | None:
        """Sampling attendu sur l'exporteur pendant le lancement (``None`` : nominal)."""
        return self.executions[0].test.sampling


class Processus(Protocol):
    """Processus lancé par le système (sous-ensemble de ``subprocess.Popen``)."""

    def wait(self) -> int: ...


class Systeme(Protocol):
    """Accès au système : commandes, processus, horloge, opérateur (remplaçable en test)."""

    def executer(self, commande: Sequence[str]) -> str: ...

    def lancer(self, commande: Sequence[str], journal: Path) -> Processus: ...

    def maintenant(self) -> int: ...

    def dormir(self, secondes: float) -> None: ...

    def confirmer(self, message: str) -> None: ...


@dataclass
class SystemeReel:
    """Système réel : ``subprocess``, horloge et saisie de l'opérateur sur le terminal."""

    saisie: Callable[[str], str] = input
    _journaux: list[IO[str]] = field(default_factory=list)

    def executer(self, commande: Sequence[str]) -> str:
        """Exécute une commande et renvoie sa sortie standard.

        Raises:
            RuntimeError: Code de retour non nul ou commande introuvable.
        """
        logger.debug("executer : {}", shlex.join(commande))
        try:
            fini = subprocess.run(list(commande), capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or exc
            logger.error("commande en échec : {} ({})", shlex.join(commande), detail)
            raise RuntimeError(f"{shlex.join(commande)} : {detail}") from exc
        logger.debug("executer : {} octets en sortie", len(fini.stdout))
        return fini.stdout

    def lancer(self, commande: Sequence[str], journal: Path) -> Processus:
        """Lance une commande en tâche de fond, sortie dans ``journal``."""
        logger.debug("lancer : {} > {}", shlex.join(commande), journal)
        sortie = journal.open("w", encoding="utf-8")
        self._journaux.append(sortie)
        processus = subprocess.Popen(list(commande), stdout=sortie, stderr=subprocess.STDOUT)
        logger.debug("lancer : pid {}", processus.pid)
        return processus

    def maintenant(self) -> int:
        """Epoch en secondes."""
        return int(time.time())

    def dormir(self, secondes: float) -> None:
        """Attend ``secondes``."""
        logger.debug("dormir : {} s", secondes)
        time.sleep(secondes)
        logger.debug("dormir : fin")

    def confirmer(self, message: str) -> None:
        """Affiche un geste à faire et attend Entrée."""
        logger.debug("confirmer : {}", message)
        self.saisie(f"{message}\nEntrée quand c'est fait… ")
        logger.debug("confirmer : confirmé")

    def fermer(self) -> None:
        """Ferme les journaux iperf ouverts."""
        logger.debug("fermer : {} journal(aux)", len(self._journaux))
        for sortie in self._journaux:
            sortie.close()
        self._journaux.clear()
        logger.debug("fermer : fait")


def _commentaire(e: plan.Execution, port: int) -> str:
    """Commentaire de la règle nft d'un port : identifie test, répétition et port."""
    return f"efv {e.test.id}#{e.repetition} {port}"


def commande_iperf(e: plan.Execution, port: int, banc: Banc) -> list[str]:
    """Commande iperf2 d'une exécution pour un port source (§7.7).

    Raises:
        ValueError: Test manuel, ou débit contractuel (``% cir``) non fourni.
    """
    logger.debug("commande_iperf : {}#{} port {}", e.test.id, e.repetition, port)
    t = e.test
    if t.protocole is None:
        logger.debug("commande_iperf : test manuel")
        raise ValueError(f"{t.id} : test manuel, pas de commande iperf")
    if e.debit_bps is None and t.debit is not None and t.debit.base == "cir":
        logger.debug("commande_iperf : CIR manquant")
        raise ValueError(f"{t.id} : débit contractuel de la classe non fourni (--cir)")
    cmd = ["iperf", "-c", banc.serveur]
    if t.protocole == "udp":
        cmd += ["-u", "-l", str(e.charge_udp)]
    if e.iperf_b is not None:
        cmd += ["-b", e.iperf_b]
    cmd += ["-p", str(t.port_destination or banc.port_destination)]
    cmd += ["-t", str(t.duree_s)] if t.duree_s is not None else ["-n", str(t.volume_octets)]
    cmd += ["-i", "10", "-e", "-B", f"{banc.client}:{port}", "-S", f"{t.tos or 0:#04x}"]
    logger.debug("commande_iperf : {}", shlex.join(cmd))
    return cmd


def lancements(execs: Sequence[plan.Execution]) -> list[Lancement]:
    """Regroupe les exécutions : tests d'un même groupe ensemble, une répétition à la fois."""
    logger.debug("lancements : {} exécutions", len(execs))
    groupes: dict[tuple[str, int], list[plan.Execution]] = {}
    for e in execs:
        if e.test.manuel is not None:
            continue
        groupes.setdefault((e.test.groupe or e.test.id, e.repetition), []).append(e)
    resultat = [Lancement(cle, rep, tuple(es)) for (cle, rep), es in groupes.items()]
    logger.debug("lancements : {}", len(resultat))
    return resultat


def _commandes_nft_initiales() -> list[list[str]]:
    """Table et chaîne de comptage du client (créées si absentes, comme run_test.sh)."""
    return [
        ["nft", "add", "table", *TABLE],
        ["nft", "add", "chain", *TABLE, CHAINE, "{ type filter hook output priority 0; }"],
    ]


def _regle_nft(e: plan.Execution, port: int) -> list[str]:
    """Règle ``counter`` d'un port source."""
    logger.debug("_regle_nft : {}#{} port {}", e.test.id, e.repetition, port)
    proto = e.test.protocole or ""
    regle = ["nft", "add", "rule", *TABLE, CHAINE, proto, "sport", str(port), "counter",
             "comment", f'"{_commentaire(e, port)}"']  # fmt: skip
    logger.debug("_regle_nft : {}", regle)
    return regle


def lire_compteurs(json_nft: str) -> dict[str, tuple[int, int, int]]:
    """Compteurs des règles efv d'une sortie ``nft -j list chain``.

    Returns:
        Commentaire → (handle, paquets, octets).

    Raises:
        ValueError: Sortie nft illisible.
    """
    logger.debug("lire_compteurs : {} caractères", len(json_nft))
    try:
        objets = json.loads(json_nft)["nftables"]
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("sortie nft illisible : {}", exc)
        raise ValueError(f"sortie nft illisible : {exc}") from exc
    compteurs: dict[str, tuple[int, int, int]] = {}
    for objet in objets:
        regle = objet.get("rule") if isinstance(objet, dict) else None
        if not regle or not str(regle.get("comment", "")).startswith("efv "):
            continue
        compteur = next((x["counter"] for x in regle.get("expr", []) if "counter" in x), None)
        if compteur is not None:
            valeurs = (int(regle["handle"]), int(compteur["packets"]), int(compteur["bytes"]))
            compteurs[regle["comment"]] = valeurs
    logger.debug("lire_compteurs : {} règle(s)", len(compteurs))
    return compteurs


def _ligne(
    e: plan.Execution, debut: int, fin: int, paquets: int, octets: int, banc: Banc
) -> list[str]:
    """Ligne de ``resultats.csv`` d'une exécution (ordre de ``COLONNES``) ; le banc donne la clé
    de lecture dans ElastiFlow (``efv lire``)."""
    logger.debug("_ligne : {}#{}", e.test.id, e.repetition)
    t = e.test
    ports = str(t.ports[0]) if len(t.ports) == 1 else f"{t.ports[0]}-{t.ports[-1]}"
    ligne = [
        t.id, str(e.repetition), t.serie, t.groupe or "", t.protocole or "", e.iperf_b or "max",
        "" if t.taille is None else str(t.taille), f"{t.tos or 0:#04x}", ports,
        "nominal" if t.sampling is None else f"1:{t.sampling}",
        str(debut), str(fin), str(paquets), str(octets),
        banc.client, banc.serveur, str(t.port_destination or banc.port_destination),
    ]  # fmt: skip
    logger.debug("_ligne : {} paquets, {} octets", paquets, octets)
    return ligne


def _preparer(lancement: Lancement, systeme: Systeme, sampling_courant: int | None) -> int | None:
    """Gestes de l'opérateur avant un lancement : sampling de l'exporteur, commandes préalables.

    Returns:
        Sampling de l'exporteur après préparation (``None`` : nominal).
    """
    logger.debug("_preparer : {}#{}", lancement.cle, lancement.repetition)
    if lancement.sampling != sampling_courant:
        cible = "nominal (tableau 4.2)" if lancement.sampling is None else f"1:{lancement.sampling}"
        systeme.confirmer(f"[{lancement.cle}] Régler le sampling de l'exporteur : {cible}")
    prealables = sorted({e.test.prealable for e in lancement.executions if e.test.prealable})
    for commande in prealables:
        systeme.confirmer(f"[{lancement.cle}] Préalable : {commande}")
    logger.debug("_preparer : {} préalable(s)", len(prealables))
    return lancement.sampling


def _executer_lancement(
    lancement: Lancement, systeme: Systeme, banc: Banc, journaux: Path
) -> list[list[str]]:
    """Règles nft, processus iperf en parallèle, relevé des compteurs ; lignes du CSV.

    Raises:
        RuntimeError: iperf en échec, ou compteur nft introuvable.
    """
    logger.debug("_executer_lancement : {}#{}", lancement.cle, lancement.repetition)
    paires = [(e, port) for e in lancement.executions for port in e.test.ports]
    commandes = [(e, port, commande_iperf(e, port, banc)) for e, port in paires]
    for e, port in paires:
        systeme.executer(_regle_nft(e, port))
    debut = systeme.maintenant()
    processus = [
        systeme.lancer(cmd, journaux / f"log_{e.test.id}_{e.repetition}_{port}.txt")
        for e, port, cmd in commandes
    ]
    codes = [p.wait() for p in processus]
    fin = systeme.maintenant()
    systeme.dormir(ATTENTE_NFT_S)
    compteurs = lire_compteurs(systeme.executer(["nft", "-j", "list", "chain", *TABLE, CHAINE]))
    lignes: list[list[str]] = []
    manquants: list[str] = []
    for e in lancement.executions:
        paquets = octets = 0
        for port in e.test.ports:
            valeurs = compteurs.get(_commentaire(e, port))
            if valeurs is None:
                manquants.append(_commentaire(e, port))
                continue
            handle, p, o = valeurs
            paquets, octets = paquets + p, octets + o
            systeme.executer(["nft", "delete", "rule", *TABLE, CHAINE, "handle", str(handle)])
        lignes.append(_ligne(e, debut, fin, paquets, octets, banc))
    if any(codes) or manquants:
        logger.debug("_executer_lancement : échec (codes {}, manquants {})", codes, manquants)
        raise RuntimeError(
            f"{lancement.cle}#{lancement.repetition} : iperf codes {codes}, compteurs absents "
            f"{manquants} (journaux dans {journaux})"
        )
    logger.debug("_executer_lancement : {} -> {} ({} ligne(s))", debut, fin, len(lignes))
    return lignes


def _simuler(lancement: Lancement, banc: Banc) -> list[str]:
    """Commandes qu'exécuterait un lancement (mode simulation)."""
    logger.debug("_simuler : {}#{}", lancement.cle, lancement.repetition)
    lignes = [f"# {lancement.cle} répétition {lancement.repetition}"]
    if lancement.sampling is not None:
        lignes.append(f"# sampling de l'exporteur : 1:{lancement.sampling}")
    for e in lancement.executions:
        if e.test.prealable:
            lignes.append(f"# préalable : {e.test.prealable}")
        for port in e.test.ports:
            lignes.append(shlex.join(_regle_nft(e, port)))
            lignes.append(shlex.join(commande_iperf(e, port, banc)) + " &")
    lignes.append("wait")
    logger.debug("_simuler : {} ligne(s)", len(lignes))
    return lignes


def injecter(
    execs: Sequence[plan.Execution],
    sortie: Path,
    systeme: Systeme,
    banc: Banc | None = None,
    pause_s: float = plan.PAUSE_S,
    lab: bool = False,
    dry_run: bool = False,
    afficher: Callable[[str], None] = print,
) -> int:
    """Déroule les exécutions et ajoute une ligne par test à ``sortie`` (``resultats.csv``).

    Args:
        execs: Exécutions du plan (``plan.executions``), dans l'ordre.
        sortie: Fichier CSV des résultats (créé avec son en-tête, sinon complété).
        systeme: Accès au système (``SystemeReel`` en production).
        banc: Adresses du banc.
        pause_s: Pause entre deux lancements.
        lab: Inclure les tests réservés au laboratoire (série E).
        dry_run: Afficher les commandes sans rien exécuter ni écrire.
        afficher: Sortie des messages (commandes simulées, avancement).

    Returns:
        Nombre de lancements exécutés (ou simulés).

    Raises:
        ValueError: Débit contractuel manquant pour un test retenu.
        RuntimeError: Commande nft ou iperf en échec (les lignes déjà écrites sont conservées).
    """
    logger.debug("injecter : {} exécutions vers {}", len(execs), sortie)
    banc = banc or Banc()
    retenues = [e for e in execs if lab or not e.test.lab]
    ecartes = sorted({e.test.id for e in execs if e.test.lab and not lab})
    if ecartes:
        afficher(f"Tests de laboratoire écartés (--lab pour les inclure) : {', '.join(ecartes)}")
    a_faire = lancements(retenues)
    for lancement in a_faire:  # contrôle avant d'injecter quoi que ce soit
        for e in lancement.executions:
            commande_iperf(e, e.test.ports[0], banc)
    if dry_run:
        for lancement in a_faire:
            afficher("\n".join(_simuler(lancement, banc)))
        logger.debug("injecter : {} lancement(s) simulé(s)", len(a_faire))
        return len(a_faire)
    if systeme.executer(["nft", "list", "tables"]).find(" ".join(("table", *TABLE))) < 0:
        for commande in _commandes_nft_initiales():
            systeme.executer(commande)
    journaux = sortie.parent
    nouveau = not sortie.exists() or sortie.stat().st_size == 0
    sampling_courant: int | None = None  # l'exporteur est supposé au sampling nominal au départ
    with sortie.open("a", encoding="utf-8", newline="") as fichier:
        ecrivain = csv.writer(fichier, lineterminator="\n")
        if nouveau:
            ecrivain.writerow(COLONNES)
        for i, lancement in enumerate(a_faire):
            if i:
                afficher(f"Pause de {pause_s:.0f} s")
                systeme.dormir(pause_s)
            sampling_courant = _preparer(lancement, systeme, sampling_courant)
            afficher(f"[{i + 1}/{len(a_faire)}] {lancement.cle} répétition {lancement.repetition}")
            lignes = _executer_lancement(lancement, systeme, banc, journaux)
            ecrivain.writerows(lignes)
            fichier.flush()
            for ligne in lignes:
                ident, debut, fin, paquets, octets = ligne[0], *ligne[10:14]
                afficher(f"  {ident} {debut} -> {fin} paquets={paquets} octets={octets}")
    logger.debug("injecter : {} lancement(s)", len(a_faire))
    return len(a_faire)
