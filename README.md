# Nom du projet

<!-- Remplacer ce README. La partie « Socle qualité » peut rester telle quelle. -->

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
