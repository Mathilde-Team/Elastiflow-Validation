# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture dans ElastiFlow : une requête d'agrégation par test de ``resultats.csv`` (§9, issue #5).

Pour chaque ligne : filtre sur la clé connue (IP source et destination, ports source, port de
destination, protocole) et la fenêtre du test (début − 1 min, fin + délai d'agrégation), puis, par
exportateur, sommes **brutes** des octets et des paquets, nombre d'enregistrements, sommes utiles
hors minutes de bord (histogramme par minute), sampling-rates et DSCP lus. Aucune correction n'est
appliquée ici (base trame ou IP, facteur d'échantillonnage : phase 0, ``efv rapport``).

Les noms de champs spécifiques à ElastiFlow dépendent de la version du schéma (§9.2, #14) : ils se
surchargent par un fichier YAML et sont vérifiés dans ``_mapping`` avant toute lecture. L'URL et les
identifiants sont lus dans l'environnement et ne sont jamais journalisés.
"""

from __future__ import annotations

import base64
import csv
import json
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml
from loguru import logger

from elastiflow_validation import stabilite
from elastiflow_validation.injection import Banc

__all__ = [
    "BORD_S",
    "CHAMPS_DEFAUT",
    "CHAMPS_OPTIONNELS",
    "COLONNES_LECTURE",
    "DELAI_S",
    "INDEX_DEFAUT",
    "Client",
    "ClientHttp",
    "Connexion",
    "champs_absents",
    "charger_champs",
    "connexion_depuis_env",
    "fenetre_utile",
    "lire",
    "lire_test",
    "requete_test",
]

INDEX_DEFAUT = "elastiflow-flow-*"
DELAI_S = 600  # délai d'agrégation et d'indexation (§9.3 : 5 à 10 min, relevé en P7)
BORD_S = 60  # minutes de bord exclues de la fenêtre utile (montée en charge, fin de flux)
# Noms alignés sur qos-audit (elastiflow.FIELDS) ; champs ElastiFlow à confirmer en P4 (#14).
CHAMPS_DEFAUT = {
    "horodatage": "@timestamp",
    "ip_source": "source.ip",
    "ip_destination": "destination.ip",
    "port_source": "source.port",
    "port_destination": "destination.port",
    "protocole": "network.transport",
    "octets": "network.bytes",
    "paquets": "network.packets",
    "exportateur": "flow.export.host.name",
    "sampling": "flow.sampling.interval",
    "dscp": "flow.ip.dscp",
}
# Champs facultatifs (#30) : sous-totaux par interface et échantillonneur, ignorés s'ils manquent
# au mapping. Un paquet prélevé à l'entrée d'un port et à la sortie d'un autre du même switch donne
# deux enregistrements aux interfaces d'entrée et de sortie identiques (qos-audit#26) : seul
# l'échantillonneur (sFlow source_id_index, nom ElastiFlow à relever en P4, sans défaut) les
# distingue.
CHAMPS_OPTIONNELS = {
    "interface_entree": "flow.input.ifindex",
    "interface_sortie": "flow.output.ifindex",
    "echantillonneur": "",
}
COLONNES_LECTURE = (
    "id",
    "repetition",
    "exportateur",
    "enregistrements",
    "octets",
    "paquets",
    "octets_utiles",
    "paquets_utiles",
    "duree_utile_s",
    "debit_utile_bps",
    "sampling",
    "dscp",
    "debut_fenetre",
    "fin_fenetre",
    "interfaces",
    "echantillonneurs",
    "double_echantillonnage",
)


class Client(Protocol):
    """Accès HTTP à Elasticsearch ou OpenSearch (remplacé par un faux client dans les tests)."""

    def requete(self, methode: str, chemin: str, corps: dict | None = None) -> dict:
        """Réponse JSON de ``methode chemin`` (corps JSON facultatif)."""
        ...


@dataclass(frozen=True)
class Connexion:
    """URL du cluster et en-têtes d'authentification (exclus de ``repr`` : jamais affichés)."""

    url: str
    entetes: dict[str, str] = field(default_factory=dict, repr=False)
    ca: str | None = None


def _hote(url: str) -> str:
    """Hôte d'une URL, seul élément journalisé (pas d'identifiants ``user:mdp@``)."""
    logger.debug("_hote : entrée")
    hote = urllib.parse.urlsplit(url).hostname or "?"
    logger.debug("_hote : sortie {}", hote)
    return hote


def connexion_depuis_env(env: Mapping[str, str]) -> Connexion:
    """Connexion depuis ``EFV_ES_URL`` et ``EFV_ES_API_KEY`` ou ``EFV_ES_USER``/``EFV_ES_PASSWORD``.

    ``EFV_ES_CA`` désigne facultativement le certificat de l'autorité du cluster.

    Raises:
        ValueError: ``EFV_ES_URL`` absent, ou utilisateur sans mot de passe.
    """
    logger.debug("connexion_depuis_env : entrée")
    url = env.get("EFV_ES_URL", "").rstrip("/")
    if not url:
        logger.debug("connexion_depuis_env : URL absente")
        raise ValueError("EFV_ES_URL absent : URL d'Elasticsearch ou d'OpenSearch")
    entetes = {"Content-Type": "application/json"}
    if env.get("EFV_ES_API_KEY"):
        entetes["Authorization"] = f"ApiKey {env['EFV_ES_API_KEY']}"
    elif env.get("EFV_ES_USER"):
        if not env.get("EFV_ES_PASSWORD"):
            logger.debug("connexion_depuis_env : mot de passe absent")
            raise ValueError("EFV_ES_USER sans EFV_ES_PASSWORD")
        jeton = base64.b64encode(f"{env['EFV_ES_USER']}:{env['EFV_ES_PASSWORD']}".encode()).decode()
        entetes["Authorization"] = f"Basic {jeton}"
    connexion = Connexion(url, entetes, env.get("EFV_ES_CA") or None)
    logger.debug(
        "connexion_depuis_env : {} (authentification : {})", _hote(url), "Authorization" in entetes
    )
    return connexion


class ClientHttp:
    """Client urllib : requêtes JSON, erreurs HTTP converties en ``RuntimeError`` sans secret."""

    def __init__(self, connexion: Connexion, delai_s: float = 30) -> None:
        logger.debug("ClientHttp : entrée {}", _hote(connexion.url))
        self._connexion = connexion
        self._delai_s = delai_s
        self._ssl = ssl.create_default_context(cafile=connexion.ca) if connexion.ca else None
        logger.debug("ClientHttp : prêt")

    def requete(self, methode: str, chemin: str, corps: dict | None = None) -> dict:
        """Réponse JSON ; ``RuntimeError`` sur erreur HTTP ou réseau."""
        logger.debug("requete : entrée {} {}", methode, chemin)
        donnees = None if corps is None else json.dumps(corps).encode()
        req = urllib.request.Request(
            self._connexion.url + chemin,
            data=donnees,
            headers=self._connexion.entetes,
            method=methode,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._delai_s, context=self._ssl) as rep:
                reponse = json.loads(rep.read().decode())
        except urllib.error.HTTPError as exc:
            logger.error("requete : HTTP {} sur {} {}", exc.code, methode, chemin)
            raise RuntimeError(f"{methode} {chemin} : HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            logger.error("requete : {} {} injoignable ({})", methode, chemin, type(exc).__name__)
            raise RuntimeError(f"{methode} {chemin} : cluster injoignable ({exc})") from exc
        logger.debug("requete : sortie {} {}", methode, chemin)
        return reponse


def charger_champs(chemin: Path | None) -> dict[str, str]:
    """Noms de champs : défauts (``CHAMPS_DEFAUT``) surchargés par un YAML ``cle: nom``.

    Les clés de ``CHAMPS_OPTIONNELS`` sont admises ; leurs défauts sont ajoutés par ``lire``.

    Raises:
        ValueError: Clé inconnue ou valeur non textuelle.
    """
    logger.debug("charger_champs : entrée {}", chemin)
    champs = dict(CHAMPS_DEFAUT)
    if chemin is None:
        logger.debug("charger_champs : défauts")
        return champs
    brut = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    if not isinstance(brut, dict):
        logger.debug("charger_champs : pas un dictionnaire")
        raise ValueError(f"{chemin} : dictionnaire « clé: nom de champ » attendu")
    inconnues = sorted(set(brut) - set(CHAMPS_DEFAUT) - set(CHAMPS_OPTIONNELS))
    if inconnues or not all(isinstance(v, str) and v for v in brut.values()):
        logger.debug("charger_champs : invalide {}", inconnues)
        raise ValueError(
            f"{chemin} : clés connues {', '.join([*CHAMPS_DEFAUT, *CHAMPS_OPTIONNELS])} ; "
            "noms de champs non vides "
            f"(inconnues : {', '.join(inconnues) or 'aucune'})"
        )
    champs.update(brut)
    logger.debug("charger_champs : {} surcharge(s)", len(brut))
    return champs


def _feuilles(proprietes: dict, prefixe: str = "") -> set[str]:
    """Chemins des champs d'un mapping (``properties`` imbriqués et sous-champs ``fields``)."""
    logger.debug("_feuilles : entrée {}", prefixe or "racine")
    noms: set[str] = set()
    for nom, defn in proprietes.items():
        chemin = f"{prefixe}{nom}"
        noms.add(chemin)
        noms |= _feuilles(defn.get("properties", {}), f"{chemin}.")
        noms |= {f"{chemin}.{sous}" for sous in defn.get("fields", {})}
    logger.debug("_feuilles : sortie {} champ(s)", len(noms))
    return noms


def champs_absents(client: Client, index: str, champs: Mapping[str, str]) -> list[str]:
    """Champs utilisés absents du mapping de tous les index de ``index`` (vérifié au démarrage)."""
    logger.debug("champs_absents : entrée {}", index)
    mapping = client.requete("GET", f"/{index}/_mapping")
    connus: set[str] = set()
    for defn in mapping.values():
        connus |= _feuilles(defn.get("mappings", {}).get("properties", {}))
    absents = sorted(nom for nom in set(champs.values()) if nom not in connus)
    logger.debug("champs_absents : sortie {}", absents)
    return absents


def _ports(texte: str) -> tuple[int, int]:
    """Plage de ports source d'une ligne : ``40012`` ou ``40019-40026``."""
    logger.debug("_ports : entrée {}", texte)
    bas, _, haut = texte.partition("-")
    plage = (int(bas), int(haut or bas))
    logger.debug("_ports : sortie {}", plage)
    return plage


def _filtre_port_destination(champ: str, texte: str) -> dict:
    """Filtre du port de destination : ``term`` (un port) ou ``range`` (iperf3, un serveur par
    processus simultané : ``50001-50008``)."""
    logger.debug("_filtre_port_destination : entrée {}", texte)
    bas, haut = _ports(texte)
    plage = {"range": {champ: {"gte": bas, "lte": haut}}}
    filtre = {"term": {champ: bas}} if bas == haut else plage
    logger.debug("_filtre_port_destination : sortie {}", filtre)
    return filtre


def fenetre_utile(debut: int, fin: int, bord_s: int = BORD_S) -> tuple[int, int]:
    """Minutes entières du test hors bords : ``[début + bord, fin − bord]`` arrondi à la minute.

    Returns:
        ``(début, fin)`` en secondes epoch, multiples de 60 ; ``fin == début`` si le test est trop
        court pour une minute utile.
    """
    logger.debug("fenetre_utile : entrée {} {}", debut, fin)
    bas = math.ceil((debut + bord_s) / 60) * 60
    haut = max(bas, math.floor((fin - bord_s) / 60) * 60)
    logger.debug("fenetre_utile : sortie {} {}", bas, haut)
    return bas, haut


def requete_test(
    ligne: Mapping[str, str],
    champs: Mapping[str, str],
    banc: Banc,
    delai_s: int = DELAI_S,
) -> dict:
    """Corps ``_search`` d'un test : filtre sur la clé et la fenêtre, agrégations par exportateur.

    Les colonnes ``client``, ``serveur`` et ``port_destination`` de la ligne priment sur ``banc``
    (fichiers produits avant leur ajout : valeurs du banc). ``ports`` vide : clé IP et protocole
    seuls (tests ``cle: ip`` du plan, G2 et G5).
    """
    logger.debug("requete_test : entrée {}#{}", ligne["id"], ligne["repetition"])
    debut, fin = int(ligne["debut"]), int(ligne["fin"])
    filtres: list[dict] = [
        {"term": {champs["ip_source"]: ligne.get("client") or banc.client}},
        {"term": {champs["ip_destination"]: ligne.get("serveur") or banc.serveur}},
        {
            "range": {
                champs["horodatage"]: {
                    "gte": (debut - 60) * 1000,
                    "lte": (fin + delai_s) * 1000,
                    "format": "epoch_millis",
                }
            }
        },
    ]
    if ligne.get("ports"):  # vide : clé sans ports (ICMP, fragments sans en-tête L4, #33)
        bas, haut = _ports(ligne["ports"])
        filtres.append({"range": {champs["port_source"]: {"gte": bas, "lte": haut}}})
        filtres.append(
            _filtre_port_destination(  # plage possible : iperf3, un serveur par processus
                champs["port_destination"],
                ligne.get("port_destination") or str(banc.port_destination),
            )
        )
    if ligne.get("protocole"):
        filtres.append({"term": {champs["protocole"]: ligne["protocole"]}})
    sommes = {
        "octets": {"sum": {"field": champs["octets"]}},
        "paquets": {"sum": {"field": champs["paquets"]}},
    }
    par_exportateur: dict = {
        **sommes,
        "sampling": {"terms": {"field": champs["sampling"], "size": 10}},
        "dscp": {"terms": {"field": champs["dscp"], "size": 10}},
        "minutes": {
            "date_histogram": {"field": champs["horodatage"], "fixed_interval": "1m"},
            "aggs": sommes,
        },
    }
    if champs.get("interface_entree") and champs.get("interface_sortie"):  # #30, facultatif
        par_exportateur["entrees"] = {
            "terms": {"field": champs["interface_entree"], "size": 10, "missing": -1},
            "aggs": {
                "sorties": {
                    "terms": {"field": champs["interface_sortie"], "size": 10, "missing": -1},
                    "aggs": {"octets": sommes["octets"]},
                }
            },
        }
    if champs.get("echantillonneur"):
        par_exportateur["echantillonneurs"] = {
            "terms": {"field": champs["echantillonneur"], "size": 10}
        }
    corps = {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": filtres}},
        "aggs": {
            "exportateurs": {
                "terms": {"field": champs["exportateur"], "size": 50},
                "aggs": par_exportateur,
            }
        },
    }
    logger.debug("requete_test : sortie ports {}", ligne.get("ports") or "aucun")
    return corps


def _valeurs(agg: dict) -> str:
    """Valeurs distinctes d'une agrégation ``terms`` : ``"8;1"`` (ordre de fréquence)."""
    logger.debug("_valeurs : entrée")
    texte = ";".join(str(b["key"]) for b in agg.get("buckets", []))
    logger.debug("_valeurs : sortie {}", texte)
    return texte


def _entier(valeur: float) -> str:
    """Somme Elasticsearch (flottant) en entier textuel."""
    logger.debug("_entier : entrée {}", valeur)
    texte = str(round(valeur))
    logger.debug("_entier : sortie {}", texte)
    return texte


def _interfaces(exp: dict) -> str:
    """Octets par couple d'interfaces : ``"12>3=4500;12>-1=20"`` (-1 : champ absent)."""
    logger.debug("_interfaces : entrée")
    couples = [
        f"{e['key']}>{s['key']}={round(s['octets']['value'])}"
        for e in exp.get("entrees", {}).get("buckets", [])
        for s in e["sorties"]["buckets"]
    ]
    texte = ";".join(couples)
    logger.debug("_interfaces : sortie {}", texte)
    return texte


def _double(exp: dict) -> str:
    """``oui`` si plusieurs échantillonneurs d'un exportateur ont vu le test (double comptage)."""
    logger.debug("_double : entrée")
    double = "oui" if len(exp.get("echantillonneurs", {}).get("buckets", [])) > 1 else ""
    logger.debug("_double : sortie {}", double or "non")
    return double


def lire_test(
    client: Client,
    index: str,
    ligne: Mapping[str, str],
    champs: Mapping[str, str],
    banc: Banc,
    delai_s: int = DELAI_S,
    bord_s: int = BORD_S,
    series: list[tuple[str, str, str, list[stabilite.Minute]]] | None = None,
) -> list[list[str]]:
    """Lignes de ``lectures.csv`` d'un test, une par exportateur (aucune si rien n'est indexé).

    ``series`` : si fourni, reçoit la série par minute utile de chaque exportateur (#31).
    """
    logger.debug("lire_test : entrée {}#{}", ligne["id"], ligne["repetition"])
    corps = requete_test(ligne, champs, banc, delai_s)
    reponse = client.requete("POST", f"/{index}/_search", corps)
    debut, fin = int(ligne["debut"]), int(ligne["fin"])
    utile_bas, utile_haut = fenetre_utile(debut, fin, bord_s)
    duree_utile = utile_haut - utile_bas
    lignes: list[list[str]] = []
    for exp in reponse.get("aggregations", {}).get("exportateurs", {}).get("buckets", []):
        minutes = [
            b for b in exp["minutes"]["buckets"] if utile_bas * 1000 <= b["key"] < utile_haut * 1000
        ]
        octets_utiles = sum(b["octets"]["value"] for b in minutes)
        debit = "" if not duree_utile else f"{octets_utiles * 8 / duree_utile:.0f}"
        lignes.append(
            [
                ligne["id"], ligne["repetition"], str(exp["key"]), str(exp["doc_count"]),
                _entier(exp["octets"]["value"]), _entier(exp["paquets"]["value"]),
                _entier(octets_utiles), _entier(sum(b["paquets"]["value"] for b in minutes)),
                str(duree_utile), debit, _valeurs(exp["sampling"]), _valeurs(exp["dscp"]),
                str(debut - 60), str(fin + delai_s),
                _interfaces(exp), _valeurs(exp.get("echantillonneurs", {})), _double(exp),
            ]
        )  # fmt: skip
        if lignes[-1][-1]:
            logger.warning(
                "{}#{} : {} vu par plusieurs échantillonneurs ({}) : double comptage probable",
                ligne["id"], ligne["repetition"], exp["key"], lignes[-1][-2],
            )  # fmt: skip
        if series is not None:
            serie = stabilite.serie_minutes(minutes, utile_bas, utile_haut)
            series.append((ligne["id"], ligne["repetition"], str(exp["key"]), serie))
    logger.debug("lire_test : sortie {} exportateur(s)", len(lignes))
    return lignes


def lire(
    resultats: Path,
    sortie: Path,
    client: Client,
    index: str = INDEX_DEFAUT,
    champs: Mapping[str, str] | None = None,
    banc: Banc | None = None,
    delai_s: int = DELAI_S,
    bord_s: int = BORD_S,
    maintenant: Callable[[], float] | None = None,
    series: Path | None = None,
    verdicts: Path | None = None,
    cv: float = 0.0,
) -> int:
    """Lit chaque test de ``resultats`` et écrit ``sortie`` (``lectures.csv``, réécrit).

    ``series`` et ``verdicts`` (facultatifs, #31) : série par minute utile de chaque test et
    exportateur, et verdict de constance du débit (``stabilite.juger``, CV des tailles ``cv``).

    Les tests dont la fenêtre n'est pas close (fin + délai dans le futur) sont écartés : leurs
    enregistrements ne sont pas encore tous indexés (§9.3).

    Returns:
        Nombre de tests lus.

    Raises:
        ValueError: Champ utilisé absent du mapping de ``index``.
    """
    logger.debug("lire : entrée {} -> {}", resultats, sortie)
    champs = dict(champs or CHAMPS_DEFAUT)
    for cle, nom in CHAMPS_OPTIONNELS.items():
        if nom:
            champs.setdefault(cle, nom)
    banc = banc or Banc()
    optionnels = {k: v for k, v in champs.items() if k in CHAMPS_OPTIONNELS}
    obligatoires = {k: v for k, v in champs.items() if k not in CHAMPS_OPTIONNELS}
    absents = champs_absents(client, index, champs)
    for cle, nom in optionnels.items():
        if nom in absents:  # facultatif (#30) : la lecture se fait sans ce sous-total
            logger.warning(
                "champ facultatif {} ({}) absent du mapping de {} : ignoré", cle, nom, index
            )
            del champs[cle]
    absents = [nom for nom in absents if nom in obligatoires.values()]
    if absents:
        logger.debug("lire : champs absents {}", absents)
        raise ValueError(
            f"champs absents du mapping de {index} : {', '.join(absents)} "
            "(relever les noms réels en P4 et les préciser avec --champs, §9.2)"
        )
    with resultats.open(encoding="utf-8", newline="") as f:
        tests = list(csv.DictReader(f))
    if maintenant is not None:
        limite = maintenant()
        en_attente = [t["id"] for t in tests if int(t["fin"]) + delai_s > limite]
        if en_attente:
            logger.warning("lecture différée (délai d'agrégation non écoulé) : {}", en_attente)
        tests = [t for t in tests if int(t["fin"]) + delai_s <= limite]
    lignes: list[list[str]] = []
    collecte: list[tuple[str, str, str, list[stabilite.Minute]]] | None = None
    if series is not None or verdicts is not None:
        collecte = []
    for t in tests:
        lus = lire_test(client, index, t, champs, banc, delai_s, bord_s, collecte)
        if not lus:
            logger.warning("{}#{} : aucun enregistrement dans ElastiFlow", t["id"], t["repetition"])
        lignes += lus
    with sortie.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f, lineterminator="\n")
        ecrivain.writerow(COLONNES_LECTURE)
        ecrivain.writerows(lignes)
    if series is not None:
        stabilite.ecrire_series(series, collecte or [], cv)
    if verdicts is not None:
        instables = stabilite.ecrire_verdicts(verdicts, collecte or [], cv)
        if instables:
            logger.warning("{} série(s) au débit non constant -> {}", instables, verdicts)
    logger.debug("lire : sortie {} test(s), {} ligne(s)", len(tests), len(lignes))
    return len(tests)
