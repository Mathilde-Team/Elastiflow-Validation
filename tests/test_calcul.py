# SPDX-License-Identifier: GPL-3.0-or-later
"""Formules du protocole (#2) : les tableaux 3.2, 4.2, 7.3 et 12.1 servent de référence."""

from __future__ import annotations

import argparse
import math

import pytest

from elastiflow_validation import calcul, cli

# Tableau 4.2 : débit (Mbit/s), plancher (kbit/s), N, échantillons au plancher,
# erreur (None = exhaustif),
# échantillons/s lien saturé de paquets de 100 o.
TABLEAU_4_2 = [
    (1, 50, 1, 1250, None, 1250),
    (2, 100, 1, 2500, None, 2500),
    (4, 200, 2, 2500, 3.9, 2500),
    (5, 250, 4, 1562, 5.0, 1562),
    (10, 500, 8, 1562, 5.0, 1562),
    (20, 1000, 16, 1562, 5.0, 1562),
    (50, 2500, 32, 1953, 4.4, 1953),
    (100, 5000, 64, 1953, 4.4, 1953),
    (200, 10000, 128, 1953, 4.4, 1953),
    (500, 25000, 256, 2441, 4.0, 2441),
    (1000, 50000, 512, 2441, 4.0, 2441),
    (2000, 100000, 1024, 2441, 4.0, 2441),
    (5000, 250000, 2048, 3052, 3.5, 3052),
    (10000, 500000, 4096, 3052, 3.5, 3052),
]


@pytest.mark.parametrize(("mbps", "kbps", "rate", "n", "erreur", "par_s"), TABLEAU_4_2)
def test_tableau_4_2(mbps, kbps, rate, n, erreur, par_s):
    ligne = calcul.dimensionner(mbps * 1e6)
    assert ligne.plancher_bps == kbps * 1e3
    assert ligne.rate == rate
    assert round(ligne.echantillons_plancher) == n
    assert (None if ligne.erreur_pct is None else round(ligne.erreur_pct, 1)) == erreur
    assert round(ligne.echantillons_s_sature) == par_s


def test_cli_rejoue_le_tableau_4_2(capsys):
    assert cli.main(["sampling"]) == 0
    sortie = capsys.readouterr().out.splitlines()
    assert sortie[0] == "n cible : 1537 échantillons (erreur 5 % à 95 %)"
    lignes = sortie[3:]
    assert len(lignes) == len(TABLEAU_4_2)
    for texte, (mbps, kbps, rate, n, erreur, par_s) in zip(lignes, TABLEAU_4_2, strict=True):
        attendu_err = "exhaustif" if erreur is None else f"{erreur:.1f} %".replace(".", ",")
        assert texte == f"| {mbps} | {kbps} | 1:{rate} | {n} | {attendu_err} | {par_s} |"


def test_cli_un_lien_et_options(capsys):
    assert cli.main(["sampling", "10M", "--fenetre", "1800", "--erreur", "10"]) == 0
    sortie = capsys.readouterr().out.splitlines()
    assert sortie[0].startswith("n cible : 385 ")
    # §4.3 : fenêtre × 6 et erreur 10 % (N × 4) -> 8 × 24 = 192, puissance de 2 inférieure 128
    assert "| 10 | 500 | 1:128 |" in sortie[3]


def test_cli_parametre_invalide():
    assert cli.main(["sampling", "10M", "--plancher", "0"]) == 2


def test_python_du_protocole():
    """Le code Python du §4.3 du protocole donne 1:8 et ~5 % pour 10 Mbit/s."""
    assert calcul.sampling_rate(10e6) == 8
    assert round(calcul.erreur_95(calcul.paquets(10e6 * 0.05, 300, 1500) / 8), 1) == 5.0


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("10M", 10e6),
        ("500k", 500e3),
        ("1G", 1e9),
        ("10e6", 10e6),
        ("2,5M", 2.5e6),
        ("100Mbps", 1e8),
    ],
)
def test_debit(texte, attendu):
    assert cli.debit(texte) == pytest.approx(attendu)


@pytest.mark.parametrize("texte", ["abc", "M", "-1M", "0", "nan", "infG"])
def test_debit_invalide(texte):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.debit(texte)


def test_n_cible_et_erreur():
    assert calcul.n_cible() == 1537
    assert calcul.n_cible(10) == 385
    assert calcul.erreur_95(1537) == pytest.approx(5.0, abs=0.01)
    assert calcul.erreur_95(0) == math.inf
    with pytest.raises(ValueError):
        calcul.n_cible(0)
    with pytest.raises(ValueError):
        calcul.erreur_95(-1)


@pytest.mark.parametrize(("mbps", "erreur"), [(1, 26), (10, 8), (100, 2.6), (1000, 0.8)])
def test_tableau_3_2(mbps, erreur):
    """1:4096, paquets de 1000 o, 30 min : erreur ±26 %, ±8 %, ±2,6 %, ±0,8 %."""
    n = calcul.paquets(mbps * 1e6, 1800, 1000) / 4096
    assert calcul.erreur_95(n) == pytest.approx(erreur, rel=0.05)


def test_probabilite_detection():
    assert calcul.probabilite_detection(1) == pytest.approx(1 - math.exp(-1))  # 37 % de ne pas voir
    assert calcul.probabilite_detection(5) > 0.99
    assert calcul.probabilite_detection(0) == 0
    with pytest.raises(ValueError):
        calcul.probabilite_detection(-1)


def test_parametres_invalides():
    with pytest.raises(ValueError):
        calcul.paquets(1e6, 0, 1500)
    with pytest.raises(ValueError):
        calcul.paquets(-1, 300, 1500)
    with pytest.raises(ValueError):
        calcul.paquets(1e6, 300, 0)
    with pytest.raises(ValueError):
        calcul.sampling_rate(1e6, plancher=1.5)


def test_adaptations_4_3():
    """§4.3 : les règles d'adaptation, arrondies à la puissance de 2 inférieure."""
    base = calcul.sampling_rate(1e9)  # 50 Mbit/s × 300 s / (8 × 1500 × 1537) = 813 -> 512
    assert calcul.sampling_rate(1e9, fenetre_s=1800) == 4096  # 813 × 6 = 4879 -> 4096
    assert calcul.sampling_rate(1e9, fenetre_s=60) == 128  # 813 ÷ 5 = 162 -> 128
    assert calcul.sampling_rate(1e9, plancher=0.10) == 2 * base
    assert calcul.sampling_rate(1e9, erreur_pct=10) == 2048  # 813 × 4 = 3247 -> 2048


# Tableau 12.1 : classe, DSCP, TOS.
TABLEAU_12_1 = [
    ("BE", 0, 0x00),
    ("BF", 0, 0x00),
    ("CS1", 8, 0x20),
    ("AF11", 10, 0x28),
    ("D3", 10, 0x28),
    ("AF21", 18, 0x48),
    ("D2", 18, 0x48),
    ("AF31", 26, 0x68),
    ("D1", 26, 0x68),
    ("AF41", 34, 0x88),
    ("CS5", 40, 0xA0),
    ("DSCP45", 45, 0xB4),
    ("EF", 46, 0xB8),
    ("CS6", 48, 0xC0),
]


@pytest.mark.parametrize(("classe", "valeur", "octet"), TABLEAU_12_1)
def test_tableau_12_1(classe, valeur, octet):
    assert calcul.dscp(classe) == valeur
    assert calcul.dscp(classe.lower()) == valeur
    assert calcul.tos(valeur) == octet
    assert calcul.dscp_de_tos(octet) == valeur


def test_dscp_tos_limites():
    assert calcul.dscp("46") == 46
    assert calcul.dscp(63) == 63
    assert calcul.tos(46, ecn=3) == 0xBB
    assert calcul.dscp_de_tos(0xBB) == 46  # ECN ignoré
    for invalide in ("64", "X", "-1"):
        with pytest.raises(ValueError):
            calcul.dscp(invalide)
    with pytest.raises(ValueError):
        calcul.tos(64)
    with pytest.raises(ValueError):
        calcul.tos(0, ecn=4)
    with pytest.raises(ValueError):
        calcul.dscp_de_tos(256)


# Tableau 7.3 : paquet IP, -l UDP, trame Ethernet sans FCS.
@pytest.mark.parametrize(
    ("ip", "charge", "trame"),
    [(100, 72, 114), (200, 172, 214), (500, 472, 514), (1000, 972, 1014), (1500, 1472, 1514)],
)
def test_tableau_7_3(ip, charge, trame):
    assert calcul.charge_udp(ip) == charge
    assert calcul.taille_ip_de_charge(charge) == ip
    assert calcul.trame_ethernet(ip) == trame
    assert calcul.trame_ethernet(ip, vlan=True) == trame + 4
    assert calcul.trame_ethernet(ip, vlan=True, fcs=True) == trame + 8
    assert not calcul.fragmente(ip)


def test_tailles_limites():
    assert calcul.fragmente(calcul.taille_ip_de_charge(1473))
    with pytest.raises(ValueError):
        calcul.charge_udp(28)
    with pytest.raises(ValueError):
        calcul.taille_ip_de_charge(0)


# #10 : erreur sur les octets avec des tailles de paquets dispersées.
def test_cv_tailles():
    assert calcul.cv_tailles([1000, 1000, 1000]) == 0
    assert calcul.cv_tailles([64, 576, 1500]) == pytest.approx(0.833, abs=1e-3)
    # pondération : 2 paquets de 64 o pour 1 de 1500 o, comme la liste développée
    assert calcul.cv_tailles([64, 1500], [2, 1]) == pytest.approx(calcul.cv_tailles([64, 64, 1500]))
    for tailles, poids in (([], None), ([64], [1, 2]), ([64], [-1]), ([64], [0]), ([0, 64], None)):
        with pytest.raises(ValueError):
            calcul.cv_tailles(tailles, poids)


def test_erreur_95_octets():
    assert calcul.erreur_95_octets(1537, 0) == pytest.approx(calcul.erreur_95(1537))
    # mélange égal 64/576/1500 o : erreur × 1,30, la marge haute de 20-30 % du protocole
    cv = calcul.cv_tailles([64, 576, 1500])
    assert calcul.erreur_95_octets(1537, cv) / calcul.erreur_95(1537) == pytest.approx(
        1.30, abs=0.005
    )
    assert calcul.erreur_95_octets(0, cv) == math.inf
    with pytest.raises(ValueError):
        calcul.erreur_95_octets(1537, -0.1)


def test_n_cible_octets():
    assert calcul.n_cible_octets() == calcul.n_cible() == 1537
    assert calcul.n_cible_octets(5, calcul.cv_tailles([64, 576, 1500])) == 2603
    assert calcul.n_cible_octets(5, math.sqrt(0.44)) == 2213  # marge de 20 % sur l'erreur
    # l'erreur visée est atteinte avec n cible échantillons
    for cv in (0.3, 0.83, 1.5):
        assert calcul.erreur_95_octets(calcul.n_cible_octets(5, cv), cv) <= 5
    for erreur, cv in ((0, 0), (5, -1)):
        with pytest.raises(ValueError):
            calcul.n_cible_octets(erreur, cv)


def test_sampling_avec_cv():
    cv = calcul.cv_tailles([64, 576, 1500])
    ligne = calcul.dimensionner(10e6, cv=cv)
    assert ligne.rate == 4  # 12 500 paquets / 2 603 = 4,8 -> 1:4 (1:8 sans CV)
    assert ligne.erreur_pct is not None and ligne.erreur_pct <= 5
    assert calcul.sampling_rate(1e9, cv=cv) == 256  # 512 sans CV


def test_cli_cv(capsys):
    assert cli.main(["sampling", "10M", "--cv", "0.833"]) == 0
    sortie = capsys.readouterr().out.splitlines()
    assert sortie[0] == "n cible : 2603 échantillons (erreur 5 % à 95 %, CV 0.833)"
    assert sortie[3].startswith("| 10 | 500 | 1:4 | 3125 | 4,6 % |")
    assert cli.main(["sampling", "10M", "--cv", "-1"]) == 2
