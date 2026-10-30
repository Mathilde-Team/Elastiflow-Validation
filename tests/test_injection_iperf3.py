# SPDX-License-Identifier: GPL-3.0-or-later
"""Variante iperf3 de l'injection (#32) : --cport, un serveur par processus, lecture en plage."""

from __future__ import annotations

import pytest
from test_injection import FauxSysteme, _execs, _lire

from elastiflow_validation import cli, injection, lecture, plan

IPERF3 = injection.Banc("srv", "cli", 5001, "iperf3")


def test_commande_iperf3_udp_tcp():
    (a2,) = _execs("A2")[:1]
    assert injection.commande_iperf(a2, 40004, IPERF3) == [
        "iperf3", "-c", "srv", "-u", "-l", "972", "-b", "500k", "-p", "5001", "-t", "1800",
        "-i", "10", "-B", "cli", "--cport", "40004", "-S", "0x00", "--pacing-timer", "100",
        "-J", "--get-server-output",
    ]  # fmt: skip
    (c1,) = _execs("C1")
    cmd = injection.commande_iperf(c1, c1.test.ports[0], IPERF3, 5009)
    assert cmd[0] == "iperf3" and "-u" not in cmd and "-b" not in cmd
    assert "--pacing-timer" not in cmd and cmd[cmd.index("-p") + 1] == "5009"
    assert cmd[cmd.index("-n") + 1] == "1000000000"


def test_commande_iperf3_udp_max_et_outil_inconnu():
    # UDP sans débit : iperf3 le limiterait à 1 Mbit/s, d'où -b 0
    t = next(t for t in plan.plan_par_defaut() if t.protocole == "udp")
    e = plan.Execution(t, 1, None, None)
    cmd = injection.commande_iperf(e, t.ports[0], IPERF3)
    assert cmd[cmd.index("-b") + 1] == "0"
    with pytest.raises(ValueError, match="inconnu"):
        injection.commande_iperf(e, t.ports[0], injection.Banc(outil="iperf4"))


def test_ports_destination_un_serveur_par_processus():
    (c3,) = injection.lancements(_execs("C3"))
    assert set(injection.ports_destination(c3, injection.Banc()).values()) == {50001}
    ports = injection.ports_destination(c3, IPERF3)
    assert sorted(ports.values()) == list(range(5001, 5009))
    (r1,) = injection.lancements(_execs("R1"))  # port RTP propre au test
    assert list(injection.ports_destination(r1, IPERF3).values()) == [20000]
    assert injection.serveurs_iperf3([c3], injection.Banc()) == ""
    assert injection.serveurs_iperf3([], IPERF3) == ""
    assert injection.serveurs_iperf3([c3, r1], IPERF3) == (
        "for p in 5001 5002 5003 5004 5005 5006 5007 5008 20000; do iperf3 -s -p $p -D; done"
    )


def test_injecter_iperf3(tmp_path):
    sortie = tmp_path / "r.csv"
    systeme = FauxSysteme()
    messages: list[str] = []
    execs = _execs("A2", "C3", "Q3-EF", "Q3-AF41")
    n = injection.injecter(
        execs, sortie, systeme, banc=IPERF3, lab=True, pause_s=0, afficher=messages.append
    )
    assert (n, len(_lire(sortie))) == (3 + 1 + 3, 3 + 1 + 3 * 2)  # lancements, lignes
    assert any(m.startswith("Serveurs iperf3") and "5001 5002" in m for m in messages)
    assert all(cmd[0] == "iperf3" for cmd, _ in systeme.lances)
    lignes = _lire(sortie)
    assert {r["port_destination"] for r in lignes if r["id"] == "A2"} == {"5001"}
    assert next(r for r in lignes if r["id"] == "C3")["port_destination"] == "5001-5008"
    q3 = [r for r in lignes if r["groupe"] == "Q3" and r["repetition"] == "1"]
    assert sorted(r["port_destination"] for r in q3) == ["5001", "5002"]
    # dry-run : mêmes ports, mêmes serveurs annoncés
    messages.clear()
    injection.injecter(execs[:1], sortie, FauxSysteme(), banc=IPERF3, dry_run=True,
                       afficher=messages.append)  # fmt: skip
    assert any("iperf3 -c srv" in m and "--cport 40004" in m for m in messages)


def test_lecture_port_destination_en_plage():
    ligne = {"id": "C3", "repetition": "1", "ports": "40019-40026", "debut": "1000", "fin": "1600",
             "protocole": "tcp", "port_destination": "5001-5008"}  # fmt: skip
    corps = lecture.requete_test(ligne, lecture.CHAMPS_DEFAUT, IPERF3)
    filtres = corps["query"]["bool"]["filter"]
    assert {"range": {"destination.port": {"gte": 5001, "lte": 5008}}} in filtres


def test_cli_injecter_iperf3(capsys):
    args = ["injecter", "--lien", "10M", "--dry-run", "--test", "C3", "--outil", "iperf3"]
    assert cli.main(args) == 0
    sortie = capsys.readouterr().out
    assert "iperf3 -c 10.20.0.2" in sortie and "--cport 40026 -S" in sortie
    assert "-p 50008" in sortie and "iperf3 -s -p $p -D" in sortie
    with pytest.raises(SystemExit):
        cli.main(["injecter", "--lien", "10M", "--outil", "iperf4"])
