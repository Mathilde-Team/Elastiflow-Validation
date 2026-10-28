# Document de validation : contre-expertise ElastiFlow par injection de trafic sFlow

| Champ | Valeur |
| --- | --- |
| Référence | VAL-ELASTIFLOW-001 |
| Version | 0.1 (brouillon, à compléter pendant les tests) |
| Date de rédaction | 24 octobre 2026 |
| Rédacteur |  |
| Relecteur |  |
| Document lié | Protocole de test : protocole-validation-elastiflow-sflow.md |

## 1. Objet

Ce document consigne la **validation du serveur ElastiFlow** : on envoie un trafic connu (iperf2) à travers des équipements Comware qui exportent du sFlow, puis on compare ce que ElastiFlow affiche à la mesure de référence prise côté client et côté serveur. Il se remplit au fil des tests et se termine par un verdict signé (section 10).

Le mode opératoire (injection, lecture dans ElastiFlow, commandes) est décrit dans le protocole de test. Ce document-ci porte les **critères, les résultats et la décision**.

## 2. Périmètre

Ce qui est validé :

- la justesse du débit mesuré (constant 30 min, UDP et TCP, du plancher au plafond du lien) ;
- le choix du sampling-rate par débit de lien, pour des paquets de 100 à 1500 octets ;
- le marquage QoS de l'opérateur (EF, AF41, D1, D2, D3, BF) et le comportement sous congestion ;
- le taux de répétition des paquets (cadence, duplications, retransmissions) ;
- la saturation (1 minute maximum) ;
- les autres éléments exposés par sFlow : ports, protocole, VLAN, flags TCP, interfaces, compteurs.

Hors périmètre : la configuration de production des équipements (hors modification du sampling des interfaces de test), et les performances du collecteur en charge.

## 3. Environnement testé

| Élément | Caractéristiques à renseigner |
| --- | --- |
| Client iperf | Hôte, OS, version d'iperf, adresse IP : |
| Serveur iperf | Hôte, OS, version d'iperf, adresse IP : |
| Équipement A (site émetteur) | Modèle, version Comware, IP d'agent sFlow, interface de mesure : |
| Équipement B (site récepteur) | Modèle, version Comware, IP d'agent sFlow, interface de mesure : |
| ElastiFlow | Version du collecteur (Unified Flow Collector), version du stockage (Elasticsearch / OpenSearch), IP du collecteur, port sFlow, index interrogé : |
| Circuit WAN | Opérateur, débit effectif du circuit (Mbit/s) : |
| Source NTP | Serveur, écart constaté entre équipements : |
| Sens d'échantillonnage Comware | Entrée seule / entrée et sortie : |
| Dates des tests | Début, fin : |

## 4. Sampling-rate par interface

Hypothèses de calcul : plancher visé = 5 % du débit du lien, fenêtre d'analyse de 5 minutes, erreur cible 5 % à 95 % de confiance (au moins 1 537 échantillons), paquets de 1500 octets pour la précision.

| Débit du lien (Mbit/s) | Sampling recommandé | Erreur attendue au plancher | Sampling actuel | Sampling validé |
| --- | --- | --- | --- | --- |
| 1 | 1:1 | exhaustif |  |  |
| 2 | 1:1 | exhaustif |  |  |
| 4 | 1:2 | 3,9 % |  |  |
| 5 | 1:4 | 5,0 % |  |  |
| 10 | 1:8 | 5,0 % |  |  |
| 20 | 1:16 | 5,0 % |  |  |
| 50 | 1:32 | 4,4 % |  |  |
| 100 | 1:64 | 4,4 % |  |  |
| 200 | 1:128 | 4,4 % |  |  |
| 500 | 1:256 | 4,0 % |  |  |
| 1 000 | 1:512 | 4,0 % |  |  |
| 2 000 | 1:1024 | 4,0 % |  |  |
| 5 000 | 1:2048 | 3,5 % |  |  |
| 10 000 | 1:4096 | 3,5 % |  |  |

Le sampling-rate se règle **par interface**. Le débit à retenir est le débit **effectif du circuit** : une interface 1G qui porte un circuit bridé à 100 Mbit/s se traite comme un lien de 100 Mbit/s.

## 5. Critères d'acceptation

| Critère | Seuil |
| --- | --- |
| Erreur de volume (séries A, B, C) | Dans la borne à 95 % : 196 ÷ √n, avec n le nombre d'échantillons du test |
| Plancher (A2) | Erreur ≤ 5 % avec le sampling recommandé |
| Absence de biais | Sur 3 répétitions, la moyenne des erreurs est proche de 0 |
| Détection au plancher (A1) | Fréquence de détection cohérente avec 1 − exp(−n attendu) |
| Marquage (Q1) | 100 % des DSCP émis retrouvés au point A ; écarts au point B expliqués par la politique de l'opérateur |
| Hiérarchie des classes (Q3) | Pertes nulles sur EF et AF41, ordre de perte D1 < D2 < D3 < BF |
| Répétition (D1, D2) | Surplus de volume cohérent avec le taux injecté, à la précision statistique près |
| Saturation (E1) | Charge offerte correcte au point A, aucun datagramme sFlow perdu, CPU du switch stable |

## 6. Phase 0 : pré-vérifications et calibrage

| ID | Vérification | Résultat | Statut |
| --- | --- | --- | --- |
| P1 | NTP aligné sur client, serveur, switchs et ElastiFlow |  | ☐ OK ☐ KO |
| P2 | `display sflow` : collecteur joignable, sampling par interface conforme, sens d'échantillonnage identifié |  | ☐ OK ☐ KO |
| P3 | ElastiFlow reçoit les datagrammes : exportateur, interface d'entrée et sampling-rate corrects |  | ☐ OK ☐ KO |
| P4 | Noms des champs ElastiFlow relevés sur un enregistrement de test (DSCP, exportateur, interfaces, sampling) |  | ☐ OK ☐ KO |
| P5 | Calibrage à 1:1 : total ElastiFlow = compteurs nft. Base additionnée par ElastiFlow : octets IP / octets de trame |  | ☐ OK ☐ KO |
| P6 | Facteur d'échantillonnage appliqué : même total à 1:1 et à 1:8 |  | ☐ OK ☐ KO |
| P7 | Délai d'agrégation d'ElastiFlow mesuré (minutes entre la fin du flux et l'enregistrement) |  | ☐ OK ☐ KO |
| P8 | Export sFlow hors du lien testé, ou classé en BE |  | ☐ OK ☐ KO |
| P9 | Confirmation écrite de l'opérateur reçue (référence et date) : correspondance des classes, sens de « EF 45 », CIR par classe, hors profil, DSCP inconnus, classe sous BF (protocole 8.6). KO : résultats Q « observés, non contractuels » |  | ☐ OK ☐ KO |

## 7. Fiches de test

Débit effectif du lien testé : \_\_\_\_\_\_\_\_ Mbit/s. Sampling de l'interface pendant la série : 1:\_\_\_\_\_\_.

### 7.1 Série A : débit constant, UDP, 30 min, paquets IP de 1000 octets

| ID | Débit (% du lien) | Débit absolu | Attendu | Mesuré (ElastiFlow) | Statut |
| --- | --- | --- | --- | --- | --- |
| A1 | 1 % |  | Détection probabiliste, sous le plancher |  | ☐ OK ☐ KO |
| A2 | 5 % |  | Plancher : erreur ≤ 5 % |  | ☐ OK ☐ KO |
| A3 | 10 % |  | Erreur dans la borne à 95 % |  | ☐ OK ☐ KO |
| A4 | 25 % |  | Erreur dans la borne à 95 % |  | ☐ OK ☐ KO |
| A5 | 50 % |  | Erreur dans la borne à 95 % |  | ☐ OK ☐ KO |
| A6 | 90 % |  | Plafond : débit proche du maximum, sans perte |  | ☐ OK ☐ KO |

A2, A3 et A5 sont répétés trois fois. Les séries A1 à A3 sont aussi rejouées avec l'ancien sampling 1:4096 pour chiffrer l'écart avant et après correction.

### 7.2 Série B : taille des paquets, UDP, 10 min, 10 % du lien

| ID | Paquet IP | Attendu | Taille moyenne lue (octets ÷ paquets) | Statut |
| --- | --- | --- | --- | --- |
| B1 | 100 o | Volume juste, nombre de paquets cohérent |  | ☐ OK ☐ KO |
| B2 | 200 o | idem |  | ☐ OK ☐ KO |
| B3 | 500 o | idem |  | ☐ OK ☐ KO |
| B4 | 1000 o | idem |  | ☐ OK ☐ KO |
| B5 | 1500 o | idem |  | ☐ OK ☐ KO |

### 7.3 Série C : TCP

| ID | Test | Attendu | Mesuré | Statut |
| --- | --- | --- | --- | --- |
| C1 | Volume exact de 1 Go | Volume IP conforme, SYN et FIN visibles |  | ☐ OK ☐ KO |
| C2 | Débit constant 30 min, 25 % du lien | Erreur dans la borne à 95 % |  | ☐ OK ☐ KO |
| C3 | Débit maximum 10 min (8 processus en parallèle) | Débit proche du lien ; sens retour (ACK) présent |  | ☐ OK ☐ KO |

### 7.4 Séries D et E : répétition des paquets et saturation

| ID | Test | Attendu | Mesuré | Statut |
| --- | --- | --- | --- | --- |
| D1 | Duplication de 5 % (netem), UDP à 10 % du lien, 10 min | Volume ElastiFlow ≈ +5 % du volume applicatif |  | ☐ OK ☐ KO |
| D2 | Perte de 2 % (netem), TCP, 10 min | Volume supérieur au volume utile, à hauteur des retransmissions |  | ☐ OK ☐ KO |
| E1 | Saturation 60 s (offert à 150 % du lien), lab uniquement | Charge offerte vue au point A ; pertes lues dans le rapport iperf et les files du switch ; CPU stable |  | ☐ OK ☐ KO |

### 7.5 QoS : classes de l'opérateur

Correspondance des classes à confirmer **par écrit** avec l'opérateur avant les tests (P9, protocole 8.6). Sans cette confirmation, les résultats de cette section sont **observés, non contractuels**. Référence de la confirmation : \_\_\_\_\_\_\_\_. EF vaut DSCP 46 (octet TOS 0xB8) ; si l'opérateur impose réellement le DSCP 45, l'octet TOS est 0xB4.

**Q1 : marquage conservé (10 min par valeur, 5 % du lien, paquets IP de 500 o)**

| ID | Classe | DSCP émis | TOS iperf | DSCP vu au point A | DSCP vu au point B | Remarquage | Statut |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Q1-1 | EF | 46 | 0xB8 |  |  |  | ☐ OK ☐ KO |
| Q1-2 | AF41 | 34 | 0x88 |  |  |  | ☐ OK ☐ KO |
| Q1-3 | D1 (AF31 supposé) | 26 | 0x68 |  |  |  | ☐ OK ☐ KO |
| Q1-4 | D2 (AF21 supposé) | 18 | 0x48 |  |  |  | ☐ OK ☐ KO |
| Q1-5 | D3 (AF11 supposé) | 10 | 0x28 |  |  |  | ☐ OK ☐ KO |
| Q1-6 | BF | 0 | 0x00 |  |  |  | ☐ OK ☐ KO |
| Q1-7 | DSCP 45 (hypothèse alternative d'EF) | 45 | 0xB4 |  |  |  | ☐ OK ☐ KO |

**Q2 : profil par classe (5 min par palier, paquets IP de 500 o)**

| Classe | Palier | Débit offert | Débit livré (point B) | Pertes | Gigue | Excédent rejeté ou remarqué | Statut |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EF | 50 % du profil |  |  |  |  |  | ☐ OK ☐ KO |
| EF | 100 % |  |  |  |  |  | ☐ OK ☐ KO |
| EF | 150 % |  |  |  |  |  | ☐ OK ☐ KO |
| AF41 | 50 % |  |  |  |  |  | ☐ OK ☐ KO |
| AF41 | 100 % |  |  |  |  |  | ☐ OK ☐ KO |
| AF41 | 150 % |  |  |  |  |  | ☐ OK ☐ KO |
| D1 | 50 % |  |  |  |  |  | ☐ OK ☐ KO |
| D1 | 100 % |  |  |  |  |  | ☐ OK ☐ KO |
| D1 | 150 % |  |  |  |  |  | ☐ OK ☐ KO |
| BF | 50 % |  |  |  |  |  | ☐ OK ☐ KO |
| BF | 100 % |  |  |  |  |  | ☐ OK ☐ KO |
| BF | 150 % |  |  |  |  |  | ☐ OK ☐ KO |

D2 et D3 sont ajoutés en second temps si les résultats de D1 le justifient.

**Q3 : congestion multi-classes (60 s, trois répétitions, lab uniquement)**

| Classe | Part du lien offerte | Livré (% de l'offert) | Pertes | Gigue | Attendu | Statut |
| --- | --- | --- | --- | --- | --- | --- |
| EF | 10 % |  |  |  | Intact | ☐ OK ☐ KO |
| AF41 | 20 % |  |  |  | Intact | ☐ OK ☐ KO |
| D1 | 20 % |  |  |  | Pertes faibles | ☐ OK ☐ KO |
| D2 | 20 % |  |  |  | Pertes moyennes | ☐ OK ☐ KO |
| D3 | 10 % |  |  |  | Pertes élevées | ☐ OK ☐ KO |
| BF | 50 % |  |  |  | Pertes les plus fortes | ☐ OK ☐ KO |

**Q4 et Q5**

| ID | Test | Attendu | Mesuré | Statut |
| --- | --- | --- | --- | --- |
| Q4 | EF à 150 % de son profil pendant 60 s, BF dans son profil | L'excès d'EF ne pénalise pas les autres classes ; devenir de l'excès noté |  | ☐ OK ☐ KO |
| Q5-1 | CS6 (0xC0) | Traitement par l'opérateur noté ; DSCP restitué par ElastiFlow |  | ☐ OK ☐ KO |
| Q5-2 | CS5 (0xA0) | idem |  | ☐ OK ☐ KO |
| Q5-3 | CS1 (0x20) | idem |  | ☐ OK ☐ KO |
| Q5-4 | Bits ECN seuls (0x01, 0x02, 0x03) | idem |  | ☐ OK ☐ KO |
| Q6 | CoS 802.1p (sous-interface VLAN avec egress-qos-map) | Priorité visible dans l'en-tête échantillonné |  | ☐ OK ☐ KO |

### 7.6 Autres éléments exposés par sFlow

| ID | Test | Attendu | Mesuré | Statut |
| --- | --- | --- | --- | --- |
| G1 | Deux VLAN (routage inter-VLAN) | VLAN source et destination, interfaces d'entrée et de sortie |  | ☐ OK ☐ KO |
| G2 | ICMP (`ping -s 1000`) | Protocole ICMP, sans ports |  | ☐ OK ☐ KO |
| G3 | Flags TCP (connexions courtes en nombre connu) | SYN, FIN, RST en proportion du nombre de connexions |  | ☐ OK ☐ KO |
| G4 | IPv6 | Adresses et classe de trafic corrects |  | ☐ OK ☐ KO |
| G5 | Fragmentation (UDP de 3000 o) | Fragments suivants sans ports, noté |  | ☐ OK ☐ KO |
| G6 | Compteurs d'interface (intervalle de 30 s) | Utilisation calculée par ElastiFlow = `display counters rate` du switch |  | ☐ OK ☐ KO |

## 8. Synthèse chiffrée des erreurs

Octets de référence : compteurs nft du client, dans la base établie en P5. Erreur = (ElastiFlow − référence) ÷ référence. Borne à 95 % = 196 ÷ √n, avec n = paquets réels ÷ sampling-rate.

| ID | Octets de référence | Octets ElastiFlow | Échantillons n | Erreur observée | Borne à 95 % | Conforme |
| --- | --- | --- | --- | --- | --- | --- |
| A1 |  |  |  |  |  | ☐ |
| A2 (1) |  |  |  |  |  | ☐ |
| A2 (2) |  |  |  |  |  | ☐ |
| A2 (3) |  |  |  |  |  | ☐ |
| A3 (1) |  |  |  |  |  | ☐ |
| A3 (2) |  |  |  |  |  | ☐ |
| A3 (3) |  |  |  |  |  | ☐ |
| A4 |  |  |  |  |  | ☐ |
| A5 (1) |  |  |  |  |  | ☐ |
| A5 (2) |  |  |  |  |  | ☐ |
| A5 (3) |  |  |  |  |  | ☐ |
| A6 |  |  |  |  |  | ☐ |
| B1 |  |  |  |  |  | ☐ |
| B2 |  |  |  |  |  | ☐ |
| B3 |  |  |  |  |  | ☐ |
| B4 |  |  |  |  |  | ☐ |
| B5 |  |  |  |  |  | ☐ |
| C1 |  |  |  |  |  | ☐ |
| C2 |  |  |  |  |  | ☐ |
| C3 |  |  |  |  |  | ☐ |
| D1 |  |  |  |  |  | ☐ |
| D2 |  |  |  |  |  | ☐ |

**Biais moyen** (moyenne des erreurs de A2, A3 et A5) : \_\_\_\_\_\_\_\_ %. Biais significatif : ☐ non ☐ oui, cause suspectée : \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_.

**Comparaison avant et après correction du sampling** (séries A1 à A3 rejouées en 1:4096 puis au sampling recommandé) :

| Test | Erreur en 1:4096 | Erreur au sampling recommandé |
| --- | --- | --- |
| A1 |  |  |
| A2 |  |  |
| A3 |  |  |

## 9. Anomalies et écarts

| N° | Test concerné | Description de l'écart | Cause identifiée | Action corrective | Statut |
| --- | --- | --- | --- | --- | --- |
| 1 |  |  |  |  | ☐ Ouverte ☐ Close |
| 2 |  |  |  |  | ☐ Ouverte ☐ Close |
| 3 |  |  |  |  | ☐ Ouverte ☐ Close |
| 4 |  |  |  |  | ☐ Ouverte ☐ Close |

## 10. Conclusion et décision

**Synthèse des résultats**

| Domaine | Tests passés / total | Commentaire |
| --- | --- | --- |
| Phase 0 | / 9 |  |
| Série A : débit constant | / 6 |  |
| Série B : taille des paquets | / 5 |  |
| Série C : TCP | / 3 |  |
| Séries D et E : répétition et saturation | / 3 |  |
| QoS (Q1 à Q6) |  | ☐ Contractuel (P9 OK) ☐ Observé, non contractuel (P9 KO) |
| Autres éléments sFlow (G1 à G6) | / 6 |  |

**Sampling-rate retenu** : voir le tableau de la section 4, colonne « Sampling validé ».

**Périmètre de validité** (protocole 10.4) : le verdict ne vaut que pour la configuration ci-dessous. Tout
changement listé dans le tableau de revalidation du protocole impose une revalidation allégée (P4 à P7 + A2) ou
complète.

| Élément | Valeur validée |
| --- | --- |
| Exporteurs (modèle, version Comware, IP d'agent) |  |
| Interfaces et sens d'échantillonnage |  |
| Sampling-rate par interface |  |
| ElastiFlow (collecteur) et stockage : versions |  |
| Date des tests |  |
| Revalidation | ☐ Initiale ☐ Allégée (P4 à P7 + A2) ☐ Complète — motif : |
| Profil exécuté (protocole 11) | ☐ Complet ☐ Minimal (`efv plan --profil minimal`) |

**Verdict**

- ☐ Validé : tous les critères d'acceptation sont atteints.
- ☐ Validé avec réserves : écarts listés en section 9, sans impact sur l'usage prévu.
- ☐ Non validé : un ou plusieurs critères essentiels ne sont pas atteints, actions correctives en section 9.

Sur le profil minimal, le verdict porte la mention « profil minimal » : un « Validé » ne vaut pas validation
complète, et ne couvre que l'usage par qos-audit.

Sans confirmation écrite de l'opérateur (P9 KO), le verdict ne porte pas sur la QoS : les résultats Q sont
rapportés comme « observés, non contractuels ».

**Recommandations** :

1. ***
2. ***
3. ***

**Signatures**

| Rôle | Nom | Date | Signature |
| --- | --- | --- | --- |
| Rédacteur |  |  |  |
| Relecteur |  |  |  |
| Approbateur |  |  |  |

## Annexe : formules et correspondances

**Précision d'un échantillonnage 1:N** (95 % de confiance) :

- n = paquets réels ÷ sampling-rate
- borne (%) = 196 ÷ √n
- échantillons nécessaires pour 5 % : n ≈ 1 537 (20 à 30 % de marge pour des octets avec tailles variables)
- probabilité de détecter un flux : 1 − exp(−n attendu)

**Taille des paquets avec iperf2 (UDP)** : taille IP = option `-l` + 28 octets ; trame Ethernet = taille IP + 14 octets (+ 4 avec un tag VLAN).

| Classe | DSCP | Octet TOS (`iperf -S`) |
| --- | --- | --- |
| BE / BF | 0 | 0x00 |
| CS1 | 8 | 0x20 |
| AF11 (D3 supposé) | 10 | 0x28 |
| AF21 (D2 supposé) | 18 | 0x48 |
| AF31 (D1 supposé) | 26 | 0x68 |
| AF41 | 34 | 0x88 |
| CS5 | 40 | 0xA0 |
| DSCP 45 | 45 | 0xB4 |
| EF | 46 | 0xB8 |
| CS6 | 48 | 0xC0 |
