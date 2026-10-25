"""Plan de tests (protocole §7.6 à §8.3) : chargement validé et conversion en débits absolus.

Le plan est un fichier YAML (``plans/protocole.yaml`` par défaut) décrivant chaque test : série,
protocole, débit (% du lien, % du débit contractuel de la classe, absolu ou ``max``), taille IP,
DSCP ou octet TOS, durée ou volume, répétitions, sampling pendant le test. Les ports sources sont
attribués dans l'ordre du fichier, ``40000 + N`` avec N à partir de 1, un port par processus iperf.
"""

from __future__ import annotations

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
PROTOCOLES = {"udp", "tcp"}
_CHAMPS = {
    "id", "serie", "groupe", "protocole", "debit", "taille", "dscp", "tos", "duree", "volume",
    "repetitions", "processus", "sampling", "prealable", "lab", "objectif", "manuel",
}  # fmt: skip
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
        raise ValueError(f"{ident} : protocole {protocole!r} (udp ou tcp attendu)")
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
    sampling = brut.get("sampling", "nominal")
    if sampling != "nominal" and (
        isinstance(sampling, bool) or not isinstance(sampling, int) or sampling < 1
    ):
        logger.debug("_test : sampling invalide")
        raise ValueError(
            f"{ident} : sampling « nominal » ou entier ≥ 1 attendu (lu : {sampling!r})"
        )
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
        sampling=None if sampling == "nominal" else sampling,
        groupe=None if brut.get("groupe") is None else str(brut["groupe"]),
        ports=tuple(PORT_BASE + n + i for i in range(processus)),
        prealable=brut.get("prealable"),
        lab=bool(brut.get("lab", False)),
        objectif=str(brut.get("objectif", "")),
    )
    logger.debug("_test : {} ports {}", ident, test.ports)
    return test


def charger_plan(source: Path | str) -> list[Test]:
    """Charge et valide un plan YAML (chemin, ou texte YAML si ``source`` est une chaîne).

    Raises:
        ValueError: Plan invalide (champ manquant ou inconnu, identifiant en double, port > 65535…).
    """
    logger.debug("charger_plan : entrée {}", source if isinstance(source, Path) else "<texte>")
    texte = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
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
    logger.debug("charger_plan : {} tests", len(tests))
    return tests


def plan_par_defaut() -> list[Test]:
    """Plan du protocole livré avec le paquet (``plans/protocole.yaml``)."""
    logger.debug("plan_par_defaut : entrée")
    texte = (
        resources.files("elastiflow_validation")
        .joinpath("plans/protocole.yaml")
        .read_text(encoding="utf-8")
    )
    tests = charger_plan(texte)
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
            charge_udp=calcul.charge_udp(t.taille) if t.protocole == "udp" and t.taille else None,
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
