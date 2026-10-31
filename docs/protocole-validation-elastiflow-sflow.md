# Validation d'ElastiFlow par injection de trafic (iperf2) et lecture sFlow

Protocole de test complet : Comware, sFlow, ElastiFlow, QoS opérateur WAN
Rédigé le 24 octobre 2026

---

## 1. Objectifs et périmètre

Contre-expertiser les chiffres produits par ElastiFlow en envoyant un trafic **connu et reproductible** (iperf2) à travers des équipements Comware qui exportent du sFlow, puis en comparant ce que le collecteur affiche avec la réalité mesurée côté client et serveur.

Ce que le protocole doit valider :

1. **Justesse du débit mesuré** : trafic constant pendant 30 min, UDP et TCP, de très petits débits jusqu'au débit maximum du lien (plancher de visibilité et plafond).
2. **Choix du sampling-rate** en fonction du débit du lien (de 1 à 10 000 Mbit/s) pour des paquets de 100 à 1500 octets.
3. **Marquage QoS** : conservation des classes de l'opérateur (EF, AF41, D1, D2, D3, BF/BE) de bout en bout, et comportement sous congestion.
4. **Taux de répétition des paquets** : débit en paquets par seconde, paquets dupliqués et retransmissions.
5. **Saturation** pendant 1 minute maximum.
6. Tout ce que sFlow expose : ports, protocole, DSCP/CoS, VLAN, flags TCP, interfaces d'entrée et de sortie, compteurs d'interface.

Le document explique séparément la **partie injection** (section 7) et la **partie lecture dans ElastiFlow** (section 9).

---

## 2. Journal de la discussion et décisions prises

| Étape | Point | Décision |
|---|---|---|
| 1 | Idée de départ : un Comware en NQA server comme cible d'iperf | Abandonnée. Le NQA server ne répond qu'aux sondes NQA (udp-echo), ce n'est pas un serveur iperf. |
| 2 | Cible de test | Un **serveur Linux distant** avec `iperf -s` (TCP et UDP), qui donne en plus un rapport de pertes et de gigue côté serveur. |
| 3 | Configuration sFlow actuelle | Standard : sampling-rate **4096**, intervalle de compteurs **300 s**. |
| 4 | Constat | Sur des liens à 10 Mbit/s, 1:4096 donne ~550 échantillons en 30 min, soit une erreur à 95 % d'environ **8 %**. Jugé critique. Le raisonnement est correct : 4096 est inadapté aux petits liens. |
| 5 | Objectif de précision | **5 % à 500 kbit/s** sur un lien de 10 Mbit/s. |
| 6 | Ajouts au périmètre | Marquage QoS, taux de répétition des paquets, saturation d'une minute maximum, valeur de sampling par débit de lien. |

**Correction apportée en cours de discussion.** Dans un tableau précédent, pour un flux de 500 kbit/s avec des paquets de 1470 octets sur une fenêtre de 5 min, j'avais indiqué 1:4. Le calcul correct est 12 755 paquets ÷ 1 537 échantillons ≈ 8,3, soit **1:8**. Le tableau de la section 4 de ce document fait foi.

---

## 3. Théorie : précision d'un échantillonnage sFlow

### 3.1 Formule

sFlow prélève au hasard 1 paquet sur N. Le volume réel est estimé en multipliant par N. L'erreur ne dépend que du **nombre d'échantillons n** recueillis sur la fenêtre d'analyse. À 95 % de confiance :

```
erreur (%) ≈ 196 / √n
```

Pour viser une erreur de 5 % :

```
n = (196 / 5)² ≈ 1 537 échantillons par fenêtre
```

Cette formule porte sur le **comptage de paquets**. Elle vaut aussi pour les **octets** tant que tous les paquets ont la même taille (iperf avec `-l` fixe). Avec des tailles variables (trafic réel), l'estimation des octets ajoute la dispersion des tailles : si CV est le coefficient de variation de la taille des paquets (écart type / moyenne),

```
erreur octets (%) ≈ 196 × √((1 + CV²) / n)
n octets = (196 / e)² × (1 + CV²)
```

| Trafic | CV | Erreur × √(1 + CV²) | n pour 5 % |
| --- | --- | --- | --- |
| Taille fixe (iperf `-l`) | 0 | × 1,00 | 1 537 |
| Marge de 20 % sur l'erreur | 0,66 | × 1,20 | 2 213 |
| Mélange égal 64 / 576 / 1 500 o | 0,83 | × 1,30 | 2 603 |

La marge de 20 à 30 % sur l'erreur correspond donc à un CV de 0,66 à 0,83 et à **2 200 à 2 600 échantillons** (et non 2 000, qui ne donne que 14 % de marge). Le CV d'un lien se mesure sur sa distribution de tailles (`efv sampling --cv <CV>` en tient compte) ; la série de vérification et la marge mesurée publiée dans le rapport sont suivies dans l'issue #10.

Cas particulier : à **1:1**, tous les paquets sont échantillonnés, le comptage est exhaustif et la formule ne s'applique plus (sous réserve que le matériel supporte ce taux et que l'export ne perde rien).

### 3.2 Pourquoi 1:4096 ne convient pas aux petits liens

Flux constant, paquets de 1000 octets, fenêtre de 30 min, 1:4096 :

| Débit | Paquets | Échantillons | Erreur 95 % |
|---|---|---|---|
| 100 kbit/s | 22 500 | ~5 | inexploitable |
| 1 Mbit/s | 225 000 | ~55 | ±26 % |
| 10 Mbit/s | 2,25 M | ~550 | ±8 % |
| 100 Mbit/s | 22,5 M | ~5 500 | ±2,6 % |
| 1 Gbit/s | 225 M | ~55 000 | ±0,8 % |

### 3.3 Plancher de visibilité

Si le nombre d'échantillons attendus est de 1, la probabilité de ne jamais voir le flux est `exp(-1)`, soit 37 %. En règle pratique : un flux dont on attend **moins de 5 échantillons** sur la fenêtre peut ne pas apparaître. La probabilité de détection est `1 − exp(−n attendu)`.

### 3.4 Effet de la taille des paquets

Le sampling est **par paquet**. À débit égal, des paquets de 100 octets produisent 15 fois plus de paquets, donc 15 fois plus d'échantillons, que des paquets de 1500 octets. Conséquence pour le dimensionnement :

- **la précision** est la plus faible avec les **gros** paquets (moins d'échantillons), on dimensionne donc sur 1500 octets ;
- **le coût** (CPU du switch, bande passante d'export) est le plus élevé avec les **petits** paquets, on le vérifie donc sur 100 octets.

---

## 4. Valeur de sampling recommandée par débit de lien (WAN, paquets de 100 à 1500 octets)

### 4.1 Hypothèses de calcul

| Paramètre | Valeur | Remarque |
|---|---|---|
| Plancher à mesurer | **5 % du débit du lien** | C'est ton cas : 500 kbit/s sur 10 Mbit/s. Un plancher absolu de 500 kbit/s est irréaliste sur les liens rapides (voir 4.4). |
| Fenêtre d'analyse | **5 minutes** | Adapter avec les règles de 4.3. |
| Confiance | 95 %, erreur cible 5 % | n ≥ 1 537 échantillons. |
| Taille de paquet pour la précision | 1500 octets (IP) | Cas le moins favorable. |
| Taille de paquet pour le coût | 100 octets (IP), lien saturé | Cas le plus coûteux. |

Formule appliquée, arrondie à la **puissance de 2 inférieure** :

```
N = puissance_de_2_inférieure( débit_plancher × fenêtre / (8 × 1500 × 1 537) )
```

### 4.2 Tableau

Le « débit du lien » est le débit **effectif** du circuit : si une interface 1G porte un circuit WAN bridé à 100 Mbit/s, prendre 100 Mbit/s.

| Débit du lien (Mbit/s) | Plancher visé (kbit/s) | **Sampling-rate** | Échantillons au plancher (5 min) | Erreur 95 % | Échantillons/s lien saturé, paquets de 100 o |
|---|---|---|---|---|---|
| 1 | 50 | **1:1** | 1 250 | exhaustif | 1 250 |
| 2 | 100 | **1:1** | 2 500 | exhaustif | 2 500 |
| 4 | 200 | **1:2** | 2 500 | 3,9 % | 2 500 |
| 5 | 250 | **1:4** | 1 562 | 5,0 % | 1 562 |
| 10 | 500 | **1:8** | 1 562 | 5,0 % | 1 562 |
| 20 | 1 000 | **1:16** | 1 562 | 5,0 % | 1 562 |
| 50 | 2 500 | **1:32** | 1 953 | 4,4 % | 1 953 |
| 100 | 5 000 | **1:64** | 1 953 | 4,4 % | 1 953 |
| 200 | 10 000 | **1:128** | 1 953 | 4,4 % | 1 953 |
| 500 | 25 000 | **1:256** | 2 441 | 4,0 % | 2 441 |
| 1 000 | 50 000 | **1:512** | 2 441 | 4,0 % | 2 441 |
| 2 000 | 100 000 | **1:1024** | 2 441 | 4,0 % | 2 441 |
| 5 000 | 250 000 | **1:2048** | 3 052 | 3,5 % | 3 052 |
| 10 000 | 500 000 | **1:4096** | 3 052 | 3,5 % | 3 052 |

Lecture :
- **1:4096 n'est la bonne valeur que pour les liens de 10 Gbit/s.** Partout ailleurs, il est trop élevé pour mesurer correctement un flux de 5 % du lien.
- Sur les liens de 1 et 2 Mbit/s, 1:1 est nécessaire. Si le matériel ne le supporte pas, allonger la fenêtre d'analyse à 30 min (voir 4.3) et prendre 1:4 à 1:8.
- Les valeurs sont **par interface** : chaque interface porte son propre taux, et chaque datagramme sFlow transporte le sampling-rate utilisé, qu'ElastiFlow doit appliquer (à valider en phase 0).

### 4.3 Adapter à un autre besoin

| Si tu changes... | Alors le sampling-rate N devient... |
|---|---|
| Fenêtre de 1 min au lieu de 5 min | N ÷ 5 |
| Fenêtre de 30 min au lieu de 5 min | N × 6 |
| Plancher de 1 % du lien au lieu de 5 % | N ÷ 5 |
| Plancher de 10 % du lien | N × 2 |
| Paquets moyens de 500 octets au lieu de 1500 | N × 3 (précision), à vérifier sur le coût |
| Erreur cible de 10 % au lieu de 5 % | N × 4 |

Calcul en Python :

```python
import math

def sampling(link_bps, floor_ratio=0.05, window_s=300, size=1500, n_target=1537):
    """Sampling-rate (puissance de 2) pour atteindre l'erreur cible au plancher."""
    floor_bps = link_bps * floor_ratio
    pkts = floor_bps * window_s / (8 * size)
    n_max = max(1.0, pkts / n_target)
    return 2 ** int(math.floor(math.log2(n_max)))

def erreur_95(link_bps, rate, floor_ratio=0.05, window_s=300, size=1500):
    pkts = link_bps * floor_ratio * window_s / (8 * size)
    n = pkts / rate
    return 196 / math.sqrt(n)

# Exemple : lien de 10 Mbit/s -> 1:8, erreur ~5 %
print(sampling(10e6), round(erreur_95(10e6, sampling(10e6)), 1))
```

### 4.4 Pourquoi un plancher relatif au débit du lien

Un plancher absolu de 500 kbit/s sur un lien de 10 Gbit/s exigerait un sampling d'environ 1:8, soit plus de 100 000 échantillons par seconde au débit maximum : impossible à exporter et à traiter. À l'inverse, une mesure précise d'un flux de 500 kbit/s n'a généralement pas d'intérêt sur un lien de 10 Gbit/s. C'est la raison du plancher à 5 % du lien.

### 4.5 Coût à surveiller

- **CPU du switch** : le pire cas du tableau (lien saturé de paquets de 100 octets) donne 1 250 à 3 050 échantillons par seconde **par interface**. Plusieurs interfaces s'additionnent. Surveiller `display cpu-usage` et les compteurs de `display sflow`. Si le CPU ou l'export sature, remonter N d'un cran.
- **Bande passante d'export** : chaque échantillon pèse environ 150 à 250 octets, soit **2 à 5 Mbit/s** d'export au pire cas. Si le collecteur est joint **par le WAN lui-même**, l'export peut consommer une part notable d'un petit lien. Faire passer l'export par un chemin séparé (gestion, hors bande) ou vérifier que le trafic sFlow est classé en BE.
- **Trafic réel** : les liens ne sont presque jamais saturés de paquets de 100 octets. Le coût réel est typiquement 5 à 10 fois inférieur au pire cas.
- **Plage supportée** : vérifier la plage du `sflow sampling-rate` sur le modèle exact (certains modèles limitent la valeur minimale).

---

## 5. Configuration sFlow Comware

Syntaxe Comware 7, à vérifier avec `display sflow` sur ton modèle :

```
system-view
 sflow agent ip <adresse_loopback>
 sflow collector 1 ip <ip_elastiflow> port 6343
 interface <interface_wan_ou_test>
  sflow flow collector 1
  sflow sampling-mode random
  sflow sampling-rate <N du tableau 4.2>
  sflow flow max-header 128
  sflow counter collector 1
  sflow counter interval 30
```

| Paramètre | Recommandation | Raison |
|---|---|---|
| `sampling-rate` | Selon le tableau 4.2, **par interface** | Le taux nominal dépend du débit du circuit. |
| `sampling-mode` | `random` | Le mode déterministe peut se caler sur la périodicité d'un flux régulier comme iperf et fausser la mesure. |
| `counter interval` | **30 s** (au lieu de 300 s) | 300 s ne donne que 6 points sur 30 min, trop peu pour valider un débit constant. |
| `flow max-header` | 128, à porter à 256 si VLAN empilés ou tunnels | L'en-tête échantillonné doit contenir les en-têtes L2 et L3/L4 pour voir DSCP, ports et flags. |
| `agent ip` | Loopback stable | L'IP exportatrice reste la même quand un lien tombe. |
| NTP | Partout : switchs, client, serveur, ElastiFlow | Indispensable pour caler les fenêtres de 30 min et mesurer la gigue. |

Avant tout test, déterminer le **sens d'échantillonnage** de la plateforme (entrée seulement, ou entrée et sortie). Si les deux sens sont échantillonnés, un même paquet peut être vu deux fois, à l'entrée et à la sortie. Dans ElastiFlow, on regroupe alors par exportateur et par interface (`input`/`output`) pour ne pas compter deux fois.

---

## 6. Architecture du test et points de mesure

```
 [Client Linux]        [Site A : Comware]          Réseau opérateur         [Site B : Comware]        [Serveur Linux]
  iperf -c  ---LAN---> port LAN ──► port WAN =====[ classes EF/AF41/D1..BE ]===== port WAN ──► port LAN ---LAN---> iperf -s
                          ▲ point de mesure A                                     ▲ point de mesure B
                          │ sFlow (entrée LAN)                                    │ sFlow (entrée WAN)
                          └──────────────► ElastiFlow ◄──────────────────────────┘
```

| Point | Ce qu'il voit | Usage |
|---|---|---|
| **Client** | Trafic émis (compteurs nft, éventuel pcap) | Vérité terrain côté émission. |
| **Mesure A** (entrée LAN, site A) | Charge **offerte** avec le marquage d'origine | Valider le débit et le marquage émis. |
| **Mesure B** (entrée WAN, site B) | Ce que **l'opérateur a livré**, avec le marquage reçu | Valider le remarquage et les pertes par classe. |
| **Serveur** | Trafic reçu, pertes, gigue (rapport iperf) | Vérité terrain côté réception. |

L'échantillonnage à l'entrée voit le trafic **avant** les files d'attente de sortie : les pertes par congestion ne sont donc **pas** visibles sur le point A, mais elles se déduisent de l'écart entre A et B, du rapport de pertes du serveur iperf et des statistiques de files du switch.

---

## 7. Partie injection : générer un trafic connu avec iperf2

### 7.1 Principe

L'injection consiste à produire un flux dont **toutes les propriétés sont connues à l'avance** : protocole, ports, débit, taille des paquets, marquage DSCP, durée. Ces propriétés servent ensuite de **clé de recherche** dans ElastiFlow et de **référence de comparaison** avec ce que le collecteur affiche.

Trois règles structurent tous les tests :

1. **Un test = un identifiant = un port source unique.** Le test numéro N utilise le port source `40000 + N` et le port destination fixe `50001`. Dans ElastiFlow, un seul filtre retrouve le flux sans ambiguïté, et deux tests ne se mélangent jamais.
2. **Une vérité terrain exacte pour chaque test**, mesurée indépendamment d'ElastiFlow (compteurs nft, compteurs du switch, rapport du serveur iperf).
3. **Horodatage** de début et de fin de chaque test (epoch), avec 5 minutes de silence entre deux tests.

### 7.2 Préparation

**Serveur distant** (derrière le switch qui exporte, le trafic doit réellement le traverser) :

```bash
sudo firewall-cmd --add-port=50001/udp --add-port=50001/tcp
iperf -s -u -p 50001 -i 10 -e > srv_udp.log 2>&1 &
iperf -s    -p 50001 -i 10 -e > srv_tcp.log 2>&1 &
```

L'option `-e` (iperf2 ≥ 2.1) donne le rapport détaillé : pertes, gigue, nombre de paquets reçus.

**Client** : compteurs nft pour la vérité terrain, un compteur par test (créé automatiquement par le script de 7.7).

**Horloges** : NTP sur le client, le serveur, les switchs et ElastiFlow. Pour mesurer aussi des délais avec `--trip-times`, la synchronisation doit être fine (NTP de qualité ou PTP).

### 7.3 Taille des paquets : de la taille IP à l'option `-l`

En UDP, `-l` est la taille de la **charge utile**. La taille du paquet IP vaut `-l + 28` (20 octets IP + 8 octets UDP), et la trame Ethernet `+ 14` (+ 4 avec un tag VLAN, + 4 de FCS selon les compteurs).

| Paquet IP voulu | `-l` UDP | Trame Ethernet (sans FCS) |
|---|---|---|
| 100 o | 72 | 114 |
| 200 o | 172 | 214 |
| 500 o | 472 | 514 |
| 1000 o | 972 | 1014 |
| 1500 o | 1472 | 1514 |

Ne pas dépasser 1472 en UDP sur un MTU de 1500, sinon les paquets sont fragmentés (la fragmentation fait l'objet d'un test dédié, 7.11).

### 7.4 Commandes de base

```bash
# UDP, débit et taille fixes, port source 40012, DSCP EF
iperf -c SERVEUR -u -p 50001 -b 500k -l 972 -t 1800 -i 10 -e -B IP_CLIENT:40012 -S 0xb8

# TCP, volume exact de 1 Go
iperf -c SERVEUR -p 50001 -n 1G -e -B IP_CLIENT:40050

# TCP, débit borné pendant 30 min (iperf >= 2.1)
iperf -c SERVEUR -p 50001 -b 10M -t 1800 -i 10 -e -B IP_CLIENT:40051
```

Le port source fixé par `-B` est l'**identifiant du test**. Éviter de réutiliser un port source en TCP juste après un test (état TIME_WAIT) : un numéro par test règle le problème.

Pour les tests multi-flux (`-P`), iperf2 ne sait pas attribuer un port source distinct à chaque flux. Lancer à la place **plusieurs processus iperf en parallèle**, chacun avec son port source, par exemple dans une boucle bash :

```bash
for i in 0 1 2 3 4 5 6 7; do
  iperf -c SERVEUR -p 50001 -t 600 -B IP_CLIENT:$((40060 + i)) > log_T$((60 + i)).txt &
done
wait
```

### 7.5 Vérité terrain

| Source | Ce qu'elle donne | Quand l'utiliser |
|---|---|---|
| **Compteurs nft du client** | Paquets et octets IP exacts émis par test | Toujours (léger, exact, valable à tous les débits). |
| **Compteurs nft du serveur** (chaîne `input`) | Paquets et octets IP reçus par test | Toujours, pour mesurer les pertes. |
| **Rapport du serveur iperf** (`-e`) | Datagrammes reçus, pertes, gigue, débit | Toujours. |
| **Compteurs du switch** (`display counters inbound interface ...`, avant et après) | Paquets et octets sur l'interface (tout le trafic) | Sur un port de test dédié, ou en retranchant le trafic de fond. |
| **tcpdump** (`-s 96 -w ref.pcap`) | Le détail paquet par paquet | Uniquement à bas débit (un pcap de 30 min à 1 Gbit/s est énorme). |

Attention à la **base de comptage** : nft compte des **octets IP** (en-tête IP inclus, sans Ethernet), les compteurs du switch des octets de trame, et sFlow rapporte la longueur de trame. L'écart est de 14 à 18 octets par paquet : 1,4 % sur des paquets de 1000 octets, **14 % sur des paquets de 100 octets**. La phase 0 de calibrage détermine la base qu'ElastiFlow additionne.

Mise en place des compteurs nft du client et du serveur :

```bash
# client
nft add table inet t
nft add chain inet t out '{ type filter hook output priority 0; }'
# serveur
nft add table inet t
nft add chain inet t in  '{ type filter hook input  priority 0; }'
nft add rule inet t in udp dport 50001 counter comment "\"srv_udp_total\""
```

### 7.6 Phase 0 : calibrage (indispensable avant la série)

1. Mettre l'interface de test en **sampling 1:1**.
2. Envoyer un flux court (2 min, 500 kbit/s, IP 1000 o, port source dédié).
3. Comparer le total ElastiFlow aux compteurs nft. Comme chaque paquet est échantillonné, le résultat doit être **exact**. Cela révèle trois choses :
   - la **base** additionnée par ElastiFlow (octets IP ou octets de trame) ;
   - si ElastiFlow **applique bien le facteur d'échantillonnage** (relancer ensuite à 1:8 et vérifier que le total reste identique) ;
   - le **décalage temporel** entre la fin du flux et l'apparition des enregistrements.
4. Remettre le sampling nominal de l'interface (tableau 4.2).

### 7.7 Script d'exécution d'un test

```bash
#!/usr/bin/env bash
# run_test.sh ID PROTO RATE LEN TOS DUR
# exemple : ./run_test.sh 12 udp 500k 972 0xb8 1800
set -euo pipefail

ID=$1; PROTO=$2; RATE=$3; LEN=$4; TOS=$5; DUR=$6
SERVER=${SERVER:-10.20.0.2}
CLIENT_IP=${CLIENT_IP:-10.10.0.2}
DPORT=${DPORT:-50001}
SPORT=$((40000 + ID))
OUT=${OUT:-resultats.csv}

# table et chaîne nft créées une seule fois
nft list table inet t >/dev/null 2>&1 || {
  nft add table inet t
  nft add chain inet t out '{ type filter hook output priority 0; }'
}
nft add rule inet t out "$PROTO" sport "$SPORT" counter comment "\"T$ID\""

ARGS=""
if [ "$PROTO" = "udp" ]; then ARGS="$ARGS -u -l $LEN"; fi
if [ "$RATE" != "max" ]; then ARGS="$ARGS -b $RATE"; fi

START=$(date +%s)
iperf -c "$SERVER" $ARGS -p "$DPORT" -t "$DUR" -i 10 -e \
      -B "$CLIENT_IP:$SPORT" -S "$TOS" | tee "log_T$ID.txt"
END=$(date +%s)
sleep 2

LINE=$(nft list table inet t | grep "comment \"T$ID\"")
PKTS=$(echo "$LINE"  | sed -E 's/.*packets ([0-9]+).*/\1/')
BYTES=$(echo "$LINE" | sed -E 's/.*bytes ([0-9]+).*/\1/')

echo "T$ID,$PROTO,$RATE,$LEN,$TOS,$SPORT,$START,$END,$PKTS,$BYTES" >> "$OUT"
echo "T$ID  $START -> $END  paquets=$PKTS  octets_IP=$BYTES"
```

Le fichier `resultats.csv` contient une ligne par test : identifiant, paramètres, début et fin en epoch, paquets et octets IP réellement émis. C'est la **référence** de l'analyse (section 10).

L'outil du dépôt automatise ce script pour tout le plan : `efv injecter` (mêmes commandes iperf2 et nft, groupes
lancés ensemble, pause de 5 min, confirmation de l'opérateur pour les changements de sampling et les préalables,
`--dry-run` pour relire les commandes avant de lancer).

### 7.8 Matrice de tests

Les débits sont donnés **en pourcentage du débit effectif du lien** ; l'exemple chiffré est celui d'un lien de **10 Mbit/s**. Le sampling de l'interface suit le tableau 4.2 (1:8 pour 10 Mbit/s).

**Série A : débit constant, UDP, 30 min, paquets IP de 1000 o (`-l 972`), DSCP 0**

| ID | Débit | Exemple 10 Mbit/s | Objectif |
|---|---|---|---|
| A1 | 1 % du lien | 100 kbit/s | Sous le plancher : mesurer la probabilité de détection. |
| A2 | 5 % du lien | 500 kbit/s | **Plancher visé** : erreur ≤ 5 %. |
| A3 | 10 % | 1 Mbit/s | |
| A4 | 25 % | 2,5 Mbit/s | |
| A5 | 50 % | 5 Mbit/s | |
| A6 | 90 % | 9 Mbit/s | **Plafond** : proche du débit maximum, sans perte attendue. |

Répéter A2, A3 et A5 **trois fois** pour détecter un biais systématique. Refaire A1, A2 et A3 avec le sampling historique 1:4096 pour **chiffrer le problème** avant correction.

**Série B : taille des paquets, UDP, 10 min, 10 % du lien, DSCP 0**

| ID | Paquet IP | `-l` | Paquets/s à 1 Mbit/s |
|---|---|---|---|
| B1 | 100 o | 72 | 1 250 |
| B2 | 200 o | 172 | 625 |
| B3 | 500 o | 472 | 250 |
| B4 | 1000 o | 972 | 125 |
| B5 | 1500 o | 1472 | 83 |

Même débit en bits, mais nombre de paquets très différent : on valide que le volume en octets reste juste, quelle que soit la taille des paquets, et que le **nombre de paquets** estimé suit le nombre réel.

**Série C : TCP**

| ID | Test | Commande (résumé) |
|---|---|---|
| C1 | Volume exact | `-n 1G` : comparer au volume IP réel, TCP incluant les en-têtes et les ACK. |
| C2 | Débit constant 30 min | `-b` à 25 % du lien. |
| C3 | Débit maximum 10 min | 8 processus parallèles, ports sources 40060 à 40067. |

Avec TCP, le sens retour (ACK) apparaît comme un **flux distinct** dans ElastiFlow : on vérifie que les deux sens sont présents, que leurs ports sont symétriques et que les **flags TCP** (SYN au début, FIN à la fin) sont visibles.

**Série E : saturation (60 s maximum), voir 7.10.**

### 7.9 Taux de répétition des paquets

Deux interprétations, les deux sont couvertes :

**a) Cadence de paquets (paquets par seconde).** Le débit en pps se déduit des échantillons : `échantillons × sampling-rate ÷ durée`. À comparer aux compteurs nft. Les séries A et B couvrent ce cas (de 83 à 1 250 pps par Mbit/s selon la taille).

**b) Paquets dupliqués et retransmissions.** sFlow n'échantillonne qu'un paquet sur N : il ne peut **pas** détecter une duplication paquet par paquet. En revanche, il en voit l'**effet en volume** : les paquets dupliqués ou retransmis sont comptés comme du trafic normal. On valide donc en injectant un taux connu :

| ID | Injection (sur une interface de test dédiée) | Mesure de référence | Résultat attendu dans ElastiFlow |
|---|---|---|---|
| D1 | `tc qdisc add dev eth0 root netem duplicate 5%` (UDP à 10 % du lien, 10 min) | Octets nft émis, rapport serveur | Volume ≈ **+5 %** par rapport au volume applicatif iperf. |
| D2 | `tc qdisc add dev eth0 root netem loss 2%` (TCP, 10 min) | `nstat -az TcpRetransSegs` avant et après | Volume supérieur au volume utile (goodput iperf) à hauteur des retransmissions. |

Retirer ensuite la règle : `tc qdisc del dev eth0 root`. `netem` s'applique à toute l'interface : à n'utiliser que sur une interface de test ou pendant une fenêtre sans autre trafic.

À retenir pour l'interprétation : ElastiFlow mesure le **débit réel sur le fil** (« throughput »), pas le débit utile applicatif (« goodput »). L'écart entre les deux mesure justement les répétitions.

### 7.10 Saturation d'une minute

- **Jamais sur un lien de production.** Utiliser un lien de lab ou un port d'accès dédié.
- Offrir **plus que la capacité** : sur un lien de 10 Mbit/s, `-b 15M -t 60`. Avec plusieurs processus iperf si un seul n'atteint pas le débit demandé.
- Passer l'interface de test à **1:1 ou 1:8** pour avoir assez d'échantillons en 60 s, puis revenir au sampling nominal.
- Relever pendant le test : CPU du switch, compteurs `display sflow` (datagrammes envoyés et perdus), pertes dans le rapport du serveur iperf, statistiques de files de sortie.
- Lecture : sFlow à l'entrée voit la charge **offerte** (15 Mbit/s), le serveur voit la charge **livrée** (~10 Mbit/s). L'écart est la perte par congestion, visible dans le rapport iperf et dans les files de sortie, pas dans ElastiFlow seul.

### 7.11 Autres éléments visibles en sFlow

| Test | Commande | Ce qu'on vérifie dans ElastiFlow |
|---|---|---|
| VLAN | Client et serveur dans deux VLAN (routage inter-VLAN) | VLAN source et destination, interfaces d'entrée et de sortie. |
| ICMP et TTL | `ping -i 0.2 -s 1000 -t 64 SERVEUR` | Protocole ICMP (sans ports), TTL si exposé. |
| Flags TCP | Beaucoup de connexions courtes, ou `hping3`/`nping` en quantité connue | SYN, FIN, RST en proportion du nombre de connexions. |
| IPv6 | Mêmes tests en IPv6 | Adresses, classe de trafic (DSCP). |
| Fragmentation | UDP avec `-l 3000` | Les fragments suivants n'ont pas d'en-tête L4 : **pas de ports** dans ElastiFlow. |
| Compteurs d'interface | `display counters rate inbound interface ...` | Débit calculé par ElastiFlow depuis les compteurs vs débit du switch (visible grâce au `counter interval 30`). |

**Outillage (#33).** G2 et G5 sont dans le plan et passent par `efv injecter` et `efv lire` ; les autres restent
des gestes de l'opérateur, notés dans la fiche VAL :

| Test | Outillage | Vérité terrain et lecture |
|---|---|---|
| G1 VLAN | manuel : changer la topologie du banc (deux VLAN, routage) n'est pas un geste scriptable depuis le client | mêmes commandes iperf, VLAN et interfaces lus dans Kibana |
| G2 ICMP | `protocole: icmp` : `ping -c N -i 0.2 -s TAILLE−28 -t 64 -Q TOS -I CLIENT SERVEUR`, 300 s à 1:8 ; ne consomme pas de port source | règle nft `icmp type echo-request counter` ; lecture par IP et protocole (colonnes `ports` et `port_destination` vides) |
| G3 flags TCP | manuel : hping3 ou nping ne sont pas sur le banc de référence, et les flags ne sont pas encore lus (champ à relever en P1) | nombre de connexions noté à la main |
| G4 IPv6 | manuel : le banc n'a pas d'adressage IPv6 de référence ; rejouer un test A avec `--serveur`/`--client` IPv6 quand il existe | idem série A |
| G5 fragmentation | test UDP de 3028 o (`-l 3000`), `cle: ip`, 300 s à 1:8, en fin de plan pour ne pas décaler les ports | nft compte le datagramme avant fragmentation (3028 o) ; le lien porte 2 fragments (1500 + 1548 = 3048 o) dont le second sans ports : lecture par IP et protocole, attendu paquets ≈ 2 × nft, octets ≈ nft + 20 o par datagramme |
| G6 compteurs | manuel : relevé `display counters rate` sur le switch, comparé à l'index des counter samples | saisie à la main dans la fiche |

Une lecture sans ports compte tout le trafic du protocole entre le client et le serveur pendant la fenêtre : ne
rien lancer d'autre entre les deux pendant G2 et G5 (la pause de 5 min entre tests y suffit).

### 7.12 Variante iperf3

> À utiliser seulement si iperf2 n'est pas disponible (Rocky 9 : iperf3 dans les dépôts de base, iperf2
> dans EPEL). Les deux outils ne se parlent pas : client et serveur doivent être du même outil.
> `efv injecter --outil iperf3` produit les commandes ci-dessous.

| Point | iperf2 (référence) | iperf3 |
|---|---|---|
| Port source fixe | `-B CLIENT:PORT` | `-B CLIENT --cport PORT` |
| Serveurs | un `iperf -s [-u] -p 50001` sert tous les processus | un serveur ne sert qu'un client à la fois (« server busy ») : un serveur par processus simultané, ports `50001`, `50002`… (`for p in 50001 50002; do iperf3 -s -p $p -D; done`, liste affichée par `efv injecter`) |
| UDP sans débit | `-b` absent | `-b 0` (sinon 1 Mbit/s par défaut) |
| Cadence UDP | régulière | salves toutes les 1 ms par défaut : `--pacing-timer 100` (100 µs) pour ne pas déclencher un policer (Q2 à Q4) |
| Rapport | texte, `-e` | JSON du client et du serveur (`-J --get-server-output`) : pertes et gigue, pas de latence |
| Connexion de contrôle | aucune | une connexion TCP vers le port du serveur, depuis un port source éphémère |

Exemples sur un lien de 10 Mbit/s (`efv injecter --lien 10M --outil iperf3 --dry-run --lab --test A2 --test Q3`) :

```bash
# sur le serveur : un iperf3 par port de destination (6 tests simultanés dans Q3)
for p in 50001 50002 50003 50004 50005 50006; do iperf3 -s -p $p -D; done
# A2
iperf3 -c 10.20.0.2 -u -l 972 -b 500k -p 50001 -t 1800 -i 10 -B 10.10.0.2 --cport 40004 -S 0x00 \
       --pacing-timer 100 -J --get-server-output
# Q3 (extrait) : les tests du groupe partent ensemble, chacun vers son serveur
iperf3 -c 10.20.0.2 -u -l 472 -b 1M -p 50001 -t 60 -i 10 -B 10.10.0.2 --cport 40049 -S 0xb8 … &
iperf3 -c 10.20.0.2 -u -l 472 -b 2M -p 50002 -t 60 -i 10 -B 10.10.0.2 --cport 40050 -S 0x88 … &
iperf3 -c 10.20.0.2 -u -l 472 -b 2M -p 50003 -t 60 -i 10 -B 10.10.0.2 --cport 40051 -S 0x68 … &
wait
```

Vérité terrain et lecture : les règles nft comptent par **port source** et `efv lire` filtre la plage de
ports source du test, la connexion de contrôle (port source éphémère) est donc écartée des deux côtés sans
règle supplémentaire. La colonne `port_destination` de `resultats.csv` devient une plage (`50001-50008`
pour C3, 8 processus) que `efv lire` interroge par intervalle.

---

## 8. Qualité de service : classes de l'opérateur

### 8.1 Les classes et leur marquage

| Classe opérateur | Usage typique | DSCP | Valeur `-S` d'iperf | Remarque |
|---|---|---|---|---|
| **EF** | Temps réel, voix | 46 | `0xB8` | Voir la note sur « EF 45 » ci-dessous. |
| **AF41** | Multimédia, vidéo interactive | 34 | `0x88` | |
| **D1** | Données prioritaires | 26 (AF31) | `0x68` | **Correspondance à confirmer** auprès de l'opérateur. |
| **D2** | Données intermédiaires | 18 (AF21) | `0x48` | **Correspondance à confirmer.** |
| **D3** | Données moins prioritaires | 10 (AF11) | `0x28` | **Correspondance à confirmer.** |
| **BF** | Best effort, par défaut | 0 | `0x00` | Interprété ici comme « best effort ». |

Calcul de la valeur `-S` : `-S` positionne l'octet TOS, soit `DSCP × 4`. Exemple : EF = 46 × 4 = 184 = `0xB8`.

**À propos de « EF 45 ».** La RFC définit EF avec le DSCP **46**. Si le « 45 » désigne une **part de bande passante** réservée à la classe (par exemple 45 % du débit souscrit), il n'y a rien à changer au marquage. Si l'opérateur impose réellement le DSCP 45 (`0xB4`), remplacer `0xB8` par `0xB4` dans les tests. Le test Q1 envoie les deux valeurs pour voir celle que l'opérateur reconnaît.

**À confirmer par écrit avec l'opérateur** avant de tester (condition du verdict, voir 8.6) : correspondance exacte D1/D2/D3 vers les DSCP, débit garanti (CIR) et débit maximal de chaque classe, taille de rafale tolérée, comportement hors profil (rejet ou remarquage vers une classe inférieure), partage ou non du débit entre classes, traitement des DSCP inconnus.

### 8.2 Ce que l'on valide

| Question | Test |
|---|---|
| Le marquage émis est-il **conservé** de bout en bout ? | Q1 |
| Chaque classe respecte-t-elle son **profil** (dans, à et au-dessus du débit contractuel) ? | Q2 |
| Sous **congestion**, la hiérarchie des classes est-elle respectée ? | Q3 |
| Que devient un trafic **hors profil** ? | Q2 (au-dessus du profil) et Q4 |
| Que fait l'opérateur d'une valeur de marquage **inconnue** ? | Q5 |

### 8.3 Tests

**Q1 : marquage conservé** (10 min par valeur, UDP, 5 % du lien, paquets IP de 500 o soit `-l 472`, un port source par valeur)

| ID | Classe | `-S` |
|---|---|---|
| Q1-1 | EF (46) | `0xB8` |
| Q1-2 | AF41 | `0x88` |
| Q1-3 | D1 (AF31) | `0x68` |
| Q1-4 | D2 (AF21) | `0x48` |
| Q1-5 | D3 (AF11) | `0x28` |
| Q1-6 | BF | `0x00` |
| Q1-7 | DSCP 45 (hypothèse alternative d'EF) | `0xB4` |

Lecture : comparer, par port source, le DSCP vu au **point A** (émis) et au **point B** (livré). Toute différence est un remarquage par l'opérateur.

**Q2 : profil par classe** (5 min par palier, UDP, paquets IP de 500 o)

Pour chaque classe, trois paliers : **50 %**, **100 %** et **150 %** du débit contractuel de la classe. À 150 %, on observe si l'excédent est rejeté ou remarqué. Mesures : pertes et gigue du rapport serveur, DSCP et volume au point B. Pour limiter la durée, commencer par EF, AF41, D1 et BF (4 classes × 3 paliers × 5 min = 1 h).

**Q3 : congestion multi-classes** (60 s, trois répétitions, lab uniquement)

Tous les flux partent simultanément, avec leur port source et leur marquage, de façon à **dépasser** la capacité du lien (130 % environ) :

| Classe | Part du lien offerte |
|---|---|
| EF | 10 % |
| AF41 | 20 % |
| D1 | 20 % |
| D2 | 20 % |
| D3 | 10 % |
| BF | 50 % |

Résultat attendu : EF et AF41 intacts (pertes nulles, gigue basse), pertes croissantes de D1 vers D3, la plus forte perte sur BF. Mesures : rapports iperf par flux (pertes, gigue), volume par DSCP aux points A (offert) et B (livré), statistiques de files du switch.

**Q4 : excès sur la classe prioritaire.** EF à 150 % de son profil pendant 60 s, pendant que BF reste dans son profil. On vérifie que l'excès d'EF ne pénalise pas les autres classes et ce qu'il devient (rejeté ou remarqué).

**Q5 : valeurs inhabituelles** (5 min chacune, 5 % du lien) : CS6 (`0xC0`, contrôle réseau), CS5 (`0xA0`), CS1 (`0x20`), bits ECN seuls (`0x01`, `0x02`, `0x03`). Objectif : savoir si l'opérateur les remet à BF et si ElastiFlow les restitue fidèlement.

### 8.4 CoS 802.1p (priorité dans le tag VLAN)

iperf ne sait pas positionner le CoS. Sur le client, créer une sous-interface VLAN avec une table de correspondance :

```bash
ip link add link eth0 name eth0.100 type vlan id 100 egress-qos-map 0:5
ip addr add 10.10.100.2/24 dev eth0.100 && ip link set eth0.100 up
```

Le CoS apparaît alors dans l'en-tête échantillonné. Vérifier que le switch Comware le conserve ou le convertit en DSCP selon sa politique de confiance (`qos trust`).

### 8.5 Où lire les résultats QoS

| Information | Où |
|---|---|
| Marquage émis | ElastiFlow, point A, champ DSCP (par port source). |
| Marquage livré (donc remarquage) | ElastiFlow, point B, même filtre. |
| Charge offerte par classe | ElastiFlow, point A, somme des octets par DSCP. |
| Charge livrée par classe | ElastiFlow, point B, et rapport du serveur iperf. |
| **Pertes** par classe | Rapport du serveur iperf (champ de pertes) et `display qos queue-statistics interface <port> outbound` : sFlow ne voit pas les pertes en sortie. |
| Gigue et latence | Rapport iperf, `--trip-times` avec horloges synchronisées. |

### 8.6 Confirmation écrite de l'opérateur : condition du verdict QoS

Les tests Q mesurent ce que l'opérateur fait du trafic **sur un lien de test, à un moment donné**. Ils ne
remplacent pas un engagement écrit sur les classes, les débits garantis et le traitement du hors-profil : une
politique peut différer d'un site, d'un circuit ou d'une période à l'autre.

La confirmation écrite de l'opérateur (contrat, annexe technique ou réponse écrite référencée) est donc une
**condition du verdict** sur la QoS (vérification P9 de la phase 0) :

- **avec** elle, les résultats Q sont comparés aux engagements et peuvent conclure « conforme » ou « non conforme » ;
- **sans** elle, les résultats Q sont publiés comme **« observés, non contractuels »** : ils décrivent le comportement
  constaté, sans valeur d'engagement, et le verdict de la section QoS ne peut pas être « validé ».

Questions à envoyer à l'opérateur avant la série (réponse écrite, datée, référencée dans le rapport) :

1. Correspondance exacte des classes **D1, D2 et D3** vers les DSCP (hypothèse du 8.1 : AF31, AF21, AF11).
2. Sens de **« EF 45 »** : DSCP 45 (`0xB4`) imposé au marquage, ou part de bande passante de 45 % (EF restant 46) ?
3. **Débit garanti (CIR)** et débit maximal de chaque classe, taille de rafale tolérée, partage ou non du débit
   inutilisé entre classes.
4. Traitement du trafic **hors profil** par classe : rejet, ou remarquage vers quelle classe ?
5. Traitement des **DSCP inconnus** ou non contractuels (CS6, CS5, ECN seul, Q5) : remis à BF, conservés, rejetés ?
6. Existence d'une **classe inférieure à BF** (scavenger, CS1 testé en Q5-3) et son traitement sous congestion.
7. Le marquage et les profils sont-ils les mêmes sur tous les sites et circuits du contrat ?

Côté qos-audit, la même règle s'applique aux constats de conformité QoS : voir Mathilde-Team/qos-audit#14.

---

### 8.7 Recette de qos-audit : série R

Le banc sert aussi de recette à qos-audit (issue #16, Mathilde-Team/qos-audit#36) : chaque test de la série R
injecte un défaut connu, et le champ `attendu` du plan YAML porte le verdict que qos-audit doit rendre sur la
fenêtre du test (`controle:GRAVITE`, contrôles de `docs/CONTROLES.md` de qos-audit). Profil : `--profil recette`.

| Test | Défaut injecté | Préalable | Verdict attendu |
|---|---|---|---|
| R1 | Voix : port RTP 20000 (`port_destination`), paquets IP de 200 o, TOS 0 | ACL voix de la politique couvrant le port 20000, serveur `iperf -s -u -p 20000` | `marking:CRIT` |
| R2 | EF (46) depuis un port non fiable | Client branché sur un port non déclaré `trusted` | `trust:CRIT` |
| R3 | DSCP 40 (CS5), hors plan | | `dscp_plan:WARN` |
| R4 | EF à 150 % du lien (lab) | | `saturation:CRIT`, `class_overflow:WARN` |
| R5 | Trafic sur la sortie de secours (lab) | Couper la sortie principale | `exit_balance:WARN` |
| R6 | Sampling 1:4096 | Sortie du banc déclarée à 10 Mbit/s dans la politique | `sampling_adequacy:CRIT` (±112 % au plancher) |

La série est en fin de plan : les ports sources des autres tests ne bougent pas. `efv plan` affiche les
verdicts attendus (colonne `attendu`) ; la comparaison automatique avec la sortie de qos-audit relève de #6 et
de qos-audit#36.

### 8.8 Sondes de production : série S

sFlow ne voit ni pertes, ni gigue, ni latence (8.5). Les tests Q les mesurent par classe, mais seulement en lab.
La série S en garde la partie **sans risque pour la production** : un flux léger par classe, **dans le profil**,
relancé périodiquement, pour savoir si la priorisation fonctionne au quotidien (issue #17, qos-audit#29).

| Paramètre | Valeur | Justification |
|---|---|---|
| Classes | EF, AF41, D1, D2, D3, BF (`-S` du 8.1) | Une sonde par classe, port source propre (plan YAML) |
| Débit | 64 kbit/s par classe, 384 kbit/s la passe | Très en dessous du palier « 50 % du CIR » de Q2 pour tout CIR de 128 kbit/s ou plus ; sur un CIR plus petit, réduire `debit` |
| Paquets | IP de 200 o (`-l 172`) | Proche de la voix : la gigue mesurée est celle qu'un flux temps réel subirait |
| Durée | 30 s, les six classes **en même temps** (groupe S) | Comparer les classes au même instant : c'est ce qui révèle une priorisation inversée |
| Période | Une passe toutes les 15 min | 96 passes par jour, 3,3 % du temps ; assez pour couvrir les heures chargées |
| Mesures | Rapport serveur iperf2 (`-e`) : paquets émis et perdus, gigue ; latence avec `--trip-times` si les horloges sont fines (7.2) | |

`efv plan --profil sonde` affiche les commandes ; `efv injecter --profil sonde` lance une passe (planifier par cron
ou timer systemd sur le client, serveur `iperf -s -u -p 50001 -e` permanent).

**Restent réservés au lab** : Q3 (congestion à 130 %), Q4 (excès d'EF), Q2 aux paliers 100 et 150 %, la saturation
d'une minute (7.10) et la série R (R4, R5). Une sonde ne doit jamais charger le lien ni sortir du profil d'une classe.

**Format de sortie** `sondes.csv` (UTF-8, séparateur virgule, une ligne par classe et par passe), lu par
qos-audit (#29) :

| Colonne | Contenu |
|---|---|
| `horodatage` | Début de la passe, ISO 8601 UTC (`2026-10-26T09:15:00Z`) |
| `site`, `sortie` | Noms de la politique qos-audit (`sites[].name`, `exits[].name`) |
| `classe` | EF, AF41, D1, D2, D3 ou BF |
| `dscp` | DSCP émis (46, 34, 26, 18, 10, 0) |
| `debit_bps`, `duree_s` | Débit et durée demandés |
| `paquets_emis`, `paquets_perdus` | Rapport serveur |
| `pertes_pct` | `100 × paquets_perdus / paquets_emis` |
| `gigue_ms` | Gigue du rapport serveur |
| `latence_ms` | Latence moyenne (`--trip-times`), vide si les horloges ne sont pas assez fines |
| `outil` | `iperf2` (ou `iperf3`, voir #32) |

Lecture attendue côté qos-audit : sur une même passe, EF ou AF41 dégradée alors que BF ne l'est pas signale une
**priorisation inversée** ; une dégradation de toutes les classes signale une congestion ou un incident hors QoS.
Les seuils (pertes, gigue) sont ceux du contrat de l'opérateur (8.6) et se fixent dans qos-audit.

## 9. Partie lecture dans ElastiFlow

### 9.1 Principe

Pour chaque test, on interroge ElastiFlow **avec la clé connue** (IP source, IP destination, ports, protocole, exportateur) sur la **fenêtre exacte du test**, on **somme** les octets et les paquets de tous les enregistrements correspondants, et on compare à la référence du fichier `resultats.csv`.

Un point de vigilance de fond : ElastiFlow **reconstruit des flux à partir d'échantillons**. Un flux de 30 minutes n'est pas un enregistrement unique, mais une série d'enregistrements dont les valeurs sont **estimées** (échantillons × sampling-rate). Il faut donc toujours **additionner** sur la période, jamais lire un enregistrement isolé.

### 9.2 Champs à connaître

| Information | Champ ECS standard | Champ spécifique ElastiFlow |
|---|---|---|
| IP source / destination | `source.ip`, `destination.ip` | |
| Ports | `source.port`, `destination.port` | |
| Protocole | `network.transport` (udp, tcp, icmp) | |
| Octets / paquets | `network.bytes`, `network.packets` | |
| Date | `@timestamp` | début et fin du flux dans les champs `event.*` ou `flow.*` |
| Exportateur (le switch) | | champ `flow.export.*` |
| Sampling-rate appliqué | | `flow.sampling_interval` — nom établi (documentation ElastiFlow,
  github.com/robcowart/elastiflow#116 ; aligné dans qos-audit, issue #16 et PR #84) ; à confirmer
  en P4 sur le mapping réel de l'instance, avec sa version d'ElastiFlow |
| Interfaces d'entrée / sortie | | champs `flow.input.*` et `flow.output.*` |
| DSCP / TOS | | champs `flow.ip.dscp*` ou équivalent |
| Flags TCP, VLAN | | champs `flow.tcp.*`, `flow.vlan.*` ou équivalent |

Les noms ECS sont standards. **Les noms spécifiques ElastiFlow dépendent de la version du schéma** : ouvrir un enregistrement du flux de test dans Discover, passer en affichage **JSON**, et relever les noms exacts dans ton index. C'est un travail de 5 minutes à faire une fois, avant la série de tests.

### 9.3 Procédure de lecture d'un test

1. **Filtre.** Dans Discover ou Lens (Kibana ou OpenSearch Dashboards), requête de la forme :
   ```
   network.transport : "udp" and source.port : 40012 and destination.port : 50001
   and source.ip : "10.10.0.2" and destination.ip : "10.20.0.2"
   ```
   Pour QoS, ajouter le filtre sur le champ DSCP. Pour isoler un point de mesure, filtrer sur l'**exportateur** (IP du switch A ou B).
2. **Fenêtre.** Début du test − 1 min jusqu'à fin du test + 5 à 10 min : les enregistrements arrivent avec un **délai d'agrégation** (relevé en phase 0). Ne conclure qu'une fois les enregistrements stabilisés.
3. **Contrôle des métadonnées.** Vérifier sur un enregistrement : exportateur, interface d'entrée, **sampling-rate attendu** (celui de l'interface), DSCP émis. Un sampling-rate incorrect fausse tous les volumes d'un facteur constant.
4. **Somme.** Somme de `network.bytes` et de `network.packets` sur la fenêtre utile (on exclut la première et la dernière minute, montée en charge et fin de flux).
5. **Débit.** `octets × 8 ÷ durée` (en excluant les minutes de bord).
6. **Constance du débit.** Histogramme par minute de la somme des octets : le débit doit être stable. Attention : le nombre d'échantillons **par minute** est bien plus faible que sur 30 min, donc la dispersion par minute est naturellement plus grande (de l'ordre de `196 ÷ √n_minute`). Le critère de 10.1 en tient compte : chaque minute est jugée contre sa propre borne `196 ÷ √n_minute`, avec une tolérance au hasard calculée sur le nombre de minutes (`efv lire --series --stabilite`, #31).
7. **Comparaison.** Estimation ElastiFlow contre référence (octets nft, avec la correction de base établie en phase 0), puis calcul de l'erreur.
8. **Cas multi-points.** Reprendre 1 à 7 pour chaque exportateur (A et B), puis comparer A et B.

La commande `efv lire` automatise les étapes 1, 2, 4 et 5 pour chaque ligne de `resultats.csv` et relève les
métadonnées de l'étape 3 (sampling-rates et DSCP lus, par exportateur) dans `lectures.csv` ; les étapes 6 à 8
relèvent du rapport (`efv rapport`). Elle refuse de démarrer si un champ utilisé n'existe pas dans le mapping
(§9.2 : noms à préciser avec `--champs`).

**Double échantillonnage (§5).** Un paquet prélevé à l'entrée d'un port et à la sortie d'un autre port du même
switch produit deux enregistrements aux interfaces d'entrée et de sortie **identiques** : seules l'interface
qui l'a prélevé (sFlow `source_id_index`) ou la comparaison à nft (volume ≈ 2×) les distinguent. `efv lire`
écrit les octets par couple d'interfaces (`interfaces`) et, si le champ `echantillonneur` est relevé en P4 et
déclaré dans `--champs`, la liste des échantillonneurs et `double_echantillonnage = oui` quand il y en a
plusieurs pour un exportateur (#30, qos-audit#26). Pour l'erreur de volume, ne retenir qu'un échantillonneur
par exportateur : celui du point de mesure. `efv lire` conserve ses octets, paquets et
enregistrements dans `volumes_echantillonneurs` (JSON). `efv rapport --point-mesure sw-a=12`
retient uniquement l'échantillonneur 12 de sw-a, y compris pour `--calibration` ;
sans choix explicite, un double échantillonnage signalé bloque le rapport. Les anciens
CSV ambigus doivent être relus, jamais corrigés en divisant le total par deux.

### 9.4 Quoi lire pour chaque objectif

`efv rapport --series series.csv` recalcule le verdict de constance pour A et C2,
séparément du volume. La jointure inclut l'échantillonneur choisi par `--point-mesure` :
le total de deux points ne peut pas prouver la stabilité d'un seul point. Les minutes
absentes sont rendues avec zéro échantillon ; sans fichier, le critère reste non évalué.
Conserver les mêmes options `--bord` et `--cv` entre lecture et rapport.

| Objectif | Lecture dans ElastiFlow |
|---|---|
| **Justesse du débit** | Somme des octets sur la fenêtre, comparée à la référence ; erreur par rapport à la borne `196 ÷ √n`. |
| **Plancher de visibilité** | Le flux apparaît-il ? Nombre d'enregistrements et d'échantillons (`paquets ÷ sampling-rate`), comparé à l'attendu. Répéter A1 plusieurs fois. |
| **Plafond** | Débit mesuré au plus haut palier (A6, C3) face au débit réel et aux compteurs du switch. |
| **Marquage QoS** | Valeur DSCP par port source, aux deux points de mesure. |
| **Débit par classe** | Somme des octets groupée par DSCP, aux deux points. |
| **Répétition des paquets** | Paquets par seconde ; écart de volume avec le trafic utile dans les tests D1 et D2. |
| **Taille des paquets** | Octets ÷ paquets par flux (série B) : doit retrouver la taille attendue (base trame ou IP selon phase 0). |
| **Sens retour TCP** | Flux inverse (ports permutés), flags TCP. |
| **Compteurs d'interface** | Utilisation de l'interface calculée par ElastiFlow vs `display counters rate`. |

### 9.5 Pièges fréquents

| Piège | Conséquence | Parade |
|---|---|---|
| Lire un seul enregistrement d'un flux long | Valeur très partielle | Toujours sommer sur la fenêtre. |
| Fenêtre trop courte, indexation en retard | Volume sous-estimé | Attendre le délai d'agrégation (phase 0) et relire. |
| Un même paquet vu à l'entrée **et** à la sortie | Volume doublé | Regrouper par exportateur et interface ; ne sommer qu'un point de vue. |
| Base de taille différente (trame vs IP) | Écart constant, important aux petits paquets | Calibrage en phase 0 ; comparer à une référence de même base. |
| Sampling-rate non appliqué ou mal lu | Facteur constant d'erreur | Phase 0 : comparaison à 1:1 puis 1:8. |
| Fragments sans ports | Flux « manquants » dans le filtre par ports | Filtrer par IP et protocole pour la série de fragmentation. |
| Autres trafics sur la même interface | Écart avec les compteurs du switch | Filtrer par port source dans les deux mesures ; ne pas comparer à l'interface entière. |
| Fuseaux horaires, horloges décalées | Fenêtres décalées | NTP partout, vérification des heures affichées. |
| Un seul test sur un flux faible | Écart d'allure aléatoire | Répéter et comparer à la **borne statistique**, pas à zéro. |

---

## 10. Analyse, critères d'acceptation et rapport

### 10.1 Critères d'acceptation

| Critère | Seuil |
|---|---|
| Erreur de volume (tests A2 à A6, B, C) | **Toutes** les mesures dans la borne globale à 95 % `z ÷ √n` (Bonferroni, §10.3 : z ≈ 305 pour 22 mesures), avec n le nombre d'échantillons du test. |
| Plancher (A2) | Erreur ≤ 5 % avec le sampling du tableau 4.2. |
| Débit constant (A, C2) | Sur la fenêtre utile (hors minutes de bord), chaque minute a au moins un échantillon ; son écart à la moyenne des minutes reste sous `f × 196 ÷ √n_minute` avec `f = max(1,5 ; Φ⁻¹(1 − 0,01 ÷ 2m) ÷ 1,96)` (1,82 pour m = 28 minutes) ; au plus k minutes dépassent `196 ÷ √n_minute`, k plus petit entier tel que P(X > k) ≤ 4 % pour X binomiale (m ; 0,05) (k = 4 pour 28 minutes). Risque total de refuser à tort une série stable : ≤ 5 % (1,2 à 2,6 % mesurés en simulation). |
| Absence de biais | Sur 3 répétitions, la moyenne des erreurs est proche de 0 (un biais constant révèle un facteur d'échantillonnage ou une base de taille erronée). |
| Détection au plancher (A1) | Fréquence de détection cohérente avec `1 − exp(−n attendu)`. |
| Marquage (Q1) | 100 % des DSCP émis retrouvés au point A ; au point B, écarts expliqués par la politique de l'opérateur. |
| Hiérarchie des classes (Q3) | Pertes nulles sur EF et AF41, ordre de perte D1 < D2 < D3 < BF. |
| Répétition (D1, D2) | Surplus de volume cohérent avec le taux injecté (à la précision statistique près). |
| Saturation (E) | Charge offerte correcte au point A, pas de perte d'export sFlow, CPU du switch stable. |

### 10.2 Fiche de résultat (à remplir par test)

| Champ | Valeur |
|---|---|
| ID du test | |
| Protocole, débit, taille IP, DSCP | |
| Début / fin (epoch) | |
| Référence : paquets / octets IP (nft) | |
| Rapport serveur : reçus / pertes / gigue | |
| ElastiFlow : exportateur, interface, sampling-rate lu | |
| ElastiFlow : somme paquets / octets | |
| Échantillons attendus `n` | |
| **Erreur observée** (ElastiFlow − référence) ÷ référence | |
| Borne globale à 95 % (Bonferroni, §10.3) | |
| Conforme (oui / non) | |

### 10.3 Calcul de l'erreur

```
n_attendu     = paquets_réels / sampling_rate
m             = nombre de mesures jugées ensemble (erreur et n calculables)
z             = 100 × Φ⁻¹(1 − 0,05 / (2m))          # Bonferroni : ≈ 305 pour m = 22, 196 pour m = 1
borne (%)     = z / sqrt(n_attendu)
erreur (%)    = (octets_elastiflow - octets_référence) / octets_référence × 100
conforme      = |erreur| <= borne
critère rempli = toutes les mesures conformes
```

Pourquoi Bonferroni (#9) : avec une borne individuelle à 95 % (`196 / √n`) et 22 mesures, un ElastiFlow juste sort au
moins une fois de la borne par hasard dans 1 − 0,95²² ≈ 68 % des cas. La correction de Bonferroni ramène ce risque
global d'échec à tort à 5 % au plus. Le seuil z est recalculé automatiquement si le nombre de mesures change
(sous-ensemble minimal, répétitions) : `efv rapport` l'indique dans la conclusion.

### 10.4 Périmètre de validité et revalidation

Une validation porte sur **un lien, un exporteur et une chaîne de collecte donnés**. Elle ne vaut pas, telle quelle,
pour N sites ni pour d'autres modèles. Le rapport et VAL-ELASTIFLOW-001 indiquent donc le périmètre exact :

- modèle, version Comware (firmware) et IP d'agent de **chaque exporteur** testé ;
- interface de mesure et **sens d'échantillonnage** (entrée seule, ou entrée et sortie) ;
- sampling-rate validé par interface ;
- version d'ElastiFlow (Unified Flow Collector) et du stockage interrogé (Elasticsearch ou OpenSearch) ;
- date des tests.

Une autre interface du même exporteur, ou un autre exporteur du même modèle et de la même version, est couvert
s'il est configuré à l'identique (même sampling-rate, même sens, mêmes paramètres sFlow du chapitre 5).

Conditions de **revalidation** :

| Changement | Revalidation |
| --- | --- |
| Mise à jour du firmware Comware d'un exporteur | allégée |
| Mise à jour d'ElastiFlow ou de son stockage (version majeure ou mineure) | allégée |
| Nouveau sampling-rate ou changement de sens d'échantillonnage | allégée |
| Nouveau modèle d'exporteur (ou nouvelle famille Comware) | complète |
| Nouveau type de circuit ou nouvel opérateur (QoS) | complète pour la QoS (section 8), allégée sinon |
| Changement de schéma ElastiFlow constaté (champ absent ou renommé) | allégée, P4 en premier |

La revalidation **allégée** rejoue P4 à P7 (champs, calibrage à 1:1, facteur d'échantillonnage, délai d'agrégation)
et A2 (justesse des volumes) ; elle prend environ 2 h. La revalidation **complète** reprend le protocole entier
(planning en section 11). Le contrôle correspondant côté qos-audit (calibration par exporteur) est suivi dans
Mathilde-Team/qos-audit#35.

---

## 11. Planning indicatif

| Phase | Durée |
|---|---|
| Phase 0 : pré-vérifications et calibrage | 1 h |
| Série A (6 tests de 30 min + répétitions de A2, A3, A5) | 6 à 7 h avec les pauses |
| Série B (5 × 10 min) | 1 h |
| Série C (TCP) | 1 h 30 |
| Série D (répétition) | 30 min |
| Série E (saturation, 3 × 60 s) | 30 min |
| QoS : Q1 (7 × 10 min), Q2 (1 h), Q3 et Q4 (30 min), Q5 | 3 h |
| Autres éléments sFlow (7.11) | 1 h |
| Analyse et rapport | 2 h |

Total d'environ **2 jours**. Pour raccourcir : ramener les tests de 30 min à 10 min pour les débits supérieurs à 50 % du lien (le nombre d'échantillons y est déjà élevé), et ne conserver 30 min que pour A1 à A3.

**Sous-ensemble minimal (environ 1 jour).** Pour produire vite une calibration exploitable par qos-audit, le
profil `minimal` du plan (`efv plan --profil minimal`) ne garde que :

| Bloc | Tests | Ce que qos-audit en tire |
|---|---|---|
| Phase 0 | P1 à P9 | noms de champs, facteur d'échantillonnage, base IP/trame, délai d'ingestion, contexte sFlow |
| Série A | A2, A3 (avec leurs répétitions) | justesse des volumes |
| Série B | B1, B5 | erreur selon la taille des paquets |
| QoS | Q1, Q2, Q3 | DSCP vus, remarquage opérateur, priorisation |
| Gestion | G1, G6 | interfaces d'entrée et de sortie, compteurs à 30 s |

Soit environ 8 h d'injection, pauses comprises, plus la phase 0 hors iperf et l'analyse. Le verdict précise le
**profil exécuté** : un « Validé » obtenu sur le profil minimal ne vaut **pas** validation complète (notamment
séries C, D et E, Q4 à Q6, A1, A4 à A6, B2 à B4 et G2 à G5 non jouées) ; la validation complète reste à faire
avant d'étendre l'usage au-delà de l'audit QoS. Le nombre de dépassements tolérés dépend du nombre de mesures du
profil (règle du verdict global, issue #9).

---

## 12. Annexes

### 12.1 Correspondance DSCP / TOS

| Classe | DSCP | Octet TOS (`iperf -S`) |
|---|---|---|
| BE / BF | 0 | `0x00` |
| CS1 | 8 | `0x20` |
| AF11 (D3) | 10 | `0x28` |
| AF21 (D2) | 18 | `0x48` |
| AF31 (D1) | 26 | `0x68` |
| AF41 | 34 | `0x88` |
| CS5 | 40 | `0xA0` |
| DSCP 45 | 45 | `0xB4` |
| EF | 46 | `0xB8` |
| CS6 | 48 | `0xC0` |
| ECN (2 bits de poids faible) | | `0x01`, `0x02`, `0x03` |

Les correspondances D1, D2 et D3 sont des hypothèses courantes, à confirmer avec le contrat de l'opérateur.

### 12.2 Commandes utiles côté Comware (à adapter au modèle)

```
display sflow
display counters inbound interface <interface>
display counters rate inbound interface <interface>
display qos queue-statistics interface <interface> outbound
display cpu-usage
display clock
```

### 12.3 Check-list avant de lancer la série

- [ ] NTP aligné sur le client, le serveur, les switchs et ElastiFlow.
- [ ] Confirmation écrite de l'opérateur reçue et référencée (questions du 8.6) ; à défaut, résultats Q « observés, non contractuels ».
- [ ] `display sflow` : collecteur joignable, sampling-rate par interface conforme au tableau 4.2, sens d'échantillonnage identifié.
- [ ] Export sFlow sur un chemin qui ne traverse pas le lien testé, ou classé en BE.
- [ ] Noms de champs ElastiFlow relevés sur un enregistrement de test (9.2).
- [ ] Phase 0 de calibrage faite (base de taille, facteur d'échantillonnage, délai d'agrégation).
- [ ] Serveur iperf démarré, pare-feu ouvert, compteurs nft en place des deux côtés.
- [ ] Interface de test dédiée pour `netem` et la saturation.
- [ ] Fenêtre de test réservée et prévenue (saturation, QoS).

### 12.4 Glossaire

| Terme | Définition |
|---|---|
| **Sampling-rate (1:N)** | Un paquet sur N est prélevé par l'agent sFlow. |
| **Échantillon** | Un paquet prélevé, avec son en-tête, exporté vers le collecteur. |
| **Plancher** | Plus petit flux mesurable avec la précision visée. |
| **CIR** | Débit garanti contractuellement (par classe ou par circuit). |
| **Goodput / throughput** | Débit utile applicatif / débit réel sur le fil, retransmissions comprises. |
| **Point A / point B** | Points de mesure sFlow en entrée du site émetteur et du site récepteur. |
