"""Formules du protocole de validation (§3, §4, §7.3, §8.1, §12.1).

- précision d'un échantillonnage sFlow : borne à 95 % ``196/√n``, n cible ``(196/e)²`` (§3.1) ;
  sur les octets, avec des tailles dispersées : ``196·√((1 + CV²)/n)`` (§3.1, #10) ;
- probabilité de voir un flux : ``1 − exp(−n attendu)`` (§3.3) ;
- sampling-rate recommandé : puissance de 2 inférieure de
  ``débit plancher × fenêtre / (8 × taille × n cible)`` (§4.1, §4.3) ;
- DSCP ↔ octet TOS (valeur ``-S`` d'iperf) et classes de l'opérateur (§8.1, §12.1) ;
- taille IP ↔ option ``-l`` UDP d'iperf ↔ trame Ethernet (§7.3).

Les tableaux 3.2, 4.2, 7.3 et 12.1 du protocole sont rejoués par les tests.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from loguru import logger

__all__ = [
    "CLASSES",
    "Dimensionnement",
    "charge_udp",
    "cv_tailles",
    "dimensionner",
    "dscp",
    "dscp_de_tos",
    "erreur_95",
    "erreur_95_octets",
    "fragmente",
    "n_cible",
    "n_cible_octets",
    "paquets",
    "probabilite_detection",
    "sampling_rate",
    "taille_ip_de_charge",
    "tos",
    "trame_ethernet",
]

Z_95 = 196  # 1,96 × 100 : la borne s'exprime directement en pour cent
ENTETES_IP_UDP = 28  # 20 octets IP + 8 octets UDP (§7.3)
ENTETE_ETHERNET = 14
TAG_VLAN = 4
FCS = 4
MTU_ETHERNET = 1500

# Classes de l'opérateur (§8.1) et DSCP usuels (§12.1). D1, D2 et D3 sont des hypothèses courantes,
# à confirmer avec le contrat de l'opérateur ; « DSCP45 » est la variante « EF 45 » testée en Q1.
CLASSES: dict[str, int] = {
    "BF": 0,
    "BE": 0,
    "CS0": 0,
    "CS1": 8,
    "AF11": 10,
    "D3": 10,
    "AF12": 12,
    "AF13": 14,
    "CS2": 16,
    "AF21": 18,
    "D2": 18,
    "AF22": 20,
    "AF23": 22,
    "CS3": 24,
    "AF31": 26,
    "D1": 26,
    "AF32": 28,
    "AF33": 30,
    "CS4": 32,
    "AF41": 34,
    "AF42": 36,
    "AF43": 38,
    "CS5": 40,
    "DSCP45": 45,
    "EF": 46,
    "CS6": 48,
    "CS7": 56,
}


def n_cible(erreur_pct: float = 5.0) -> int:
    """Nombre d'échantillons pour une erreur cible à 95 % : ``⌈(196/e)²⌉`` (5 % → 1 537)."""
    logger.debug("n_cible : erreur={} %", erreur_pct)
    if erreur_pct <= 0:
        logger.debug("n_cible : erreur cible invalide")
        raise ValueError(f"erreur cible invalide : {erreur_pct} (doit être > 0)")
    n = math.ceil((Z_95 / erreur_pct) ** 2)
    logger.debug("n_cible : {}", n)
    return n


def erreur_95(n: float) -> float:
    """Borne d'erreur à 95 %, en pour cent, pour ``n`` échantillons : ``196/√n`` (∞ si n = 0)."""
    logger.debug("erreur_95 : n={}", n)
    if n < 0:
        logger.debug("erreur_95 : n négatif")
        raise ValueError(f"nombre d'échantillons négatif : {n}")
    erreur = math.inf if n == 0 else Z_95 / math.sqrt(n)
    logger.debug("erreur_95 : {} %", erreur)
    return erreur


def cv_tailles(tailles: Sequence[float], poids: Sequence[float] | None = None) -> float:
    """Coefficient de variation (écart type / moyenne) de la taille des paquets.

    Args:
        tailles: Tailles des paquets, ou tailles distinctes d'un mélange si ``poids`` est fourni.
        poids: Part (ou nombre) de paquets de chaque taille ; ``None`` : un paquet par taille.

    Returns:
        CV de la distribution (écart type de population) ; 0 pour des tailles fixes (iperf ``-l``).
    """
    logger.debug("cv_tailles : {} tailles, poids={}", len(tailles), poids is not None)
    w = [1.0] * len(tailles) if poids is None else list(poids)
    if not tailles or len(w) != len(tailles) or any(x < 0 for x in w) or sum(w) <= 0:
        logger.debug("cv_tailles : entrée invalide")
        raise ValueError("tailles non vides et poids positifs de même longueur attendus")
    if any(t <= 0 for t in tailles):
        logger.debug("cv_tailles : taille non positive")
        raise ValueError("tailles strictement positives attendues")
    total = sum(w)
    moyenne = sum(t * x for t, x in zip(tailles, w, strict=True)) / total
    variance = sum(x * (t - moyenne) ** 2 for t, x in zip(tailles, w, strict=True)) / total
    cv = math.sqrt(variance) / moyenne
    logger.debug("cv_tailles : moyenne={} o, cv={}", moyenne, cv)
    return cv


def erreur_95_octets(n: float, cv: float) -> float:
    """Borne d'erreur à 95 % sur les **octets** : ``196·√((1 + CV²)/n)`` (∞ si n = 0).

    La borne ``196/√n`` ne vaut pour les octets que si tous les paquets ont la même taille (CV = 0).
    """
    logger.debug("erreur_95_octets : n={}, cv={}", n, cv)
    if cv < 0:
        logger.debug("erreur_95_octets : cv négatif")
        raise ValueError(f"coefficient de variation négatif : {cv}")
    erreur = erreur_95(n) * math.sqrt(1 + cv**2)
    logger.debug("erreur_95_octets : {} %", erreur)
    return erreur


def n_cible_octets(erreur_pct: float = 5.0, cv: float = 0.0) -> int:
    """Échantillons pour une erreur cible sur les octets : ``⌈(196/e)² × (1 + CV²)⌉``."""
    logger.debug("n_cible_octets : erreur={} %, cv={}", erreur_pct, cv)
    if erreur_pct <= 0 or cv < 0:
        logger.debug("n_cible_octets : paramètres invalides")
        raise ValueError(f"erreur > 0 et CV ≥ 0 attendus : {erreur_pct}, {cv}")
    # Arrondi à 1e-9 avant ⌈⌉ : neutralise l'erreur flottante du produit (2 603,0000…1 → 2 603).
    n = math.ceil(round((Z_95 / erreur_pct) ** 2 * (1 + cv**2), 9))
    logger.debug("n_cible_octets : {}", n)
    return n


def probabilite_detection(n_attendu: float) -> float:
    """Probabilité de voir au moins un échantillon d'un flux : ``1 − exp(−n attendu)`` (§3.3)."""
    logger.debug("probabilite_detection : n attendu={}", n_attendu)
    if n_attendu < 0:
        logger.debug("probabilite_detection : n négatif")
        raise ValueError(f"nombre d'échantillons attendu négatif : {n_attendu}")
    p = -math.expm1(-n_attendu)
    logger.debug("probabilite_detection : {}", p)
    return p


def paquets(debit_bps: float, fenetre_s: float, taille_ip: int) -> float:
    """Nombre de paquets IP de ``taille_ip`` octets émis à ``debit_bps`` pendant ``fenetre_s``."""
    logger.debug(
        "paquets : debit={} bit/s, fenetre={} s, taille={} o", debit_bps, fenetre_s, taille_ip
    )
    if debit_bps < 0 or fenetre_s <= 0 or taille_ip <= 0:
        logger.debug("paquets : paramètres invalides")
        raise ValueError("débit ≥ 0, fenêtre > 0 et taille > 0 attendus")
    nombre = debit_bps * fenetre_s / (8 * taille_ip)
    logger.debug("paquets : {}", nombre)
    return nombre


def sampling_rate(
    debit_lien_bps: float,
    plancher: float = 0.05,
    fenetre_s: float = 300,
    taille_ip: int = 1500,
    erreur_pct: float = 5.0,
    cv: float = 0.0,
) -> int:
    """Sampling-rate N (puissance de 2 inférieure) qui garde l'erreur cible au plancher (§4.1).

    Args:
        debit_lien_bps: Débit effectif du circuit (un 1G bridé à 100 Mbit/s compte pour 100 Mbit/s).
        plancher: Part du lien à mesurer précisément (0,05 = 5 %).
        fenetre_s: Fenêtre d'analyse en secondes.
        taille_ip: Taille IP des paquets pour la précision (1500 o = cas le moins favorable).
        erreur_pct: Erreur cible à 95 %, en pour cent.
        cv: Coefficient de variation de la taille des paquets (0 : erreur sur les paquets,
            ou octets à taille fixe ; > 0 : erreur sur les octets du trafic réel).

    Returns:
        N, au moins 1 (1:1 = comptage exhaustif).
    """
    logger.debug(
        "sampling_rate : lien={} bit/s, plancher={}, fenetre={} s, taille={} o, erreur={} %, cv={}",
        debit_lien_bps,
        plancher,
        fenetre_s,
        taille_ip,
        erreur_pct,
        cv,
    )
    if not 0 < plancher <= 1:
        logger.debug("sampling_rate : plancher invalide")
        raise ValueError(f"plancher invalide : {plancher} (part du lien, entre 0 exclu et 1)")
    n_max = max(
        1.0,
        paquets(debit_lien_bps * plancher, fenetre_s, taille_ip) / n_cible_octets(erreur_pct, cv),
    )
    rate = 2 ** math.floor(math.log2(n_max))
    logger.debug("sampling_rate : 1:{}", rate)
    return rate


@dataclass(frozen=True)
class Dimensionnement:
    """Ligne du tableau 4.2 : sampling-rate retenu, résultat au plancher et au pire cas."""

    debit_lien_bps: float
    plancher_bps: float
    rate: int
    echantillons_plancher: float
    erreur_pct: float | None  # None : 1:1, comptage exhaustif, la borne ne s'applique pas
    echantillons_s_sature: float


def dimensionner(
    debit_lien_bps: float,
    plancher: float = 0.05,
    fenetre_s: float = 300,
    taille_ip: int = 1500,
    erreur_pct: float = 5.0,
    taille_cout: int = 100,
    cv: float = 0.0,
) -> Dimensionnement:
    """Ligne du tableau 4.2 : précision sur ``taille_ip``, coût sur ``taille_cout``."""
    logger.debug("dimensionner : lien={} bit/s", debit_lien_bps)
    rate = sampling_rate(debit_lien_bps, plancher, fenetre_s, taille_ip, erreur_pct, cv)
    plancher_bps = debit_lien_bps * plancher
    n = paquets(plancher_bps, fenetre_s, taille_ip) / rate
    ligne = Dimensionnement(
        debit_lien_bps=debit_lien_bps,
        plancher_bps=plancher_bps,
        rate=rate,
        echantillons_plancher=n,
        erreur_pct=None if rate == 1 else erreur_95_octets(n, cv),
        echantillons_s_sature=paquets(debit_lien_bps, 1, taille_cout) / rate,
    )
    logger.debug("dimensionner : {}", ligne)
    return ligne


def dscp(valeur: str | int) -> int:
    """DSCP d'une classe (``EF``, ``af41``, ``D1``, ``CS6``…) ou d'une valeur 0-63."""
    logger.debug("dscp : {!r}", valeur)
    texte = str(valeur).strip().upper()
    if texte in CLASSES:
        logger.debug("dscp : classe {} = {}", texte, CLASSES[texte])
        return CLASSES[texte]
    if texte.isdigit() and 0 <= int(texte) <= 63:
        logger.debug("dscp : valeur {}", int(texte))
        return int(texte)
    logger.debug("dscp : inconnu")
    raise ValueError(f"DSCP inconnu : {valeur!r} (classe connue ou entier 0-63)")


def tos(valeur_dscp: int, ecn: int = 0) -> int:
    """Octet TOS (valeur ``-S`` d'iperf) : ``DSCP × 4 + ECN`` (EF 46 → 0xB8)."""
    logger.debug("tos : dscp={}, ecn={}", valeur_dscp, ecn)
    if not 0 <= valeur_dscp <= 63 or not 0 <= ecn <= 3:
        logger.debug("tos : hors plage")
        raise ValueError(f"DSCP 0-63 et ECN 0-3 attendus : {valeur_dscp}, {ecn}")
    octet = valeur_dscp << 2 | ecn
    logger.debug("tos : {:#04x}", octet)
    return octet


def dscp_de_tos(octet: int) -> int:
    """DSCP porté par un octet TOS (les 2 bits ECN de poids faible sont ignorés)."""
    logger.debug("dscp_de_tos : {}", octet)
    if not 0 <= octet <= 255:
        logger.debug("dscp_de_tos : hors plage")
        raise ValueError(f"octet TOS hors plage : {octet}")
    logger.debug("dscp_de_tos : {}", octet >> 2)
    return octet >> 2


def charge_udp(taille_ip: int) -> int:
    """Option ``-l`` d'iperf (charge UDP) pour un paquet IP de ``taille_ip`` octets (1000 → 972)."""
    logger.debug("charge_udp : taille IP={}", taille_ip)
    if taille_ip <= ENTETES_IP_UDP:
        logger.debug("charge_udp : taille trop petite")
        raise ValueError(f"taille IP {taille_ip} ≤ {ENTETES_IP_UDP} octets d'en-têtes IP + UDP")
    logger.debug("charge_udp : -l {}", taille_ip - ENTETES_IP_UDP)
    return taille_ip - ENTETES_IP_UDP


def taille_ip_de_charge(charge: int) -> int:
    """Taille du paquet IP pour une option ``-l`` UDP (972 → 1000)."""
    logger.debug("taille_ip_de_charge : -l {}", charge)
    if charge <= 0:
        logger.debug("taille_ip_de_charge : charge invalide")
        raise ValueError(f"charge UDP invalide : {charge}")
    logger.debug("taille_ip_de_charge : {}", charge + ENTETES_IP_UDP)
    return charge + ENTETES_IP_UDP


def trame_ethernet(taille_ip: int, vlan: bool = False, fcs: bool = False) -> int:
    """Trame Ethernet : IP + 14, + 4 avec un tag VLAN, + 4 si les compteurs incluent le FCS."""
    logger.debug("trame_ethernet : taille IP={}, vlan={}, fcs={}", taille_ip, vlan, fcs)
    trame = taille_ip + ENTETE_ETHERNET + (TAG_VLAN if vlan else 0) + (FCS if fcs else 0)
    logger.debug("trame_ethernet : {}", trame)
    return trame


def fragmente(taille_ip: int, mtu: int = MTU_ETHERNET) -> bool:
    """Vrai si un paquet IP de cette taille dépasse le MTU (``-l`` > 1472 sur un MTU de 1500)."""
    logger.debug("fragmente : taille IP={}, mtu={}", taille_ip, mtu)
    logger.debug("fragmente : {}", taille_ip > mtu)
    return taille_ip > mtu
