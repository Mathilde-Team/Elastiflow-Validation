"""Lecture dans ElastiFlow (#5) : requête par test, sommes utiles, champs, connexion, CLI."""

from __future__ import annotations

import csv
import io
import json
import urllib.error
from pathlib import Path

import pytest

from elastiflow_validation import cli, injection, lecture

DEBUT = 1_800_000_010  # 10 s après une minute ronde
FIN = DEBUT + 300

MAPPING = {
    "elastiflow-flow-2026.10.26": {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "source": {"properties": {"ip": {"type": "ip"}, "port": {"type": "long"}}},
                "destination": {"properties": {"ip": {"type": "ip"}, "port": {"type": "long"}}},
                "network": {
                    "properties": {
                        "transport": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                        "bytes": {"type": "long"},
                        "packets": {"type": "long"},
                    }
                },
                "flow": {
                    "properties": {
                        "export": {"properties": {"host": {"properties": {"name": {}}}}},
                        "sampling": {"properties": {"interval": {"type": "long"}}},
                        "ip": {"properties": {"dscp": {"type": "long"}}},
                    }
                },
            }
        }
    }
}


def _minute(debut_s: int, octets: float, paquets: float) -> dict:
    return {"key": debut_s * 1000, "octets": {"value": octets}, "paquets": {"value": paquets}}


def _exportateur(nom: str, minutes: list[dict]) -> dict:
    return {
        "key": nom,
        "doc_count": 12,
        "octets": {"value": sum(m["octets"]["value"] for m in minutes)},
        "paquets": {"value": sum(m["paquets"]["value"] for m in minutes)},
        "sampling": {"buckets": [{"key": 8, "doc_count": 12}]},
        "dscp": {"buckets": [{"key": 46, "doc_count": 11}, {"key": 0, "doc_count": 1}]},
        "minutes": {"buckets": minutes},
    }


class FauxClient:
    """Réponses enregistrées : ``_mapping`` puis une réponse ``_search`` par test."""

    def __init__(self, recherches: list[dict], mapping: dict | None = None) -> None:
        self.recherches = list(recherches)
        self.mapping = MAPPING if mapping is None else mapping
        self.appels: list[tuple[str, str, dict | None]] = []

    def requete(self, methode: str, chemin: str, corps: dict | None = None) -> dict:
        self.appels.append((methode, chemin, corps))
        if chemin.endswith("/_mapping"):
            return self.mapping
        return self.recherches.pop(0)


def _resultats(tmp_path: Path, lignes: list[dict]) -> Path:
    chemin = tmp_path / "resultats.csv"
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.DictWriter(f, fieldnames=list(lignes[0]))
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
    return chemin


def _ligne(**autres: str) -> dict:
    base = {"id": "Q1-EF", "repetition": "1", "protocole": "udp", "ports": "40019-40026"}
    return base | {"debut": str(DEBUT), "fin": str(FIN)} | autres


def _lire(chemin: Path) -> list[dict]:
    with chemin.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_fenetre_utile():
    # bords d'une minute, arrondis aux minutes entières : 2 minutes utiles sur 300 s
    assert lecture.fenetre_utile(DEBUT, FIN) == (DEBUT - 10 + 120, DEBUT - 10 + 240)
    assert lecture.fenetre_utile(DEBUT, DEBUT + 90) == (DEBUT - 10 + 120,) * 2
    assert lecture.fenetre_utile(DEBUT - 10, DEBUT + 290, bord_s=0) == (DEBUT - 10, DEBUT + 290)


def test_requete_test_cle_et_fenetre():
    banc = injection.Banc()
    corps = lecture.requete_test(_ligne(), lecture.CHAMPS_DEFAUT, banc, delai_s=600)
    filtres = corps["query"]["bool"]["filter"]
    assert {"term": {"source.ip": "10.10.0.2"}} in filtres
    assert {"term": {"destination.ip": "10.20.0.2"}} in filtres
    assert {"range": {"source.port": {"gte": 40019, "lte": 40026}}} in filtres
    assert {"term": {"destination.port": 50001}} in filtres
    assert {"term": {"network.transport": "udp"}} in filtres
    fenetre = next(f["range"]["@timestamp"] for f in filtres if "@timestamp" in f.get("range", {}))
    assert fenetre == {
        "gte": (DEBUT - 60) * 1000,
        "lte": (FIN + 600) * 1000,
        "format": "epoch_millis",
    }
    aggs = corps["aggs"]["exportateurs"]
    assert aggs["terms"]["field"] == "flow.export.host.name"
    assert aggs["aggs"]["minutes"]["date_histogram"]["fixed_interval"] == "1m"
    assert corps["size"] == 0 and corps["track_total_hits"] is True
    # colonnes du banc (efv injecter) prioritaires ; protocole absent : pas de filtre
    ligne = _ligne(client="10.10.9.9", serveur="10.20.9.9", port_destination="20000", protocole="")
    ligne["ports"] = "40063"
    filtres = lecture.requete_test(ligne, lecture.CHAMPS_DEFAUT, banc)["query"]["bool"]["filter"]
    assert {"term": {"source.ip": "10.10.9.9"}} in filtres
    assert {"term": {"destination.ip": "10.20.9.9"}} in filtres
    assert {"range": {"source.port": {"gte": 40063, "lte": 40063}}} in filtres
    assert {"term": {"destination.port": 20000}} in filtres
    assert not any("network.transport" in f.get("term", {}) for f in filtres)


def test_lire_test_sommes_par_exportateur():
    m0 = DEBUT - 10  # minute ronde contenant le début
    minutes = [_minute(m0 + 60 * i, 1000.0 * (i + 1), 10.0) for i in range(7)]
    reponse = {
        "aggregations": {
            "exportateurs": {
                "buckets": [_exportateur("sw-a", minutes), _exportateur("sw-b", minutes[:1])]
            }
        }
    }
    client = FauxClient([reponse])
    lignes = lecture.lire_test(
        client, "idx", _ligne(), lecture.CHAMPS_DEFAUT, injection.Banc(), delai_s=600
    )
    assert client.appels[0][:2] == ("POST", "/idx/_search")
    a, b = (dict(zip(lecture.COLONNES_LECTURE, x, strict=True)) for x in lignes)
    # utile : minutes m0+120 et m0+180 (fin à m0+310, bord d'une minute)
    assert (a["exportateur"], a["enregistrements"], a["octets"], a["paquets"]) == (
        "sw-a",
        "12",
        "28000",
        "70",
    )
    assert (a["octets_utiles"], a["paquets_utiles"], a["duree_utile_s"]) == ("7000", "20", "120")
    assert a["debit_utile_bps"] == str(round(7000 * 8 / 120))
    assert (a["sampling"], a["dscp"]) == ("8", "46;0")
    assert (a["debut_fenetre"], a["fin_fenetre"]) == (str(DEBUT - 60), str(FIN + 600))
    assert (b["exportateur"], b["octets_utiles"]) == ("sw-b", "0")
    # test trop court pour une minute utile : pas de débit
    court = _ligne(fin=str(DEBUT + 90))
    c = lecture.lire_test(
        FauxClient([reponse]), "idx", court, lecture.CHAMPS_DEFAUT, injection.Banc()
    )[0]
    assert c[lecture.COLONNES_LECTURE.index("debit_utile_bps")] == ""
    vide = FauxClient([{}])
    assert lecture.lire_test(vide, "idx", _ligne(), lecture.CHAMPS_DEFAUT, injection.Banc()) == []


def test_champs_absents_et_charger_champs(tmp_path: Path):
    client = FauxClient([])
    assert lecture.champs_absents(client, "idx", lecture.CHAMPS_DEFAUT) == []
    champs = lecture.CHAMPS_DEFAUT | {"sampling": "flow.sampling_interval"}
    assert lecture.champs_absents(client, "idx", champs) == ["flow.sampling_interval"]
    assert lecture.champs_absents(client, "idx", {"p": "network.transport.keyword"}) == []
    f = tmp_path / "champs.yaml"
    f.write_text("sampling: flow.sampling_interval\nexportateur: flow.export.ip.addr\n", "utf-8")
    lus = lecture.charger_champs(f)
    assert lus["sampling"] == "flow.sampling_interval" and lus["octets"] == "network.bytes"
    assert lecture.charger_champs(None) == lecture.CHAMPS_DEFAUT
    for texte, motif in (("- a\n", "dictionnaire"), ("inconnu: x\n", "inconnues : inconnu"),
                         ("octets: ''\n", "non vides")):  # fmt: skip
        f.write_text(texte, "utf-8")
        with pytest.raises(ValueError, match=motif):
            lecture.charger_champs(f)
    f.write_text("", "utf-8")
    assert lecture.charger_champs(f) == lecture.CHAMPS_DEFAUT


def test_lire_ecrit_lectures_et_differe_les_tests_recents(tmp_path: Path):
    m0 = DEBUT - 10
    reponse = {
        "aggregations": {
            "exportateurs": {"buckets": [_exportateur("sw-a", [_minute(m0 + 120, 500.0, 5.0)])]}
        }
    }
    resultats = _resultats(
        tmp_path,
        [
            _ligne(),
            _ligne(id="A1"),  # aucun enregistrement
            _ligne(id="A2", debut=str(DEBUT + 10_000), fin=str(FIN + 10_000)),  # trop récent
        ],
    )
    sortie = tmp_path / "lectures.csv"
    client = FauxClient([reponse, {"aggregations": {"exportateurs": {"buckets": []}}}])
    n = lecture.lire(resultats, sortie, client, index="idx", maintenant=lambda: FIN + 1_000)
    assert n == 2
    lignes = _lire(sortie)
    assert list(lignes[0]) == list(lecture.COLONNES_LECTURE)
    assert [(x["id"], x["exportateur"], x["octets_utiles"]) for x in lignes] == [
        ("Q1-EF", "sw-a", "500")
    ]
    assert [a[1] for a in client.appels] == ["/idx/_mapping", "/idx/_search", "/idx/_search"]
    # sans horloge : tout est lu
    client = FauxClient([reponse] * 3)
    assert lecture.lire(resultats, sortie, client) == 3


def test_lire_refuse_un_champ_absent(tmp_path: Path):
    resultats = _resultats(tmp_path, [_ligne()])
    client = FauxClient([], mapping={"i": {"mappings": {}}})
    with pytest.raises(ValueError, match="champs absents du mapping de idx : .*--champs"):
        lecture.lire(resultats, tmp_path / "l.csv", client, index="idx")


def test_connexion_depuis_env():
    with pytest.raises(ValueError, match="EFV_ES_URL"):
        lecture.connexion_depuis_env({})
    c = lecture.connexion_depuis_env({"EFV_ES_URL": "https://es:9200/"})
    assert c.url == "https://es:9200" and "Authorization" not in c.entetes and c.ca is None
    c = lecture.connexion_depuis_env({"EFV_ES_URL": "https://es", "EFV_ES_API_KEY": "k"})
    assert c.entetes["Authorization"] == "ApiKey k"
    c = lecture.connexion_depuis_env(
        {"EFV_ES_URL": "https://es", "EFV_ES_USER": "u", "EFV_ES_PASSWORD": "p", "EFV_ES_CA": "/ca"}
    )
    assert c.entetes["Authorization"] == "Basic dTpw" and c.ca == "/ca"
    assert "dTpw" not in repr(c)  # en-têtes exclus de repr : jamais affichés
    with pytest.raises(ValueError, match="EFV_ES_PASSWORD"):
        lecture.connexion_depuis_env({"EFV_ES_URL": "https://es", "EFV_ES_USER": "u"})


class _Reponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    vus = []

    def urlopen(req, timeout, context):
        vus.append((req.full_url, req.get_method(), req.data, req.headers, timeout, context))
        return _Reponse(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr(lecture.urllib.request, "urlopen", urlopen)
    client = lecture.ClientHttp(lecture.Connexion("https://u:mdp@es:9200", {"X": "1"}))
    assert client.requete("POST", "/i/_search", {"size": 0}) == {"ok": 1}
    assert client.requete("GET", "/i/_mapping") == {"ok": 1}
    assert vus[0][:3] == ("https://u:mdp@es:9200/i/_search", "POST", b'{"size": 0}')
    assert vus[1][2] is None and vus[0][4] == 30 and vus[0][5] is None

    def http_erreur(req, timeout, context):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(lecture.urllib.request, "urlopen", http_erreur)
    with pytest.raises(RuntimeError, match="HTTP 401") as exc:
        client.requete("GET", "/i/_mapping")
    assert "mdp" not in str(exc.value)

    def injoignable(req, timeout, context):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(lecture.urllib.request, "urlopen", injoignable)
    with pytest.raises(RuntimeError, match="injoignable"):
        client.requete("GET", "/i/_mapping")


def test_client_http_ca(monkeypatch: pytest.MonkeyPatch):
    contextes = []
    monkeypatch.setattr(
        lecture.ssl, "create_default_context", lambda cafile: contextes.append(cafile)
    )
    lecture.ClientHttp(lecture.Connexion("https://es", ca="/etc/ca.pem"))
    assert contextes == ["/etc/ca.pem"]


def test_cli_lire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    resultats = _resultats(tmp_path, [_ligne()])
    sortie = tmp_path / "lectures.csv"
    reponse = {"aggregations": {"exportateurs": {"buckets": [_exportateur("sw-a", [])]}}}
    monkeypatch.setattr(cli.time, "time", lambda: FIN + 10_000)
    args = ["lire", "--resultats", str(resultats), "--sortie", str(sortie), "--index", "idx"]
    arguments = cli.construire_parseur().parse_args(args)
    assert cli.commande_lire(arguments, FauxClient([reponse])) == 0
    assert "1 test(s) lu(s)" in capsys.readouterr().out
    assert _lire(sortie)[0]["exportateur"] == "sw-a"
    # délai d'agrégation non écoulé : aucun test lu
    arguments = cli.construire_parseur().parse_args([*args, "--delai", "100000"])
    assert cli.commande_lire(arguments, FauxClient([])) == 0
    assert _lire(sortie) == []
    # erreurs : URL absente (2), champ absent (2), cluster en erreur (1)
    monkeypatch.delenv("EFV_ES_URL", raising=False)
    assert cli.main(args) == 2
    vide = FauxClient([], mapping={})
    assert cli.commande_lire(cli.construire_parseur().parse_args(args), vide) == 2

    class EnPanne(FauxClient):
        def requete(self, methode, chemin, corps=None):
            raise RuntimeError("HTTP 503")

    assert cli.commande_lire(cli.construire_parseur().parse_args(args), EnPanne([])) == 1
