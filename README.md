# Elastiflow-Validation

Outil de contre-expertise d'**ElastiFlow** : on envoie un trafic connu (iperf2) à travers des équipements Comware qui exportent du **sFlow**, puis on compare ce qu'ElastiFlow affiche à la vérité terrain mesurée sur le client et le serveur (compteurs nft, rapport iperf).

## Documents de référence

- [Protocole de test](docs/protocole-validation-elastiflow-sflow.md) : théorie de l'échantillonnage, sampling-rate par débit de lien, injection, QoS opérateur, lecture dans ElastiFlow.
- [Document de validation VAL-ELASTIFLOW-001](docs/VAL-ELASTIFLOW-001.md) : critères d'acceptation, fiches de test, verdict.

## Chaîne visée

| Étape | Commande | Issue |
| --- | --- | --- |
| Calculs (sampling-rate, borne à 95 %, DSCP/TOS, tailles) : disponible | `efv sampling` | #2 |
| Plan de tests (phase 0, séries A à E, QoS, G1 à G6) : disponible | `efv plan` | #3 |
| Injection iperf2 et relevé nft vers `resultats.csv` : disponible | `efv injecter` | #4 |
| Lecture ElastiFlow (Elasticsearch / OpenSearch) vers `lectures.csv` : disponible | `efv lire` | #5 |
| Comparaison, verdict et document de validation rempli : disponible | `efv rapport` | #6 |

## Installation et usage

```bash
uv sync --locked
uv run efv --help
uv run efv --debug ...      # traces de debug (ou SOCLE_DEBUG=1)
```

`efv sampling` sans argument rejoue le tableau 4.2 du protocole ; avec des débits, il dimensionne ces
liens (débit effectif du circuit). Les options suivent le §4.3 :

```bash
uv run efv sampling                         # tableau 4.2 (plancher 5 %, 5 min, 1500 o, erreur 5 %)
uv run efv sampling 10M 1G --fenetre 1800   # 30 min d'analyse
uv run efv sampling 20M --plancher 1 --erreur 10 --taille 500
uv run efv sampling 10M --cv 0.83                 # erreur sur les octets, tailles 64/576/1500 o
```

`efv rapport` confronte les lectures ElastiFlow à la référence nft de `resultats.csv` et remplit les
sections 7, 8 et 10 de VAL-ELASTIFLOW-001 (erreur, borne à 95 % `196 ÷ √n`, conformité, biais
moyen de A2/A3/A5, comparaison avant/après correction du sampling) :

```bash
uv run efv rapport                       # resultats.csv + lectures.csv -> rapport-validation.md
uv run efv rapport --sortie val-001.md   # sections 7, 8 et 10 remplies, verdict à signer
```

Avec `--calibration calibrations/<site>.yaml`, le rapport produit aussi le **fichier de calibration
consommé par qos-audit** (`docs/CALIBRATION.md` de ce dépôt voisin) : noms de champs P4, facteur
d'échantillonnage P6 et marge P5 inférés des mesures, exportateurs P2 et sampling-rates observés,
délai P7 (`--ingest-lag-minutes`). C'est un brouillon : `model`, `comware_version`, `interfaces`
(`display sflow`), version ElastiFlow et date de signature restent à compléter à la main.

Les formules (borne `196/√n`, n cible `(196/e)²`, erreur sur les octets `196·√((1+CV²)/n)`, détection `1 − exp(−n)`, DSCP ↔ TOS, taille IP ↔
`-l` UDP ↔ trame Ethernet) sont dans `elastiflow_validation.calcul`.

`efv plan` déroule le plan YAML (`src/elastiflow_validation/plans/protocole.yaml` par défaut) pour
un lien donné : débit absolu (`-b`), charge UDP (`-l`), octet TOS (`-S`), durée ou volume, ports
sources (`40000 + N`, dans l'ordre du fichier), sampling attendu et durée totale estimée. Les débits
Q2 sont exprimés en % du débit contractuel de la classe, à fournir avec `--cir` :

```bash
uv run efv plan --lien 10M --cir EF=2M --cir AF41=3M --cir D1=2M --cir BF=3M
uv run efv plan --lien 1G --format csv > plan.csv
uv run efv plan --lien 10M --plan mon-plan.yaml
uv run efv plan --lien 10M --profil minimal   # sous-ensemble d'environ 1 jour (protocole 11)
uv run efv plan --lien 10M --profil recette   # recette de qos-audit (série R, protocole 8.7)
```

La série R injecte des défauts connus (voix en TOS 0, EF depuis un port non fiable, DSCP hors plan, 150 %
de charge, bascule de sortie, sampling trop grossier) ; le champ `attendu` du plan porte le verdict
qos-audit attendu (`controle:GRAVITE`), affiché dans la colonne `attendu`, et `port_destination` remplace
le port du serveur iperf pour un test (port RTP de R1).

`efv injecter` remplace `run_test.sh` (§7.7) sur le client du banc (root, `iperf` 2 et `nft`) : une règle
nft `counter` par port source, un iperf2 par port (`-B CLIENT:PORT`), les tests d'un même groupe (Q3, Q4)
lancés ensemble, relevé des compteurs 2 s après la fin, une ligne par test ajoutée à `resultats.csv`, pause de
5 min entre deux lancements. Les changements de sampling de l'exporteur et les préalables (`tc … netem`) sont
annoncés et attendent la confirmation de l'opérateur ; les tests de laboratoire demandent `--lab`.
Sans iperf2, `--outil iperf3` (§7.12) génère les commandes iperf3 (`--cport`, `-b 0` en UDP sans débit,
`--pacing-timer 100`, rapport JSON) et donne à chaque processus simultané son serveur (`50001`, `50002`…,
liste à lancer affichée) ; `port_destination` est alors une plage pour les tests à plusieurs processus.

```bash
uv run efv injecter --lien 10M --test A2 --dry-run          # commandes, sans rien exécuter
sudo .venv/bin/efv injecter --lien 10M --profil minimal --lab \
     --cir EF=2M --cir AF41=3M --cir D1=2M --cir BF=3M --sortie resultats.csv
```

Colonnes de `resultats.csv` : `id`, `repetition`, `serie`, `groupe`, `protocole`, `debit` (`-b`), `taille`
(IP), `tos`, `ports`, `sampling`, `debut` et `fin` (epoch), `paquets` et `octets` (IP, nft, somme des ports),
`client`, `serveur` et `port_destination` (clé de lecture dans ElastiFlow).

`efv lire` (§9) interroge Elasticsearch ou OpenSearch pour chaque test de `resultats.csv` dont le délai
d'agrégation est écoulé : filtre sur la clé (IP, ports source, port de destination, protocole) et la fenêtre
`debut − 1 min` à `fin + --delai` (600 s par défaut, P7), puis, **par exportateur**, sommes brutes d'octets et de
paquets, nombre d'enregistrements, sommes utiles hors minutes de bord (`--bord`, 60 s, minutes entières),
débit utile, sampling-rates et DSCP lus. Aucune correction n'est appliquée (base trame ou IP, facteur
d'échantillonnage : `efv rapport`, #6). Les noms de champs (défauts alignés sur qos-audit) se surchargent par
`--champs champs.yaml` (`sampling: flow.sampling_interval`, clés : `horodatage`, `ip_source`,
`ip_destination`, `port_source`, `port_destination`, `protocole`, `octets`, `paquets`, `exportateur`,
`sampling`, `dscp`) ; chacun est vérifié dans le `_mapping` de l'index avant toute lecture (P4, #14).
Champs facultatifs (#30), ignorés avec un avertissement s'ils manquent au mapping : `interface_entree`
(`flow.input.ifindex`) et `interface_sortie` (`flow.output.ifindex`) donnent la colonne `interfaces`
(octets par couple, `12>3=4500`) ; `echantillonneur` (sans défaut : champ de l'interface qui a prélevé le
paquet, sFlow `source_id_index`, à relever en P4) donne `echantillonneurs` et `double_echantillonnage`
(`oui` quand plusieurs échantillonneurs d'un même exportateur ont vu le test).

```bash
export EFV_ES_URL=https://elastic.example:9200   # + EFV_ES_API_KEY, ou EFV_ES_USER et EFV_ES_PASSWORD
uv run efv lire --resultats resultats.csv --sortie lectures.csv --index 'elastiflow-flow-*' --champs champs.yaml
```

URL et identifiants sont lus dans l'environnement (`EFV_ES_CA` pour l'autorité du cluster) et jamais affichés.

## Développement

```bash
make lint   # ruff check + ruff format --check
make test   # pytest, couverture 95 % minimum (branches comprises)
```

## Socle qualité Mathilde-Team

Ce dépôt est créé depuis [`modele-depot`](https://github.com/Mathilde-Team/modele-depot). Il suit le [socle qualité de l'équipe](https://github.com/Mathilde-Team/.github/blob/main/SOCLE.md) :

- **`main`** (livraison) et **`dev`** (intégration) protégées. On n'y pousse jamais directement : PR vers `dev`, puis PR `dev` → `main`.
- **CI** : job « Qualité (lint, tests) », requis sur les deux branches. À adapter au langage du projet dans `.github/workflows/ci.yml`, en gardant le nom du job.
- **Garde** : `.github/workflows/guard-main.yml` refuse les PR vers `main` qui ne viennent pas de `dev`.
- **Mise à jour des PR** : `.github/workflows/maj-branches.yml` met à jour les PR vers `dev` à chaque avancée de `dev`, et fait résoudre les conflits simples par l'IA locale (SOCLE §11). Label `pas-de-maj-auto` pour exclure une PR ; une PR `resolu-par-ia` repasse en brouillon : la relire avant *Ready for review*.
- **Protections versionnées** : `.github/branch-protection/{main,dev}.json`, vérifiées chaque jour par le workflow « Protection des branches ».
- **Licence** : [MIT](LICENSE).
- **Couverture des tests** : seuil bloquant de **95 %**, branches comprises (décision d'équipe du 21/10/2026, [.github#9](https://github.com/Mathilde-Team/.github/issues/9)). Le seuil est déclaré une seule fois, dans `pyproject.toml` (`[tool.coverage.report] fail_under = 95`, `[tool.coverage.run] branch = true`) ; le job Qualité le lit (`make test`) sans le recopier.

### Couverture des tests

- Remplacer `source = ["scripts"]` de `pyproject.toml` par le paquet du dépôt. Le code d'interface (GTK, Electron) compte : il est mesuré par un job graphique (Xvfb) et combiné, jamais exclu pour atteindre le seuil.
- Exclusions (`omit`, `# pragma: no cover`) réservées au code réellement inatteignable en test, chacune justifiée par un commentaire.
- **Dépôt existant sous 95 %** : fixer d'abord `fail_under` à la mesure du jour (aucune régression possible), puis le relever à chaque PR de tests jusqu'à 95. Le palier et la date cible sont écrits dans l'issue de suivi du dépôt.
- Le modèle s'applique la règle : `scripts/check_branch_protection.py` est couvert à 100 % par `tests/`.

```bash
make test   # même commande que le job Qualité : uv run --locked pytest --cov
```

### Journalisation Python (SOCLE §8)

- Tout le Python du dépôt journalise avec **loguru** : `debug()` en entrée et avant chaque sortie de chaque fonction non triviale, chaque `except` journalise (cause exacte, jamais de secret), messages en français.
- Les règles sont vérifiées par analyse AST dans `tests/test_loguru_regles.py`. Les fonctions sans trace restantes sont comptées par `scripts/audit_debug_sorties.py` et figées dans `scripts/audit_debug_sorties.baseline` — 0 dans le modèle : la référence ne peut qu'être maintenue à 0.
- Voir les traces de debug : `SOCLE_DEBUG=1` (ex. `SOCLE_DEBUG=1 scripts/check_branch_protection.py --branche dev`).

### Après « Use this template »

Les protections ne sont pas copiées par GitHub avec le modèle. Un administrateur les applique une fois :

```bash
gh repo create Mathilde-Team/<nom> --public --template Mathilde-Team/modele-depot --clone
cd <nom>
scripts/appliquer_socle.sh Mathilde-Team/<nom>     # crée dev + applique les protections
```

Puis pour chaque sujet :

```bash
git switch dev && git pull && git switch -c feat/<n°>-description
# …
gh pr create --base dev
```
