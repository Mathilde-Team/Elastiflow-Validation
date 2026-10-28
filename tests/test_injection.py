# SPDX-License-Identifier: GPL-3.0-or-later
"""Injection (#4) : commandes iperf2, compteurs nft, resultats.csv, avec un système simulé."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from elastiflow_validation import cli, injection, plan

LIEN = 10e6


class FauxProcessus:
    def __init__(self, code: int) -> None:
        self.code = code

    def wait(self) -> int:
        return self.code


class FauxSysteme:
    """Enregistre les commandes ; chaque processus iperf « émet » 10 paquets de 1000 octets."""

    def __init__(self, tables: str = "", code_iperf: int = 0, perdre: str | None = None) -> None:
        self.tables = tables
        self.code_iperf = code_iperf
        self.perdre = perdre  # commentaire de règle absent du relevé
        self.commandes: list[list[str]] = []
        self.lances: list[tuple[list[str], Path]] = []
        self.confirmations: list[str] = []
        self.pauses: list[float] = []
        self.horloge = 1_000
        self.regles: dict[str, int] = {}

    def executer(self, commande):
        self.commandes.append(list(commande))
        if commande[:3] == ["nft", "list", "tables"]:
            return self.tables
        if commande[:3] == ["nft", "add", "rule"]:
            self.regles[commande[-1].strip('"')] = len(self.regles) + 10
        if commande[:3] == ["nft", "-j", "list"]:
            regles = [
                {
                    "rule": {
                        "comment": c,
                        "handle": h,
                        "expr": [{"match": {}}, {"counter": {"packets": 10, "bytes": 10_000}}],
                    }
                }
                for c, h in self.regles.items()
                if c != self.perdre
            ]
            return json.dumps({"nftables": [{"metainfo": {}}, {"chain": {}}, *regles]})
        return ""

    def lancer(self, commande, journal):
        self.lances.append((list(commande), journal))
        self.horloge += 60
        return FauxProcessus(self.code_iperf)

    def maintenant(self):
        return self.horloge

    def dormir(self, secondes):
        self.pauses.append(secondes)

    def confirmer(self, message):
        self.confirmations.append(message)


def _execs(*ids: str, cir: dict | None = None) -> list[plan.Execution]:
    tests = [t for t in plan.plan_par_defaut() if t.id in ids]
    return plan.executions(tests, LIEN, cir or {})


def _lire(chemin: Path) -> list[dict[str, str]]:
    with chemin.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_commande_iperf_udp_tcp_volume():
    banc = injection.Banc("srv", "cli", 5001)
    (a2,) = _execs("A2")[:1]
    assert injection.commande_iperf(a2, 40004, banc) == [
        "iperf", "-c", "srv", "-u", "-l", "972", "-b", "500k", "-p", "5001", "-t", "1800",
        "-i", "10", "-e", "-B", "cli:40004", "-S", "0x00",
    ]  # fmt: skip
    (c1,) = _execs("C1")
    cmd = injection.commande_iperf(c1, c1.test.ports[0], banc)
    assert "-u" not in cmd and "-b" not in cmd and cmd[cmd.index("-n") + 1] == "1000000000"
    (q1,) = _execs("Q1-1")
    assert injection.commande_iperf(q1, 40030, banc)[-1] == "0xb8"
    (r1,) = _execs("R1")  # port RTP propre au test (#16)
    cmd = injection.commande_iperf(r1, r1.test.ports[0], banc)
    assert cmd[cmd.index("-p") + 1] == "20000" and cmd[-1] == "0x00"


def test_commande_iperf_refus():
    g1 = next(t for t in plan.plan_par_defaut() if t.id == "G1")
    with pytest.raises(ValueError, match="manuel"):
        injection.commande_iperf(plan.Execution(g1, 1, None, None), 1, injection.Banc())
    (q2,) = _execs("Q2-EF-50")
    with pytest.raises(ValueError, match="--cir"):
        injection.commande_iperf(q2, 40037, injection.Banc())


def test_lancements_groupes_et_repetitions():
    execs = plan.executions(plan.plan_par_defaut(), LIEN, {"EF": 2e6})
    lots = injection.lancements(execs)
    cles = [(lot.cle, lot.repetition) for lot in lots]
    assert cles[:4] == [("P5", 1), ("P6", 1), ("A1", 1), ("A2", 1)]
    q3 = [lot for lot in lots if lot.cle == "Q3"]
    assert [lot.repetition for lot in q3] == [1, 2, 3] and len(q3[0].executions) == 6
    assert all(lot.cle not in {"G1", "Q6"} for lot in lots)  # tests manuels écartés
    assert lots[0].sampling == 1 and q3[0].sampling is None
    g1 = next(t for t in plan.plan_par_defaut() if t.id == "G1")
    assert injection.lancements([plan.Execution(g1, 1, None, None)]) == []


def test_lire_compteurs():
    sortie = json.dumps(
        {
            "nftables": [
                {"metainfo": {}},
                "texte",
                {
                    "rule": {
                        "comment": "efv A2#1 40004",
                        "handle": 7,
                        "expr": [{"counter": {"packets": 3, "bytes": 30}}],
                    }
                },
                {
                    "rule": {
                        "comment": "autre",
                        "handle": 8,
                        "expr": [{"counter": {"packets": 1, "bytes": 1}}],
                    }
                },
                {"rule": {"comment": "efv sans compteur", "handle": 9, "expr": []}},
                {"rule": {"handle": 10}},
            ]
        }
    )
    assert injection.lire_compteurs(sortie) == {"efv A2#1 40004": (7, 3, 30)}
    for illisible in ("pas du json", "{}", "[]"):
        with pytest.raises(ValueError, match="nft illisible"):
            injection.lire_compteurs(illisible)


def test_injecter_ecrit_resultats(tmp_path):
    sortie = tmp_path / "resultats.csv"
    systeme = FauxSysteme()
    messages: list[str] = []
    execs = _execs("P5", "A2", "C3", "D1")
    n = injection.injecter(execs, sortie, systeme, pause_s=300, afficher=messages.append)
    assert n == 6  # P5, A2 ×3, C3, D1
    # table et chaîne créées, une règle par port, supprimée après lecture
    assert ["nft", "add", "table", "inet", "t"] in systeme.commandes
    regles = [c for c in systeme.commandes if c[:3] == ["nft", "add", "rule"]]
    assert len(regles) == 1 + 3 + 8 + 1
    assert len([c for c in systeme.commandes if c[:3] == ["nft", "delete", "rule"]]) == len(regles)
    assert len(systeme.lances) == len(regles)
    assert systeme.lances[0][1] == tmp_path / "log_P5_1_40001.txt"
    # pauses de 5 min entre lancements, 2 s avant chaque relevé
    assert systeme.pauses.count(300) == 5 and systeme.pauses.count(injection.ATTENTE_NFT_S) == 6
    # sampling 1:1 pour P5 puis retour au nominal, préalable netem de D1
    assert systeme.confirmations[0].endswith("1:1")
    assert "nominal" in systeme.confirmations[1]
    assert (
        systeme.confirmations[2] == "[D1] Préalable : tc qdisc add dev eth0 root netem duplicate 5%"
    )
    lignes = _lire(sortie)
    assert list(lignes[0]) == list(injection.COLONNES)
    # clé de lecture dans ElastiFlow (efv lire, #5)
    assert {(r["client"], r["serveur"], r["port_destination"]) for r in lignes} == {
        ("10.10.0.2", "10.20.0.2", "50001")
    }
    assert [(r["id"], r["repetition"]) for r in lignes] == [
        ("P5", "1"), ("A2", "1"), ("A2", "2"), ("A2", "3"), ("C3", "1"), ("D1", "1")
    ]  # fmt: skip
    c3 = lignes[4]
    assert (c3["ports"], c3["paquets"], c3["octets"], c3["debit"]) == (
        "40019-40026",
        "80",
        "80000",
        "max",
    )
    assert int(c3["fin"]) - int(c3["debut"]) == 8 * 60
    assert lignes[0]["sampling"] == "1:1" and lignes[1]["sampling"] == "nominal"
    assert any(m.startswith("[1/6] P5") for m in messages)
    # un second passage complète le fichier sans répéter l'en-tête ; table déjà présente
    systeme2 = FauxSysteme(tables="table inet t\n")
    injection.injecter(_execs("B1"), sortie, systeme2, afficher=messages.append)
    assert ["nft", "add", "table", "inet", "t"] not in systeme2.commandes
    assert [r["id"] for r in _lire(sortie)][-1] == "B1" and len(_lire(sortie)) == 7


def test_injecter_groupe_lab(tmp_path):
    sortie = tmp_path / "r.csv"
    messages: list[str] = []
    execs = _execs("Q3-EF", "Q3-AF41", "A2")
    assert injection.injecter(execs, sortie, FauxSysteme(), afficher=messages.append) == 3
    assert "Tests de laboratoire écartés (--lab pour les inclure) : Q3-AF41, Q3-EF" in messages
    systeme = FauxSysteme()
    assert (
        injection.injecter(execs, tmp_path / "lab.csv", systeme, lab=True, afficher=messages.append)
        == 6
    )
    lignes = _lire(tmp_path / "lab.csv")
    q3 = [r for r in lignes if r["groupe"] == "Q3" and r["repetition"] == "1"]
    assert len(q3) == 2 and q3[0]["debut"] == q3[1]["debut"] and q3[0]["fin"] == q3[1]["fin"]


def test_injecter_dry_run(tmp_path):
    sortie = tmp_path / "r.csv"
    messages: list[str] = []
    systeme = FauxSysteme()
    n = injection.injecter(
        _execs("P6", "D1", "C3"), sortie, systeme, dry_run=True, afficher=messages.append
    )
    assert n == 3 and not sortie.exists() and systeme.commandes == [] and systeme.lances == []
    texte = "\n".join(messages)
    assert "# sampling de l'exporteur : 1:8" in texte
    assert "# préalable : tc qdisc add dev eth0 root netem duplicate 5%" in texte
    assert texte.count("iperf -c") == 1 + 1 + 8 and texte.count("wait") == 3


def test_injecter_cir_manquant_avant_tout(tmp_path):
    systeme = FauxSysteme()
    with pytest.raises(ValueError, match="--cir"):
        injection.injecter(_execs("A2", "Q2-EF-50"), tmp_path / "r.csv", systeme, afficher=print)
    assert systeme.commandes == []


@pytest.mark.parametrize(("code", "perdre"), [(1, None), (0, "efv A2#1 40004")])
def test_injecter_echec_iperf_ou_compteur(tmp_path, code, perdre):
    sortie = tmp_path / "r.csv"
    systeme = FauxSysteme(code_iperf=code, perdre=perdre)
    with pytest.raises(RuntimeError, match="A2#1"):
        injection.injecter(_execs("A2"), sortie, systeme, afficher=print)
    assert _lire(sortie) == []  # en-tête seul


def test_systeme_reel(tmp_path, monkeypatch):
    s = injection.SystemeReel(saisie=lambda message: "")
    assert s.executer(["python3", "-c", "print('ok')"]) == "ok\n"
    with pytest.raises(RuntimeError, match="boom"):
        s.executer(["python3", "-c", "import sys; sys.exit('boom')"])
    with pytest.raises(RuntimeError):
        s.executer(["commande-absente-efv"])
    journal = tmp_path / "log.txt"
    assert s.lancer(["python3", "-c", "print('iperf')"], journal).wait() == 0
    s.fermer()
    assert journal.read_text(encoding="utf-8") == "iperf\n"
    assert s.maintenant() > 1_700_000_000
    dormi: list[float] = []
    monkeypatch.setattr(injection.time, "sleep", dormi.append)
    s.dormir(2)
    assert dormi == [2]
    vus: list[str] = []
    injection.SystemeReel(saisie=vus.append).confirmer("Régler")
    assert vus == ["Régler\nEntrée quand c'est fait… "]


def test_cli_injecter_dry_run(capsys, tmp_path):
    args = ["injecter", "--lien", "10M", "--dry-run", "--test", "A2", "--test", "Q3", "--lab"]
    assert cli.main([*args, "--serveur", "s", "--client", "c", "--port-destination", "6000"]) == 0
    sortie = capsys.readouterr().out
    assert "iperf -c s -u -l 972 -b 500k -p 6000 -t 1800" in sortie and "-B c:40004" in sortie
    assert "# Q3 répétition 3" in sortie and "6 lancement(s) simulé(s)" in sortie
    assert cli.main(["injecter", "--lien", "10M", "--dry-run", "--profil", "minimal", "--lab"] + [
        f"--cir={c}=1M" for c in ("EF", "AF41", "D1", "BF")
    ]) == 0  # fmt: skip


def test_cli_injecter_erreurs(tmp_path, monkeypatch):
    base = ["injecter", "--lien", "10M", "--sortie", str(tmp_path / "r.csv")]
    assert cli.main([*base, "--test", "ZZ"]) == 2
    assert cli.main([*base, "--test", "Q2-EF-50"]) == 2  # CIR manquant
    assert cli.main([*base, "--plan", str(tmp_path / "absent.yaml")]) == 2

    def echec(commande, **_):
        raise subprocess.CalledProcessError(1, commande, stderr="nft: permission refusée")

    monkeypatch.setattr(injection.subprocess, "run", echec)
    assert cli.main([*base, "--test", "A1"]) == 1


def test_cli_injecter_reel(tmp_path, monkeypatch, capsys):
    systeme = FauxSysteme()
    monkeypatch.setattr(injection, "SystemeReel", lambda: _AvecFermer(systeme))
    sortie = tmp_path / "r.csv"
    assert (
        cli.main(
            [
                "injecter",
                "--lien",
                "10M",
                "--test",
                "A*-4096",
                "--pause",
                "1",
                "--sortie",
                str(sortie),
            ]
        )
        == 0
    )
    assert [r["id"] for r in _lire(sortie)] == ["A1-4096", "A2-4096", "A3-4096"]
    assert systeme.pauses.count(1) == 2 and "3 lancement(s) terminé(s)" in capsys.readouterr().out


class _AvecFermer(FauxSysteme):
    def __init__(self, interne: FauxSysteme) -> None:
        self.__dict__ = interne.__dict__

    def fermer(self) -> None:
        self.ferme = True
