# Relevé P4/G6 : mapping ElastiFlow et compteurs matériels

Fiche opérateur prête à compléter, préparée le 31 octobre 2026 pour ElastiFlow-Validation v0.1.0. Aucune mesure réelle n'est renseignée et aucun accès au banc n'a été effectué ; les cases restent volontairement non validées.

Le périmètre reprend P4 et G6 de la [fiche VAL-ELASTIFLOW-001](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/VAL-ELASTIFLOW-001.md). Le mapping des flux et celui des compteurs sont relevés séparément ; la conformité des volumes échantillonnés, leur stabilité et la justesse des compteurs matériels ne constituent pas un verdict unique.

## Organisation du relevé

Les fichiers du dossier sont des modèles : [Mapping-P4.csv](Mapping-P4.csv),
[Mesures-G6.csv](Mesures-G6.csv) et [Plan-support-G6.yaml](Plan-support-G6.yaml).
Copier les gabarits dans un dossier de campagne à accès restreint avant de les remplir ;
ne pas committer les données réelles, les documents bruts ou les secrets dans ce dépôt.

| Champ | À compléter avant exécution |
| --- | --- |
| Identifiant de campagne | |
| Site / environnement | ☐ Lab dédié ☐ Autre, autorisation à joindre |
| Opérateur / relecteur | |
| Créneau et autorisation | |
| Switch A : modèle / firmware / IP d'agent | |
| Switch B : modèle / firmware / IP d'agent | |
| Client / serveur / outil iperf et version | |
| Collecteur ElastiFlow : version exacte et preuve | |
| Elasticsearch ou OpenSearch : version et preuve | |
| Index concrets flux / compteurs | |
| Source NTP / écart mesuré de chaque horloge | |
| Capacité physique et débit contractuel du circuit | |
| Point A : exportateur / ifIndex / nom de port / sens | |
| Point B : exportateur / ifIndex / nom de port / sens | |
| Échantillonnage nominal / intervalle compteurs avant test | |
| Délai d'indexation confirmé en P7, en secondes | |

Prévoir un opérateur pour l'injection et un pour les relevés switch, ou un moyen de collecte préalablement vérifié. Les preuves sont datées en UTC, avec précision suffisante pour documenter les décalages entre équipements.

### Sécurité et autorisations

- **Lecture seule** : consulter les versions, exporter mapping et documents du seul trafic de test, relever les compteurs et horloges. Utiliser des droits limités aux index et équipements nécessaires.
- **À autoriser avant exécution** : modifier `sflow counter interval`, activer un export, changer le sampling ou générer du trafic. L'accord doit préciser les interfaces, la durée, la charge et le retour arrière.
- **À exclure** : saturation, effacement des compteurs, redémarrage ou modification d'une interface de production pour cette seule préparation. Aucun de ces gestes n'est requis par cette fiche.
- **Secrets et confidentialité** : ne pas archiver de mot de passe, clé API, en-tête Authorization ni URL contenant un secret. Conserver les originaux sensibles dans un espace restreint ; joindre au rapport une copie expurgée avec la correspondance des alias conservée séparément.

## P4 : établir le mapping réel

### Preuves à collecter

Dans Discover ou OpenSearch Dashboards, retrouver un flux de test connu, puis exporter son JSON avec l'index concret, l'identifiant et les horodatages. La confirmation sur le mapping réel et la version installée reste nécessaire, même si un nom est documenté dans le protocole ([champs à connaître, §9.2](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md)).

Conserver les fichiers suivants, sans modifier leur contenu brut :

| Preuve attendue | Nom proposé |
| --- | --- |
| Version du collecteur et mode de déploiement | `P4-version-collecteur.txt` |
| Version du stockage, réponse autorisée de `GET /` | `P4-version-stockage.json` |
| Mapping de chaque index concret de flux utilisé | `P4-mapping-flux-<index>.json` |
| Mapping de chaque index concret de compteurs utilisé | `P4-mapping-compteurs-<index>.json` |
| Capacités des champs effectivement retenus | `P4-field-caps-<index>.json` |
| Documents de flux du trafic connu, par point | `P4-flux-<point>.json` |
| Au moins deux counter samples consécutifs par interface et sens | `P4-compteurs-<point>.json` |
| Sorties switch, identité ifIndex/port et configuration sFlow | `P4-switch-<point>.txt` |
| Table complétée des correspondances | `Mapping-P4.csv` |

Gabarits de requêtes en lecture seule, à adapter dans la console du stockage. Les marqueurs entre chevrons sont obligatoirement remplacés par des noms confirmés ; les réponses ne doivent pas être jointes à une issue publique sans expurgation.

```text
GET /
GET <INDEX_CONCRET_FLUX>/_mapping
GET <INDEX_CONCRET_COMPTEURS>/_mapping
GET <INDEX_CONCRET_FLUX>/_field_caps?fields=<CHAMPS_FLUX_CONFIRMES_SEPARES_PAR_VIRGULES>&include_unmapped=true
GET <INDEX_CONCRET_COMPTEURS>/_field_caps?fields=<CHAMPS_COMPTEURS_CONFIRMES_SEPARES_PAR_VIRGULES>&include_unmapped=true
```

Relever les types, l'existence de valeurs et les capacités de recherche/agrégation pour chaque index réellement interrogé. Un champ présent sur un seul index d'un motif ne suffit pas à qualifier tous les index du motif ; isoler les versions de schéma incompatibles ou documenter un périmètre homogène.

### Table des flux

Compléter les lignes `flux` de `Mapping-P4.csv`, puis écrire `champs.yaml`. Les clés et les surcharges ci-dessous correspondent à l'[implémentation de lecture v0.1.0](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/src/elastiflow_validation/lecture.py).

```yaml
horodatage: "<CHAMP_DATE_FLUX_CONFIRME>"
ip_source: "<CHAMP_IP_SOURCE_CONFIRME>"
ip_destination: "<CHAMP_IP_DESTINATION_CONFIRME>"
port_source: "<CHAMP_PORT_SOURCE_CONFIRME>"
port_destination: "<CHAMP_PORT_DESTINATION_CONFIRME>"
protocole: "<CHAMP_PROTOCOLE_CONFIRME>"
octets: "<CHAMP_OCTETS_FLUX_CONFIRME>"
paquets: "<CHAMP_PAQUETS_FLUX_CONFIRME>"
exportateur: "<CHAMP_EXPORTATEUR_CONFIRME>"
sampling: "<CHAMP_SAMPLING_CONFIRME>"
dscp: "<CHAMP_DSCP_CONFIRME>"
interface_entree: "<CHAMP_IFINDEX_ENTREE_CONFIRME>"
interface_sortie: "<CHAMP_IFINDEX_SORTIE_CONFIRME>"
echantillonneur: "<CHAMP_IFINDEX_ECHANTILLONNEUR_CONFIRME>"
```

**Attention au sampling** : la documentation cite `flow.sampling_interval`, tandis que le défaut du code v0.1.0 est `flow.sampling.interval` ; renseigner explicitement la valeur observée dans `champs.yaml`, sans choisir l'une par convention ([protocole §9.2](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md), [défauts du lecteur](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/src/elastiflow_validation/lecture.py)). Conserver la valeur brute du champ, le sampling configuré sur le port et le lien vers leur preuve.

L'identifiant d'échantillonneur doit désigner le port qui a prélevé le paquet, et non seulement le couple entrée/sortie ; ce couple ne suffit pas à résoudre un double échantillonnage ([protocole, double échantillonnage](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md)). Si le champ n'est pas disponible alors que plusieurs prélèvements sont possibles, laisser ce point non validé et ne pas corriger les volumes en les divisant par deux.

### Table des counter samples

Compléter les lignes `compteurs` de `Mapping-P4.csv` indépendamment du YAML des flux. Pour chaque champ, préciser sa sémantique et sa source de preuve ; aucun nom spécifique de schéma n'est prérempli.

- **Identité** : index, exportateur/agent, ifIndex, nom du port, sens entrée/sortie.
- **Temps** : instant de mesure porté par le sample, instant d'ingestion si disponible, intervalle annoncé et intervalle réellement observé.
- **Mesure** : compteurs cumulatifs octets et paquets, ou débit déjà calculé ; unité, largeur des compteurs et indication de discontinuité/reset.
- **Capacité** : vitesse physique du port et, séparément, débit du circuit si une utilisation par rapport au contrat est souhaitée.
- **Base de comptage** : préciser la base effectivement établie pour chaque source, avec VLAN/FCS et autres différences éventuelles notées comme confirmées ou inconnues.

Si seuls des débits calculés sont exposés, archiver aussi leur période d'intégration et leur méthode si elle est disponible. Ne pas reconstruire fictivement des compteurs cumulatifs à partir de captures d'écran.

### Décision P4

- [ ] Versions du collecteur et du stockage prouvées.
- [ ] Mapping archivé pour tous les index concrets utilisés.
- [ ] Champs flux renseignés, types et valeurs vérifiés sur le trafic connu.
- [ ] Sampling confirmé et explicitement surchargé dans `champs.yaml`.
- [ ] Exportateur, ifIndex et sens reliés au bon port physique.
- [ ] Échantillonneur identifié, ou absence de double comptage démontrée et limitée au périmètre observé.
- [ ] Index et sémantique des counter samples confirmés séparément.
- [ ] Horloges et retard d'indexation documentés.

**Décision à renseigner** : ☐ P4 validé ☐ P4 partiel ☐ P4 non validé. Observations, références des preuves, date et signature : ____________________.

## G6 : comparer les mesures matérielles

G6 compare l'utilisation issue des counter samples à celle relevée sur le switch ; il reste manuel dans v0.1.0 et n'est pas exécuté automatiquement par `efv injecter` ([protocole, outillage G6](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md)). Le présent CSV constitue une annexe de mesure, pas une entrée prise en charge par `efv rapport`.

### Contrat de comparaison à figer avant l'essai

| Paramètre | Valeur à approuver |
| --- | --- |
| Interface et sens comparés, pour chaque point | |
| Même population de trafic des deux côtés | |
| Fenêtre de collecte proposée | 30 min par exécution, cadence cible 30 s |
| Fenêtre utile proposée | Hors première et dernière minute ; bornes UTC à noter |
| Intervalle réellement exposé par chaque source | |
| Tolérance maximale d'alignement des fenêtres | |
| Tolérance d'erreur de débit par intervalle | |
| Tolérance d'erreur de volume sur la fenêtre utile | |
| Règle de traitement des trous et nombre maximal admis | |
| Base d'octets commune et correction éventuellement prouvée | |
| Définition de l'utilisation | ☐ Capacité physique ☐ Débit du circuit, valeur : |
| Seuils d'arrêt : CPU, pertes, impact sur le service | |
| Signataire du contrat et date | |

Les durées de 30 min et la cadence de 30 s sont alignées sur l'objectif de constance et la configuration recommandée du [protocole](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md). Les tolérances numériques de G6 ne sont pas fixées ici : les faire approuver avant la mesure, sans les ajuster après observation des résultats.

### Collecte sur le switch et retour arrière

Archiver `display sflow` avant et après l'essai, ainsi que l'identité du port et les sorties de compteurs. Le protocole donne `display counters rate inbound interface ...` comme point de départ ; confirmer sur le modèle la syntaxe, le sens sortant et la fenêtre de moyennage avant de comparer les chiffres ([commandes du protocole](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/protocole-validation-elastiflow-sflow.md)).

La modification ci-dessous n'est qu'un gabarit à faire autoriser ; elle ne doit pas être exécutée sur un port non approuvé. Relever la valeur précédente et prévoir sa restauration exacte, sans supposer qu'elle vaut 300 s.

```text
# APRÈS AUTORISATION ET VALIDATION DE LA SYNTAXE DU MODÈLE UNIQUEMENT
system-view
interface <INTERFACE_DE_TEST_AUTORISEE>
 sflow counter interval 30
```

Privilégier les valeurs cumulatives des compteurs, accompagnées de leur temps de mesure. Si seule la commande de débit est disponible, conserver ses valeurs brutes et sa période de calcul ; une moyenne d'une autre durée ne constitue pas un relevé comparable à un intervalle de 30 s.

### Déroulement proposé

- **Avant T0** : confirmer P4, l'accord de test, le trafic de fond, les horloges, la capacité et la base de comptage. Vérifier que les counter samples arrivent et réaliser un court relevé au repos.
- **Injection préparée** : le fichier `Plan-support-G6.yaml` définit une seule exécution A3 de 30 min à 10 % du lien, proposée comme trafic de support ; ce n'est pas le G6 matériel lui-même ni une validation complète de la série A. Le plan standard prévoit trois répétitions A3, d'où ce plan dédié pour éviter de lancer involontairement trois séquences ([plan et commandes du dépôt](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/README.md)).
- **De T0 à T0 + 30 min** : relever chaque source au plus près de sa cadence de 30 s, et noter les timestamps effectifs, pas seulement l'heure prévue. Pour une grille exactement alignée, 30 min représentent 60 intervalles et 61 relevés de bornes ; hors une minute à chaque bord, la fenêtre utile comporte 56 intervalles.
- **Après l'injection** : attendre le retard d'indexation mesuré en P7, puis vérifier que les données sont stabilisées. Conserver les documents tardifs et distinguer heure de mesure et heure d'arrivée.
- **Clôture** : relever CPU, pertes sFlow et éventuelles discontinuités ; restaurer toute configuration modifiée, prouver le retour à l'état initial et archiver les sorties.

Dans `Mesures-G6.csv`, créer une ligne par observation, source, point et sens. Utiliser `switch` ou `elastiflow_compteurs` dans `source`, conserver les compteurs comme entiers décimaux exacts, et laisser vide une valeur non disponible ; ne jamais remplacer une absence par zéro.

**Périmètre identique obligatoire** : comparer l'interface entière à l'interface entière, dans le même sens. Si un compteur matériel inclut du trafic de fond ou de gestion, ne pas le comparer au seul flux iperf filtré ; sur un lien non isolé, G6 qualifie les compteurs de l'interface entière, pas la justesse du volume de l'unique test.

### Calculs et anomalies

Pour deux compteurs cumulatifs valides \(C_0\) et \(C_1\), relevés en octets aux instants \(t_0\) et \(t_1\), calculer les différences et le débit sur leur durée réelle :

\[
\Delta C = C_1-C_0,\qquad
\Delta t=t_1-t_0,\qquad
R=\frac{8\Delta C}{\Delta t},\qquad
U=100\frac{R}{B}.
\]

Ici \(R\) est en bit/s, \(\Delta t\) en secondes, et \(B\) est la capacité en bit/s explicitement choisie dans le contrat. Garder séparés les pourcentages rapportés à la vitesse du port et au débit contractuel lorsqu'ils diffèrent.

Pour des fenêtres et bases comparables, calculer \(E=100(R_{\mathrm{EF}}-R_{\mathrm{switch}})/R_{\mathrm{switch}}\). Si la référence vaut zéro, ne pas produire de pourcentage : noter l'écart absolu et appliquer la règle préapprouvée pour le repos.

- **Reset ou redémarrage** : invalider l'intervalle traversant la discontinuité ; un compteur en baisse n'est pas automatiquement un wrap.
- **Wrap confirmé** : n'ajouter \(2^w\) que si la largeur \(w\), la capacité, le temps écoulé et l'absence de reset permettent de lever l'ambiguïté sur les tours. Sinon, déclarer l'intervalle inexploitable.
- **Trous, doublons, désordre temporel** : conserver les observations brutes, identifier la cause et ne pas interpoler silencieusement. Dédoublonner uniquement sur une identité de sample démontrée.
- **Fenêtres non alignées** : comparer une fenêtre commune démontrable, ou déclarer la comparaison non concluante ; ne pas présenter un timestamp d'ingestion comme un instant de mesure.
- **Unités et précision** : noter bit/s ou octet/s, base décimale/binaire et moyennage de chaque affichage. À l'import dans un tableur, protéger les grands compteurs contre l'arrondi numérique.
- **Sampling** : ne pas appliquer automatiquement le facteur 1:N des flow samples aux compteurs matériels. Faire d'abord confirmer la sémantique du champ observé.

### Résultats G6 et décision

| Point / sens | Fenêtre utile UTC | Intervalles attendus / valides | Débit switch | Débit EF compteurs | Écart % | Écart volume % | Conclusion / preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A / à préciser | | | | | | | |
| B / à préciser | | | | | | | |

- [ ] P4 validé sur le périmètre testé et contrat G6 signé avant l'essai.
- [ ] Même interface, même sens, même trafic, même base et fenêtres comparables.
- [ ] Cadence réellement observée et tous les trous/discontinuités documentés.
- [ ] Débits et volumes calculés uniquement sur les intervalles valides.
- [ ] Tolérances préapprouvées respectées, ou écarts explicitement classés.
- [ ] Configuration restaurée et preuves brutes archivées.

**Décision à renseigner** : ☐ G6 validé ☐ G6 partiel ☐ Non concluant ☐ G6 non validé. Cause, périmètre exact, date, opérateur et relecteur : ____________________.

Reporter P4 dans la section 6 et G6 dans la section 7.6 de la [fiche VAL](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/docs/VAL-ELASTIFLOW-001.md). Une validation P4/G6 ne vaut pas signature du verdict global ni validation des autres séries.

## Commandes préparées, sans exécution matérielle

### Prévisualiser une seule injection

Exemple illustratif : remplacer le lien et les deux adresses par le banc autorisé. La présence de `--dry-run` est volontaire ; ne pas le retirer sans accord de test.

```bash
uv run efv injecter \
  --plan docs/releves-p4-g6/Plan-support-G6.yaml --lien 10M --test A3 \
  --client 192.0.2.10 --serveur 192.0.2.20 \
  --sortie resultats.csv --dry-run
```

Lancer depuis la racine du dépôt en donnant le chemin réel du fichier de plan s'il a été copié ailleurs. Le prévisualiseur affiche aussi les commandes nft à vérifier ; il ne configure pas le sampling du switch ni son export de compteurs.

### Lire les flux après un essai autorisé

Les options suivantes sont disponibles dans la [CLI documentée](https://github.com/Mathilde-Team/Elastiflow-Validation/blob/91a07f42675e7eccf7ed7f82919caee2f0b4e3f2/README.md). Remplacer tous les marqueurs, et configurer l'accès via les variables `EFV_ES_*` sans copier de secrets dans la fiche.

```bash
uv run efv lire \
  --resultats resultats.csv --champs champs.yaml \
  --index '<INDEX_FLUX_CONFIRME>' --delai "${DELAI_P7_SECONDES:?Renseigner le délai P7 mesuré}" \
  --bord 60 --cv 0 \
  --sortie lectures.csv --series series.csv --stabilite stabilite.csv

uv run efv rapport \
  --resultats resultats.csv --lectures lectures.csv \
  --series series.csv --bord 60 --cv 0 \
  --point-mesure '<EXPORTATEUR_EXACT_A>=<IFINDEX_A>' \
  --point-mesure '<EXPORTATEUR_EXACT_B>=<IFINDEX_B>' \
  --sortie rapport-flux.md
```

Ne conserver que les points réellement mesurés ; `EXPORTATEUR_EXACT` est la valeur du champ observé, pas nécessairement un alias « sw-a ». Ces commandes concernent les flux et leur stabilité, pas l'index des counter samples ni le CSV manuel G6.

## Dossier de preuves à remettre

Regrouper la fiche remplie, `Mapping-P4.csv`, `Mesures-G6.csv`, les mappings/documents bruts, les sorties switch, `champs.yaml`, le plan effectivement utilisé et les fichiers `resultats.csv`, `lectures.csv`, `series.csv`, `stabilite.csv` et `rapport-flux.md` lorsque le test de support a été exécuté. Ajouter un inventaire des fichiers avec horodatage et empreinte SHA-256, ainsi que l'accord de changement et la preuve de restauration.

Pour lever le reste de P4 dans l'issue 14, fournir le champ sampling effectivement établi avec la version ElastiFlow, le mapping archivé et le périmètre d'index ; l'exigence de preuve vient de l'[issue de suivi P4](https://github.com/Mathilde-Team/Elastiflow-Validation/issues/14). Ne modifier la documentation et la calibration finales qu'après revue de ces preuves.
