# SPDX-License-Identifier: GPL-3.0-or-later
"""Série G outillée (#33) : G2 ICMP (ping), G5 fragmentation, clé de lecture sans ports."""

from __future__ import annotations

import pytest
from test_injection import FauxSysteme, _execs, _lire

from elastiflow_validation import cli, injection, lecture, plan


def _plan(texte: str) -> list[plan.Test]:
    return plan.charger_plan("version: 1\ntests:\n" + texte)


def test_plan_g2_icmp_et_g5_cle_ip():
    tests = {t.id: t for t in plan.plan_par_defaut()}
    g2, g5 = tests["G2"], tests["G5"]
    assert (g2.protocole, g2.ports, g2.cle, g2.taille, g2.sampling) == ("icmp", (), "ip", 1028, 8)
    assert g2.debit == plan.Debit("absolu", 1028 * 8 / plan.INTERVALLE_PING_S) and g2.manuel is None
    assert (g5.protocole, g5.taille, g5.cle, len(g5.ports)) == ("udp", 3028, "ip", 1)
    # fin de fichier, avant la série S
    assert max(p for t in tests.values() if t.serie != "S" for p in t.ports) == g5.ports[0]
    assert {t.id for t in tests.values() if t.serie == "G" and t.manuel} == {"G1", "G3", "G4", "G6"}
    (e,) = plan.executions([g2], 10e6)
    assert e.charge_udp == 1000 and e.iperf_b == "41.12k"
    # un test ICMP ne consomme pas de port : le suivant garde le sien
    a, b = _plan(
        "  - {id: I, protocole: icmp, taille: 100, duree: 10}\n"
        "  - {id: U, protocole: udp, debit: 1M, taille: 100, duree: 10}\n"
    )
    assert a.ports == () and b.ports == (plan.PORT_BASE + 1,) and b.cle == "ports"


@pytest.mark.parametrize(
    ("entree", "motif"),
    [
        ("{id: I, protocole: icmp, taille: 100, duree: 10, debit: 1M}", "sans objet"),
        ("{id: I, protocole: icmp, taille: 100, duree: 10, cle: ip}", "sans objet"),
        ("{id: I, protocole: icmp, duree: 10}", "obligatoires"),
        ("{id: I, protocole: icmp, taille: 100}", "obligatoires"),
        ("{id: U, protocole: udp, debit: 1M, taille: 100, duree: 10, cle: mac}", "cle"),
        ("{id: U, protocole: udp, debit: 1M, taille: 100, duree: 10, sampling: 0}", "sampling"),
    ],
)
def test_plan_icmp_cle_invalides(entree, motif):
    with pytest.raises(ValueError, match=motif):
        _plan(f"  - {entree}\n")


def test_commande_ping_et_regle_nft():
    (g2,) = _execs("G2")
    banc = injection.Banc("srv", "cli")
    assert injection.commande_iperf(g2, 0, banc) == [
        "ping", "-c", "1500", "-i", "0.2", "-s", "1000", "-t", "64", "-Q", "0x00", "-I", "cli",
        "srv",
    ]  # fmt: skip
    assert injection._regle_nft(g2, 0)[6:10] == ["icmp", "type", "echo-request", "counter"]


def test_injecter_g2_g5(tmp_path):
    sortie = tmp_path / "r.csv"
    systeme = FauxSysteme()
    messages: list[str] = []
    assert injection.injecter(_execs("G2", "G5"), sortie, systeme, afficher=messages.append) == 2
    assert systeme.lances[0][0][0] == "ping" and systeme.lances[0][1].name == "log_G2_1_0.txt"
    assert systeme.lances[1][0][:2] == ["iperf", "-c"] and "3000" in systeme.lances[1][0]
    g2, g5 = _lire(sortie)
    assert (g2["protocole"], g2["ports"], g2["port_destination"], g2["paquets"]) == (
        "icmp", "", "", "10"
    )  # fmt: skip
    assert (g5["ports"], g5["port_destination"], g5["sampling"]) == ("", "", "1:8")
    messages.clear()
    injection.injecter(_execs("G2"), sortie, FauxSysteme(), dry_run=True, afficher=messages.append)
    texte = "\n".join(messages)
    assert "icmp type echo-request counter" in texte and "ping -c 1500" in texte


def test_lecture_sans_ports():
    ligne = {"id": "G2", "repetition": "1", "protocole": "icmp", "ports": "", "debut": "1000",
             "fin": "1300", "port_destination": ""}  # fmt: skip
    filtres = lecture.requete_test(ligne, lecture.CHAMPS_DEFAUT, injection.Banc())["query"]
    filtres = filtres["bool"]["filter"]
    assert not any("source.port" in f.get("range", {}) for f in filtres)
    assert not any("destination.port" in f.get("term", {}) for f in filtres)
    assert {"term": {"network.transport": "icmp"}} in filtres


def test_cli_plan_g(capsys):
    assert cli.main(["plan", "--lien", "10M", "--format", "csv"]) == 0
    lignes = {x.split(",")[0]: x for x in capsys.readouterr().out.splitlines()}
    assert ",(lu sans ports)," in lignes["G2"] and "(lu sans ports)" in lignes["G5"]
    assert ",icmp," in lignes["G2"]
