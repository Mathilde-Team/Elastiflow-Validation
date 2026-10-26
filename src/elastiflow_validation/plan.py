"""Plan de tests (protocole §7.6 à §8.3) : chargement validé et conversion en débits absolus.

Le plan est un fichier YAML (``plans/protocole.yaml`` par défaut) décrivant chaque test : série,
protocole, débit (% du lien, % du débit contractuel de la classe, absolu ou ``max``), taille IP,
DSCP ou octet TOS, durée ou volume, répétitions, sampling pendant le test. Les ports sources sont
attribués dans l'ordre du fichier, ``40000 + N`` avec N à partir de 1, un port par processus iperf.

Une section ``profils`` nomme des sous-ensembles du plan (``minimal`` : environ 1 jour, issue #11).
Un sélecteur désigne un identifiant, un motif (``Q1-*``) ou un groupe ; les ports restent ceux du
plan complet, pour que les compteurs nft d'un test ne changent pas d'un profil à l'autre.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml
from loguru import logger

from elastiflow_validation import calcul

__all__ = [
    "PORT_BASE",
    "Debit",
    "Execution",
    "Test",
    "charger_plan",
    "duree_totale",
    "executions",
    "format_debit",
    "plan_par_defaut",
]

PORT_BASE = 40000
PAUSE_S = 300  # pause entre deux tests (#4)
PROTOCOLES = {"udp", "tcp", "icmp"}
INTERVALLE_PING_S = 0.2  # cadence ICMP fixe (ping -i 0.2, protocole 7.11) : 5 paquets/s
CLES = ("ports", "ip")  # clé de lecture : IP + ports (défaut) ou IP + protocole seul (G2, G5)
_CHAMPS = {
    "id", "serie", "groupe", "protocole", "debit", "taille", "dscp", "tos", "duree", "volume",
    "repetitions", "processus", "sampling", "prealable", "lab", "objectif", "manuel", "attendu",
    "port_destination", "cle",
}  # fmt: skip
# Contrôles de qos-audit (docs/CONTROLES.md) et gravités : verdicts attendus de la recette (#16).
CONTROLES_QOS_AUDIT = {
    "marking", "dscp_plan", "trust", "class_share", "port_range", "saturation", "class_overflow",
    "counter_consistency", "congestion_ecn", "congestion_drops", "exit_balance",
    "sampling_adequacy", "sampling_cost", "sampling_drift", "calibration",
}  # fmt: skip
GRAVITES = ("OK", "INCONCLUSIVE", "WARN", "CRIT")
_DEBIT = re.compile(r"^(?P<v>\d+(?:[.,]\d+)?)\s*(?P<u>%\s*cir|%|[kKmMgG]?)$")
_UNITES = {"": 1.0, "k": 1e3, "m": 1e6, "g": 1e9}


@dataclass(frozen=True)
class Debit:
    """Débit : ``base`` = ``lien``, ``cir``, ``absolu`` ou ``max`` ; ``valeur`` en % ou bit/s."""

    base: str
    valeur: float = 0.0


@dataclass(frozen=True)
class Test:
    """Un test du plan, tel que décrit dans le YAML (ports attribués)."""

    id: str
    serie: str
    protocole: str | None = None
    debit: Debit | None = None
    taille: int | None = None
    tos: int | None = None
    duree_s: int | None = None
    volume_octets: int | None = None
    repetitions: int = 1
    processus: int = 1
    sampling: int | None = None  # None : sampling nominal (tableau 4.2)
    groupe: str | None = None
    ports: tuple[int, ...] = ()
    prealable: str | None = None
    lab: bool = False
    objectif: str = ""
    manuel: str | None = None
    attendu: tuple[tuple[str, str], ...] = ()  # verdicts qos-audit attendus (contrôle, gravité)
    port_destination: int | None = None  # défaut : port du serveur iperf du banc
    cle: str = "ports"  # « ip » : lecture sans ports (ICMP, fragments, #33)

    @property
    def dscp(self) -> int | None:
        """DSCP émis (octet TOS sans les bits ECN)."""
        return None if self.tos is None else self.tos >> 2


@dataclass(frozen=True)
class Execution:
    """Une exécution d'un test pour un lien donné (une ligne par répétition)."""

    test: Test
    repetition: int
    debit_bps: float | None  # None : débit maximal, ou débit contractuel non fourni
    charge_udp: int | None  # option -l d'iperf

    @property
    def iperf_b(self) -> str | None:
        """Valeur de l'option ``-b`` d'iperf, ``None`` sans limite (``max``)."""
        return None if self.debit_bps is None else format_debit(self.debit_bps)


def format_debit(bps: float) -> str:
    """Débit lisible par iperf : ``500k``, ``2.5M``, ``1G``, ``800``."""
    logger.debug("format_debit : {}", bps)
    for unite, facteur in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if bps >= facteur:
            texte = f"{bps / facteur:.6g}{unite}"
            logger.debug("format_debit : {}", texte)
            return texte
    logger.debug("format_debit : {:.6g}", bps)
    return f"{bps:.6g}"


def _debit(valeur: object, ident: str) -> Debit:
    """Analyse le champ ``debit`` (``5%``, ``150% cir``, ``500k``, ``max``)."""
    logger.debug("_debit : {} {!r}", ident, valeur)
    texte = str(valeur).strip()
    if texte.lower() == "max":
        logger.debug("_debit : max")
        return Debit("max")
    m = _DEBIT.match(texte)
    if m is None:
        logger.debug("_debit : illisible")
        raise ValueError(f"{ident} : débit illisible {texte!r} (ex. 5%, 150% cir, 500k, max)")
    v = float(m["v"].replace(",", "."))
    unite = m["u"].replace(" ", "").lower()
    if v <= 0:
        logger.debug("_debit : non positif")
        raise ValueError(f"{ident} : débit non positif {texte!r}")
    if unite in {"%", "%cir"}:
        resultat = Debit("lien" if unite == "%" else "cir", v)
    else:
        resultat = Debit("absolu", v * _UNITES[unite])
    logger.debug("_debit : {}", resultat)
    return resultat


def _entier(brut: dict, cle: str, ident: str, minimum: int = 1) -> int | None:
    """Champ entier optionnel ≥ ``minimum``."""
    logger.debug("_entier : {} {}", ident, cle)
    if cle not in brut:
        logger.debug("_entier : absent")
        return None
    v = brut[cle]
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        logger.debug("_entier : invalide")
        raise ValueError(f"{ident} : {cle} doit être un entier ≥ {minimum} (lu : {v!r})")
    logger.debug("_entier : {}", v)
    return v


def _attendu(brut: dict, ident: str) -> tuple[tuple[str, str], ...]:
    """Verdicts qos-audit attendus : liste de ``controle:GRAVITE`` (scénarios de recette, #16)."""
    logger.debug("_attendu : {}", ident)
    valeurs = brut.get("attendu", [])
    if not isinstance(valeurs, list):
        logger.debug("_attendu : pas une liste")
        raise ValueError(f"{ident} : attendu doit être une liste de « controle:GRAVITE »")
    verdicts: list[tuple[str, str]] = []
    for v in valeurs:
        controle, _, gravite = str(v).partition(":")
        if controle not in CONTROLES_QOS_AUDIT or gravite not in GRAVITES:
            logger.debug("_attendu : verdict invalide {}", v)
            raise ValueError(
                f"{ident} : verdict attendu invalide {v!r} (contrôle qos-audit et "
                f"gravité parmi {', '.join(GRAVITES)})"
            )
        verdicts.append((controle, gravite))
    logger.debug("_attendu : {}", verdicts)
    return tuple(verdicts)


def _port(brut: dict, ident: str) -> int | None:
    """Port de destination propre au test (ex. port RTP), sinon celui du banc."""
    logger.debug("_port : {}", ident)
    port = _entier(brut, "port_destination", ident)
    if port is not None and port > 65535:
        logger.debug("_port : hors plage")
        raise ValueError(f"{ident} : port_destination hors plage (1 à 65535)")
    logger.debug("_port : {}", port)
    return port


def _tos(brut: dict, ident: str) -> int | None:
    """Octet TOS depuis ``dscp`` (classe ou valeur) ou ``tos`` (octet brut, ECN compris)."""
    logger.debug("_tos : {}", ident)
    if "dscp" in brut and "tos" in brut:
        logger.debug("_tos : dscp et tos")
        raise ValueError(f"{ident} : dscp et tos sont exclusifs")
    if "tos" in brut:
        octet = _entier(brut, "tos", ident, minimum=0)
        if octet is not None and octet > 255:
            logger.debug("_tos : hors plage")
            raise ValueError(f"{ident} : tos hors plage ({octet})")
        logger.debug("_tos : {}", octet)
        return octet
    if "dscp" in brut:
        octet = calcul.tos(calcul.dscp(brut["dscp"]))
        logger.debug("_tos : {}", octet)
        return octet
    logger.debug("_tos : aucun")
    return None


def _test(brut: object, n: int) -> Test:
    """Valide une entrée du plan ; ``n`` est le premier N libre (port ``40000 + N``)."""
    logger.debug("_test : entrée n={}", n)
    if not isinstance(brut, dict) or not isinstance(brut.get("id"), str) or not brut["id"]:
        logger.debug("_test : sans identifiant")
        raise ValueError(f"test n° {n} : objet avec un « id » textuel attendu")
    ident = brut["id"]
    inconnus = set(brut) - _CHAMPS
    if inconnus:
        logger.debug("_test : champs inconnus")
        raise ValueError(f"{ident} : champs inconnus {sorted(inconnus)}")
    serie = str(brut.get("serie", ""))
    if "manuel" in brut:
        test = Test(
            id=ident,
            serie=serie,
            manuel=str(brut["manuel"]),
            objectif=str(brut.get("objectif", "")),
        )
        logger.debug("_test : {} manuel", ident)
        return test
    protocole = brut.get("protocole")
    if protocole not in PROTOCOLES:
        logger.debug("_test : protocole invalide")
        raise ValueError(f"{ident} : protocole {protocole!r} (udp, tcp ou icmp attendu)")
    if protocole == "icmp":
        test = _test_icmp(brut, ident, serie)
        logger.debug("_test : {} icmp", ident)
        return test
    if "debit" not in brut:
        logger.debug("_test : sans débit")
        raise ValueError(f"{ident} : débit manquant")
    debit = _debit(brut["debit"], ident)
    taille = _entier(brut, "taille", ident, minimum=calcul.ENTETES_IP_UDP + 1)
    if protocole == "udp" and taille is None:
        logger.debug("_test : udp sans taille")
        raise ValueError(f"{ident} : taille IP obligatoire en UDP (option -l)")
    if protocole == "udp" and debit.base == "max":
        logger.debug("_test : udp sans débit")
        raise ValueError(f"{ident} : un débit est obligatoire en UDP (iperf -b)")
    duree = _entier(brut, "duree", ident)
    volume = None
    if "volume" in brut:
        volume = _debit(brut["volume"], ident)
        if volume.base != "absolu":
            logger.debug("_test : volume invalide")
            raise ValueError(f"{ident} : volume absolu attendu (ex. 1G)")
    if (duree is None) == (volume is None):
        logger.debug("_test : durée et volume")
        raise ValueError(f"{ident} : une durée ou un volume (exclusifs) attendu")
    processus = _entier(brut, "processus", ident) or 1
    test = Test(
        id=ident,
        serie=serie,
        protocole=protocole,
        debit=debit,
        taille=taille,
        tos=_tos(brut, ident),
        duree_s=duree,
        volume_octets=None if volume is None else int(volume.valeur),
        repetitions=_entier(brut, "repetitions", ident) or 1,
        processus=processus,
        sampling=_sampling(brut, ident),
        groupe=None if brut.get("groupe") is None else str(brut["groupe"]),
        ports=tuple(PORT_BASE + n + i for i in range(processus)),
        prealable=brut.get("prealable"),
        lab=bool(brut.get("lab", False)),
        objectif=str(brut.get("objectif", "")),
        attendu=_attendu(brut, ident),
        port_destination=_port(brut, ident),
        cle=_cle(brut, ident),
    )
    logger.debug("_test : {} ports {}", ident, test.ports)
    return test


def _sampling(brut: dict, ident: str) -> int | None:
    """Sampling pendant le test : ``nominal`` (``None``, tableau 4.2) ou N (1:N)."""
    logger.debug("_sampling : {}", ident)
    sampling = brut.get("sampling", "nominal")
    if sampling != "nominal" and (
        isinstance(sampling, bool) or not isinstance(sampling, int) or sampling < 1
    ):
        logger.debug("_sampling : invalide")
        raise ValueError(
            f"{ident} : sampling « nominal » ou entier ≥ 1 attendu (lu : {sampling!r})"
        )
    resultat = None if sampling == "nominal" else sampling
    logger.debug("_sampling : {}", resultat)
    return resultat


def _cle(brut: dict, ident: str) -> str:
    """Clé de lecture : ``ports`` (défaut) ou ``ip`` (fragments sans en-tête L4, #33)."""
    logger.debug("_cle : {}", ident)
    cle = brut.get("cle", "ports")
    if cle not in CLES:
        logger.debug("_cle : invalide")
        raise ValueError(f"{ident} : cle {cle!r} ({' ou '.join(CLES)} attendu)")
    logger.debug("_cle : {}", cle)
    return cle


def _test_icmp(brut: dict, ident: str, serie: str) -> Test:
    """Test ICMP (G2) : ``ping`` à cadence fixe, sans port ni débit, lu par IP et protocole."""
    logger.debug("_test_icmp : {}", ident)
    interdits = sorted({"debit", "volume", "processus", "port_destination", "cle"} & set(brut))
    if interdits:
        logger.debug("_test_icmp : champs interdits")
        raise ValueError(
            f"{ident} : {', '.join(interdits)} sans objet en ICMP (ping à cadence fixe, sans port)"
        )
    taille = _entier(brut, "taille", ident, minimum=calcul.ENTETES_IP_UDP + 1)
    duree = _entier(brut, "duree", ident)
    if taille is None or duree is None:
        logger.debug("_test_icmp : taille ou durée manquante")
        raise ValueError(f"{ident} : taille IP et durée obligatoires en ICMP")
    test = Test(
        id=ident,
        serie=serie,
        protocole="icmp",
        debit=Debit("absolu", taille * 8 / INTERVALLE_PING_S),
        taille=taille,
        tos=_tos(brut, ident),
        duree_s=duree,
        repetitions=_entier(brut, "repetitions", ident) or 1,
        sampling=_sampling(brut, ident),
        groupe=None if brut.get("groupe") is None else str(brut["groupe"]),
        prealable=brut.get("prealable"),
        lab=bool(brut.get("lab", False)),
        objectif=str(brut.get("objectif", "")),
        attendu=_attendu(brut, ident),
        cle="ip",
    )
    logger.debug("_test_icmp : {} octets toutes les {} s", taille, INTERVALLE_PING_S)
    return test


def _selection(tests: list[Test], selecteur: object, profil: str) -> list[Test]:
    """Tests désignés par un sélecteur de profil : identifiant, motif ``fnmatch`` ou groupe."""
    logger.debug("_selection : {} dans {}", selecteur, profil)
    if not isinstance(selecteur, str) or not selecteur:
        logger.debug("_selection : sélecteur invalide")
        raise ValueError(f"profil {profil} : sélecteur invalide {selecteur!r}")
    choisis = [t for t in tests if fnmatch.fnmatchcase(t.id, selecteur) or t.groupe == selecteur]
    if not choisis:
        logger.debug("_selection : aucun test")
        raise ValueError(f"profil {profil} : « {selecteur} » ne désigne aucun test")
    logger.debug("_selection : {} test(s)", len(choisis))
    return choisis


def _profil(brut: dict, tests: list[Test], nom: str) -> list[Test]:
    """Sous-ensemble ``nom`` du plan, dans l'ordre du fichier."""
    logger.debug("_profil : entrée {}", nom)
    profils = brut.get("profils") or {}
    if not isinstance(profils, dict) or not all(isinstance(v, list) for v in profils.values()):
        logger.debug("_profil : section invalide")
        raise ValueError("plan : « profils » attend des listes de sélecteurs par nom")
    if nom not in profils:
        logger.debug("_profil : profil inconnu")
        connus = ", ".join(sorted(profils)) or "aucun"
        raise ValueError(f"plan : profil inconnu « {nom} » (profils : {connus})")
    ids = {t.id for sel in profils[nom] for t in _selection(tests, sel, nom)}
    choisis = [t for t in tests if t.id in ids]
    logger.debug("_profil : {} test(s)", len(choisis))
    return choisis


def _texte_plan(source: Path | str | None) -> str:
    """Texte YAML d'un plan : chemin, texte, ou plan du protocole livré avec le paquet."""
    logger.debug("_texte_plan : entrée")
    if source is None:
        texte = (
            resources.files("elastiflow_validation")
            .joinpath("plans/protocole.yaml")
            .read_text(encoding="utf-8")
        )
    else:
        texte = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
    logger.debug("_texte_plan : {} caractères", len(texte))
    return texte


def charger_plan(source: Path | str, profil: str | None = None) -> list[Test]:
    """Charge et valide un plan YAML (chemin, ou texte YAML si ``source`` est une chaîne).

    Args:
        source: Chemin du plan, ou texte YAML.
        profil: Nom d'un profil de la section ``profils`` (défaut : plan complet).

    Raises:
        ValueError: Plan invalide (champ manquant ou inconnu, identifiant en double, port > 65535,
            profil inconnu ou sélecteur sans test…).
    """
    logger.debug("charger_plan : entrée {}", source if isinstance(source, Path) else "<texte>")
    texte = _texte_plan(source)
    try:
        brut = yaml.safe_load(texte)
    except yaml.YAMLError as exc:
        logger.debug("charger_plan : YAML illisible ({})", exc)
        raise ValueError(f"plan illisible : {exc}") from exc
    if (
        not isinstance(brut, dict)
        or brut.get("version") != 1
        or not isinstance(brut.get("tests"), list)
    ):
        logger.debug("charger_plan : structure invalide")
        raise ValueError("plan : « version: 1 » et une liste « tests » attendues")
    tests: list[Test] = []
    n = 1
    for entree in brut["tests"]:
        test = _test(entree, n)
        n += len(test.ports)
        tests.append(test)
    ids = [t.id for t in tests]
    doublons = sorted({i for i in ids if ids.count(i) > 1})
    if doublons:
        logger.debug("charger_plan : doublons")
        raise ValueError(f"plan : identifiants en double {doublons}")
    if n - 1 + PORT_BASE > 65535:
        logger.debug("charger_plan : ports épuisés")
        raise ValueError("plan : trop de tests, ports sources au-delà de 65535")
    if profil is not None:
        tests = _profil(brut, tests, profil)
    logger.debug("charger_plan : {} tests", len(tests))
    return tests


def plan_par_defaut(profil: str | None = None) -> list[Test]:
    """Plan du protocole livré avec le paquet (``plans/protocole.yaml``), ou l'un de ses profils."""
    logger.debug("plan_par_defaut : entrée {}", profil)
    tests = charger_plan(_texte_plan(None), profil)
    logger.debug("plan_par_defaut : {} tests", len(tests))
    return tests


def _debit_bps(test: Test, lien_bps: float, cir: dict[int, float]) -> float | None:
    """Débit absolu d'un test sur ce lien (``None`` : max, ou débit contractuel non fourni)."""
    logger.debug("_debit_bps : {}", test.id)
    d = test.debit
    if d is None or d.base == "max":
        logger.debug("_debit_bps : sans limite")
        return None
    if d.base == "absolu":
        logger.debug("_debit_bps : absolu")
        return d.valeur
    if d.base == "lien":
        logger.debug("_debit_bps : % du lien")
        return lien_bps * d.valeur / 100
    base = cir.get(test.dscp) if test.dscp is not None else None
    logger.debug("_debit_bps : % du cir ({})", base)
    return None if base is None else base * d.valeur / 100


def executions(
    tests: list[Test], lien_bps: float, cir: dict[str, float] | None = None
) -> list[Execution]:
    """Exécutions (une par répétition) des tests injectables, débits convertis pour ce lien.

    Args:
        tests: Plan chargé.
        lien_bps: Débit effectif du lien.
        cir: Débit contractuel par classe (``{"EF": 2e6}``), pour les débits en ``% cir``.

    Returns:
        Exécutions dans l'ordre du plan ; les tests manuels sont exclus.
    """
    logger.debug("executions : {} tests, lien={} bit/s", len(tests), lien_bps)
    if lien_bps <= 0:
        logger.debug("executions : lien invalide")
        raise ValueError(f"débit de lien non positif : {lien_bps}")
    par_dscp = {calcul.dscp(k): v for k, v in (cir or {}).items()}
    out = [
        Execution(
            test=t,
            repetition=r,
            debit_bps=_debit_bps(t, lien_bps, par_dscp),
            charge_udp=calcul.charge_udp(t.taille)
            if t.protocole in {"udp", "icmp"} and t.taille
            else None,
        )
        for t in tests
        if t.manuel is None
        for r in range(1, t.repetitions + 1)
    ]
    logger.debug("executions : {}", len(out))
    return out


def duree_totale(execs: list[Execution], lien_bps: float, pause_s: int = PAUSE_S) -> float:
    """Durée estimée en secondes : tests d'un même groupe en parallèle, pause entre deux lancements.

    Un test au volume (``-n``) est estimé au débit du lien.
    """
    logger.debug("duree_totale : {} exécutions", len(execs))
    lancements: dict[tuple[str, int], float] = {}
    for e in execs:
        t = e.test
        duree = t.duree_s if t.duree_s is not None else (t.volume_octets or 0) * 8 / lien_bps
        cle = (t.groupe or f"#{t.id}", e.repetition)
        lancements[cle] = max(lancements.get(cle, 0.0), duree)
    total = sum(lancements.values()) + pause_s * max(0, len(lancements) - 1)
    logger.debug("duree_totale : {} s", total)
    return total
