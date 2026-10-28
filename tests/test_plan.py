"""Plan de tests YAML (#3) : chargement validé, ports, conversion en débits absolus, efv plan."""

from __future__ import annotations

import csv
import io

import pytest

from elastiflow_validation import cli, plan


@pytest.fixture(scope="module")
def defaut() -> list[plan.Test]:
    return plan.plan_par_defaut()


def _par_id(tests: list[plan.Test]) -> dict[str, plan.Test]:
    return {t.id: t for t in tests}


def test_plan_par_defaut_couvre_le_protocole(defaut):
    ids = {t.id for t in defaut}
    attendus = {"P5", "P6", "C1", "C2", "C3", "D1", "D2", "E1", "Q4-EF", "Q4-BF", "Q6"}
    attendus |= {f"A{i}" for i in range(1, 7)} | {f"B{i}" for i in range(1, 6)}
    attendus |= {f"Q1-{i}" for i in range(1, 8)} | {f"Q5-{i}" for i in range(1, 4)}
    attendus |= {f"G{i}" for i in range(1, 7)} | {"A1-4096", "A2-4096", "A3-4096"}
    assert attendus <= ids
    t = _par_id(defaut)
    # répétitions du 7.8 : A2, A3 et A5 trois fois ; Q3 trois fois
    assert {i for i, x in t.items() if x.repetitions == 3} >= {"A2", "A3", "A5", "Q3-EF", "E1"}
    assert t["A1-4096"].sampling == 4096 and t["A2"].sampling is None and t["P5"].sampling == 1


def test_ports_uniques_40000_plus_n(defaut):
    ports = [p for t in defaut for p in t.ports]
    assert len(ports) == len(set(ports))
    assert ports[0] == 40001 and ports == list(range(40001, 40001 + len(ports)))
    t = _par_id(defaut)
    assert len(t["C3"].ports) == 8 and t["C3"].ports[-1] - t["C3"].ports[0] == 7
    assert t["G1"].ports == ()


def test_tableaux_du_protocole(defaut):
    t = _par_id(defaut)
    # B : même débit, tailles 100 à 1500 o (7.8)
    assert [t[f"B{i}"].taille for i in range(1, 6)] == [100, 200, 500, 1000, 1500]
    # Q1 : -S du 8.3
    tos = [t[f"Q1-{i}"].tos for i in range(1, 8)]
    assert tos == [0xB8, 0x88, 0x68, 0x48, 0x28, 0x00, 0xB4]
    assert [t[f"Q5-4{x}"].tos for x in "abc"] == [0x01, 0x02, 0x03]
    assert t["Q5-4a"].dscp == 0
    # Q3 : parts offertes du 8.3, ~130 % du lien
    q3 = [x for x in defaut if x.groupe == "Q3"]
    assert sum(x.debit.valeur for x in q3) == 130


def test_executions_lien_10m(defaut):
    execs = plan.executions(defaut, 10e6, {"EF": 2e6})
    e = {(x.test.id, x.repetition): x for x in execs}
    assert e[("A2", 1)].iperf_b == "500k" and e[("A2", 3)].debit_bps == 500e3
    assert e[("A2", 1)].charge_udp == 972
    assert e[("A4", 1)].iperf_b == "2.5M"
    assert e[("E1", 1)].iperf_b == "15M"
    assert e[("P5", 1)].iperf_b == "500k"
    assert e[("Q2-EF-150", 1)].iperf_b == "3M"
    assert e[("Q2-BF-150", 1)].debit_bps is None  # CIR de BF non fourni
    assert e[("C3", 1)].iperf_b is None and e[("C3", 1)].charge_udp is None
    assert all(x.test.manuel is None for x in execs)
    assert len([x for x in execs if x.test.id == "A2"]) == 3


def test_duree_totale():
    texte = """
version: 1
tests:
  - {id: X1, protocole: udp, debit: 5%, taille: 500, duree: 600}
  - {id: X2, groupe: G, protocole: udp, debit: 5%, taille: 500, duree: 60, repetitions: 2}
  - {id: X3, groupe: G, protocole: udp, debit: 5%, taille: 500, duree: 90, repetitions: 2}
  - {id: X4, protocole: tcp, debit: max, volume: 1G}
"""
    execs = plan.executions(plan.charger_plan(texte), 1e9)
    # X1 600 + G (rép. 1) 90 + G (rép. 2) 90 + X4 8 s à 1 Gbit/s, et 3 pauses de 300 s
    assert plan.duree_totale(execs, 1e9) == pytest.approx(600 + 90 + 90 + 8 + 3 * 300)
    assert plan.duree_totale([], 1e9) == 0


@pytest.mark.parametrize(
    ("bps", "texte"),
    [(500e3, "500k"), (2.5e6, "2.5M"), (1e9, "1G"), (800, "800"), (1234567, "1.23457M")],
)
def test_format_debit(bps, texte):
    assert plan.format_debit(bps) == texte


@pytest.mark.parametrize(
    ("entree", "message"),
    [
        ("[]", "version: 1"),
        ("version: 2\ntests: []", "version: 1"),
        ("version: 1\ntests: [{}]", "id"),
        ("version: 1\ntests: [{id: X, inconnu: 1}]", "champs inconnus"),
        ("version: 1\ntests: [{id: X, protocole: icmp}]", "protocole"),
        ("version: 1\ntests: [{id: X, protocole: udp}]", "débit manquant"),
        (
            "version: 1\ntests: [{id: X, protocole: udp, debit: vite, taille: 500, duree: 1}]",
            "débit illisible",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: udp, debit: 0%, taille: 500, duree: 1}]",
            "non positif",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: udp, debit: 5%, duree: 1}]",
            "taille IP obligatoire",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: udp, debit: max, taille: 500, duree: 1}]",
            "obligatoire en UDP",
        ),
        ("version: 1\ntests: [{id: X, protocole: udp, debit: 5%, taille: 20, duree: 1}]", "taille"),
        ("version: 1\ntests: [{id: X, protocole: tcp, debit: 5%}]", "durée ou un volume"),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, volume: 1G}]",
            "durée ou un volume",
        ),
        ("version: 1\ntests: [{id: X, protocole: tcp, debit: max, volume: 5%}]", "volume absolu"),
        ("version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 0}]", "duree"),
        ("version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: true}]", "duree"),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, sampling: 0}]",
            "sampling",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, sampling: x}]",
            "sampling",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, dscp: EF, tos: 1}]",
            "exclusifs",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, tos: 300}]",
            "tos hors plage",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, dscp: ZZ}]",
            "DSCP inconnu",
        ),
        ("version: 1\ntests: [{id: X, manuel: a}, {id: X, manuel: b}]", "en double"),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, processus: 25536}]",
            "65535",
        ),
        ("version: 1\ntests: [", "illisible"),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, attendu: trust:x}]",
            "liste",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, attendu: [trust]}]",
            "verdict attendu invalide",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, attendu: [x:CRIT]}]",
            "verdict attendu invalide",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, "
            "port_destination: -1}]",
            "port_destination",
        ),
        (
            "version: 1\ntests: [{id: X, protocole: tcp, debit: 5%, duree: 1, "
            "port_destination: 70000}]",
            "hors plage",
        ),
    ],
)
def test_plan_invalide(entree, message):
    with pytest.raises(ValueError, match=message):
        plan.charger_plan(entree)


def test_executions_lien_invalide(defaut):
    with pytest.raises(ValueError):
        plan.executions(defaut, 0)


def test_charger_plan_fichier(tmp_path):
    f = tmp_path / "plan.yaml"
    f.write_text(
        "version: 1\ntests:\n  - {id: T1, protocole: tcp, debit: 2M, duree: 10, dscp: af41}\n",
        "utf-8",
    )
    (t,) = plan.charger_plan(f)
    assert (t.ports, t.tos, t.debit) == ((40001,), 0x88, plan.Debit("absolu", 2e6))
    assert cli.main(["plan", "--lien", "10M", "--plan", str(f), "--format", "csv"]) == 0


def test_cli_plan_table(capsys):
    assert cli.main(["plan", "--lien", "10M", "--cir", "EF=2M"]) == 0
    sortie = capsys.readouterr().out
    assert "| A2 | 1 | A |  | udp | 500k | 972 | 0x00 | 1800s | 40004 | nominal |  |" in sortie
    assert "| C1 | 1 | C |  | tcp | max |  | 0x00 | -n 1G |" in sortie
    assert "| Q2-EF-150 | 1 | Q |  | udp | 3M | 472 | 0xb8 | 300s |" in sortie
    assert "| Q2-BF-150 | 1 | Q |  | udp | CIR ? |" in sortie
    assert (
        "| D1 | 1 | D |  | udp | 1M | 972 | 0x00 | 600s | 40027 | nominal |  | "
        "tc qdisc add dev eth0" in sortie
    )
    assert "| E1 | 1 | E |  | udp | 15M | 1472 | 0x00 | 60s |" in sortie
    assert "| 1:8 |  | lab |" in sortie
    assert "Tests manuels (hors iperf) : Q6, G1, G2, G3, G4, G5, G6" in sortie
    assert "préciser --cir" in sortie


def test_cli_plan_csv_complet(capsys):
    assert (
        cli.main(
            ["plan", "--lien", "1G", "--format", "csv"]
            + [f"--cir={c}=10M" for c in ("EF", "AF41", "D1", "BF")]
        )
        == 0
    )
    lignes = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert lignes[0][:3] == ["id", "rep", "serie"]
    assert all(r[5] != "CIR ?" for r in lignes[1:])
    assert [
        "C3",
        "1",
        "C",
        "",
        "tcp",
        "max",
        "",
        "0x00",
        "600s",
        "40019-40026",
        "nominal",
        "",
        "",
    ] in lignes


def test_cli_plan_sans_cir_affichage_complet(capsys):
    assert (
        cli.main(["plan", "--lien", "10M"] + [f"--cir={c}=1M" for c in ("EF", "AF41", "D1", "BF")])
        == 0
    )
    assert "préciser --cir" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "args", [["--cir", "EF"], ["--cir", "=2M"], ["--cir", "ZZ=2M"], ["--cir", "EF=x"]]
)
def test_cli_plan_cir_invalide(args):
    with pytest.raises(SystemExit):
        cli.main(["plan", "--lien", "10M", *args])


def test_cli_plan_fichier_invalide(tmp_path):
    assert cli.main(["plan", "--lien", "10M", "--plan", str(tmp_path / "absent.yaml")]) == 2


# --- Profils (#11) ---


def test_profil_minimal(defaut):
    minimal = plan.plan_par_defaut("minimal")
    ids = [t.id for t in minimal]
    assert ids[:6] == ["P5", "P6", "A2", "A3", "B1", "B5"]
    assert {f"Q1-{i}" for i in range(1, 8)} <= set(ids) and {"G1", "G6"} <= set(ids)
    assert len([i for i in ids if i.startswith("Q2-")]) == 12
    assert {t.id for t in defaut if t.groupe == "Q3"} <= set(ids)
    assert not {"A1", "A2-4096", "C1", "D1", "E1", "Q4-EF", "Q5-1", "G4", "G5"} & set(ids)
    # ports du plan complet, inchangés
    complet = _par_id(defaut)
    assert all(t.ports == complet[t.id].ports for t in minimal)
    # environ une journée d'injection
    heures = plan.duree_totale(plan.executions(minimal, 10e6), 10e6) / 3600
    assert 6 < heures < 10


_PROFILS = """
version: 1
tests:
  - {id: X1, protocole: udp, debit: 5%, taille: 500, duree: 60}
  - {id: X2, groupe: G, protocole: udp, debit: 5%, taille: 500, duree: 60}
  - {id: Y1, groupe: G, protocole: udp, debit: 5%, taille: 500, duree: 60}
"""


def test_profil_selecteurs_et_ordre():
    texte = _PROFILS + "profils: {p: [Y1, X*], g: [G]}\n"
    assert [t.id for t in plan.charger_plan(texte, "p")] == ["X1", "X2", "Y1"]
    assert [(t.id, t.ports) for t in plan.charger_plan(texte, "g")] == [
        ("X2", (40002,)),
        ("Y1", (40003,)),
    ]
    assert len(plan.charger_plan(texte)) == 3


@pytest.mark.parametrize(
    ("suffixe", "message"),
    [
        ("", "profil inconnu « p » \\(profils : aucun\\)"),
        ("profils: {q: [X1]}\n", "profil inconnu « p » \\(profils : q\\)"),
        ("profils: [X1]\n", "listes de sélecteurs"),
        ("profils: {p: X1}\n", "listes de sélecteurs"),
        ("profils: {p: [Z9]}\n", "« Z9 » ne désigne aucun test"),
        ("profils: {p: [1]}\n", "sélecteur invalide"),
        ("profils: {p: ['']}\n", "sélecteur invalide"),
    ],
)
def test_profil_invalide(suffixe, message):
    with pytest.raises(ValueError, match=message):
        plan.charger_plan(_PROFILS + suffixe, "p")


def test_cli_plan_profil(capsys, tmp_path):
    assert cli.main(["plan", "--lien", "10M", "--profil", "minimal"]) == 0
    sortie = capsys.readouterr().out
    assert "| A2 | 1 | A |" in sortie and "| C1 |" not in sortie
    assert (
        "Profil minimal : un verdict « Validé » sur ce profil ne vaut pas validation complète"
        in sortie
    )
    assert "Tests manuels (hors iperf) : G1, G6" in sortie
    assert cli.main(["plan", "--lien", "10M", "--profil", "inconnu"]) == 2
    f = tmp_path / "plan.yaml"
    f.write_text(_PROFILS + "profils: {p: [X1]}\n", "utf-8")
    assert (
        cli.main(["plan", "--lien", "10M", "--plan", str(f), "--profil", "p", "--format", "csv"])
        == 0
    )
    assert capsys.readouterr().out.count("\n") == 2
    assert cli.main(["plan", "--lien", "10M", "--plan", str(f), "--profil", "p"]) == 0
    assert "Tests manuels" not in capsys.readouterr().out


def test_serie_r_recette(defaut):
    """#16 : défauts connus pour la recette de qos-audit, verdicts attendus, ports inchangés."""
    t = _par_id(defaut)
    recette = plan.plan_par_defaut("recette")
    assert [x.id for x in recette] == [f"R{i}" for i in range(1, 7)]
    assert t["R1"].attendu == (("marking", "CRIT"),) and t["R1"].tos == 0
    assert t["R1"].port_destination == 20000 and t["R2"].port_destination is None
    assert t["R2"].attendu == (("trust", "CRIT"),) and t["R2"].tos == 0xB8 and t["R2"].prealable
    assert t["R3"].attendu == (("dscp_plan", "WARN"),) and t["R3"].dscp == 40
    assert t["R4"].attendu == (("saturation", "CRIT"), ("class_overflow", "WARN")) and t["R4"].lab
    assert t["R5"].attendu == (("exit_balance", "WARN"),) and t["R5"].lab
    assert t["R6"].attendu == (("sampling_adequacy", "CRIT"),) and t["R6"].sampling == 4096
    assert all(x.attendu == () for x in defaut if x.serie != "R")
    # en fin de plan (avant la série S, ajoutée après) : les ports des autres séries ne bougent pas
    assert min(p for x in recette for p in x.ports) > max(
        p for x in defaut if x.serie not in {"R", "S"} for p in x.ports
    )


def test_cli_plan_recette(capsys):
    assert cli.main(["plan", "--lien", "10M", "--profil", "recette", "--format", "csv"]) == 0
    lignes = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [x["id"] for x in lignes] == [f"R{i}" for i in range(1, 7)]
    assert lignes[3]["attendu"] == "saturation:CRIT, class_overflow:WARN"
    assert lignes[0]["note"].startswith("port destination 20000 ; ")
    assert cli.main(["plan", "--lien", "10M", "--profil", "recette"]) == 0
    sortie = capsys.readouterr().out
    assert "Recette qos-audit : comparer ses verdicts" in sortie and "Validé" not in sortie


def test_serie_s_sondes(defaut):
    """#17 : sondes de production, légères, dans le profil, six classes en simultané."""
    sondes = plan.plan_par_defaut("sonde")
    assert [x.id for x in sondes] == ["S-EF", "S-AF41", "S-D1", "S-D2", "S-D3", "S-BF"]
    assert {x.groupe for x in sondes} == {"S"} and not any(x.lab for x in sondes)
    assert {x.dscp for x in sondes} == {46, 34, 26, 18, 10, 0}
    assert all(x.duree_s == 30 and x.taille == 200 and x.protocole == "udp" for x in sondes)
    # 64 kbit/s par classe : 384 kbit/s pour la passe, moins de 4 % d'un lien de 10 Mbit/s
    execs = plan.executions(sondes, 10e6)
    assert sum(e.debit_bps for e in execs) == pytest.approx(384e3)
    # en fin de plan : les ports des autres séries (R comprise) ne bougent pas
    autres = max(p for x in defaut if x.serie != "S" for p in x.ports)
    assert min(p for x in sondes for p in x.ports) > autres


def test_cli_plan_sonde(capsys):
    assert cli.main(["plan", "--lien", "10M", "--profil", "sonde"]) == 0
    sortie = capsys.readouterr().out
    assert "Sondes de production : une passe toutes les 15 min" in sortie and "Validé" not in sortie
    assert sortie.count("| S-") == 6
