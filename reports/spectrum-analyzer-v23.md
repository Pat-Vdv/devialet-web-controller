# Analyseur de spectre stéréo V2.3

- **Date** : 2026-10-27
- **Périmètre** : visualisation uniquement. Pas d'égaliseur, pas de réglage de tonalité ; aucun réglage des Phantom n'est touché.
- **État** : implémenté et testé (122 tests automatisés). Vérifié de bout en bout sur le matériel réel le 2026-10-27 : musique jouée en Bluetooth depuis `DESKTOP-EXAMPLE` vers la paire « My Phantom System ».

---

## 1. Faisabilité : WASAPI Loopback sur la sortie Bluetooth

| Vérification | Résultat |
|---|---|
| Sonde `scripts/loopback-probe.ps1` (lancée par l'utilisateur) | `Haut-parleurs (My Phantom System)`, 44 100 Hz, 2 canaux, float 32 bits (WAVEFORMATEXTENSIBLE, masque 0x3) ; 3,01 s capturées ; L et R à −9,9 dBFS RMS et −0,1 dBFS crête ; `RESULT: loopback OK` |
| Agent réel de bout en bout (serveur de test sur le port 8766) | Capture de « Haut-parleurs (My Phantom System) », sortie par défaut ; **25,1 trames/s** ; niveaux gauche et droite différents ; capture lancée quand un navigateur regarde, **arrêtée** dès qu'il part |

**Conclusion** : la capture réelle est possible, sans pilote, sans installation et sans droits administrateur.

---

## 2. Architecture

```
Windows                                              WSL (Ubuntu 24.04)
┌──────────────────────────────────────┐            ┌──────────────────────────────────────┐
│ DevialetSpectrumAgent.exe (C#)       │  WebSocket │ FastAPI  devialet_web                │
│  WASAPI loopback « My Phantom System »          │  JSON      │  SpectrumHub (spectrum.py)           │
│  → FFT 4096 Hann, 2 × 10 bandes      │ ─────────▶ │   /api/spectrum/agent  (local seul)  │
│  → 25 trames/s (2/s en silence)      │ ◀───────── │   /api/spectrum/ws     (navigateurs) │
│  échantillons : jamais hors du       │  control   │   GET/POST /api/spectrum             │
│  processus, jamais sur disque        │            └──────────────┬───────────────────────┘
└──────────────────────────────────────┘                           │ WebSocket (≤ 30/s, dernière valeur)
                                                    Navigateur ────┘ canvas 60 FPS (requestAnimationFrame)
```

- **Agent** : C# 5 compilé par `csc.exe` du .NET Framework 4, présent dans Windows. Il n'y a aucun SDK, téléchargement ni service à installer. Fichiers : `agent/windows/SpectrumCore.cs` (analyse), `Wasapi.cs` (capture), `Agent.cs` (programme), `SelfTest.cs`, `build.cmd`.
- **Sens de connexion** : c'est l'agent qui se connecte au backend (`ws://localhost:8000/api/spectrum/agent`). Les requêtes Windows vers `localhost:8000` arrivent dans WSL avec l'adresse `127.0.0.1`, comme vérifié dans le journal d'accès. Le backend n'a donc jamais besoin d'atteindre Windows.
- **Pilotage** : le backend envoie `{"type":"control","capture":true|false}`. La capture n'est demandée que si l'analyseur est **activé** et qu'**au moins un navigateur** regarde. Sinon, l'agent libère le périphérique audio.

---

## 3. Analyse (agent)

| Paramètre | Valeur |
|---|---|
| Format | Celui du mixeur, décodé : float 32/64 bits, PCM 16, 24 et 32 bits (WAVEFORMATEXTENSIBLE compris). Une source mono est dupliquée sur L et R et signalée à l'interface |
| Fenêtre | Hann, FFT radix-2 de **4 096** points (≈ 93 ms à 44,1 kHz ; résolution de 10,8 Hz, utile pour la bande de 31 Hz) |
| Cadence | Une analyse toutes les `rate / 25` échantillons (1 764 à 44,1 kHz), soit **25 trames/s** (option `--fps`, de 5 à 30) |
| Bandes | Octaves centrées sur 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k et 16k Hz (bornes f/√2 à f·√2). La première commence après la composante continue ; la dernière s'étend jusqu'à Nyquist |
| Normalisation | Énergie de la bande (théorème de Parseval, corrigée de la fenêtre), en dB **relatifs à une sinusoïde pleine échelle** : une sinusoïde à 0 dBFS centrée dans une bande lit 0,0 dB, et une à −20 dBFS lit −20,0 dB (autotests) |
| Plancher | −90 dB ; une trame entièrement au plancher est marquée `silent` |
| Silence | En loopback, Windows **n'envoie aucun paquet** quand rien ne joue. Après 100 ms sans paquet, l'agent insère des zéros : les barres retombent pour de vrai. En silence, il n'envoie que 2 trames/s |
| Discontinuités | Le premier paquet après le démarrage est normalement marqué et ignoré. Ensuite, chaque discontinuité **vide la fenêtre d'analyse**, pour ne pas assembler deux morceaux de signal non contigus, et le compteur est remonté à l'interface |
| Changement de périphérique | Sortie retirée, Bluetooth déconnecté ou `AUDCLNT_E_DEVICE_INVALIDATED` : l'état passe à `no_device` et l'agent réessaie toutes les 2 s. Si « My Phantom System » n'est plus la sortie par défaut de Windows, un avertissement s'affiche (vérification toutes les 5 s) |
| Charge processeur | 2 FFT de 4 096 points × 25 par seconde, plus une interrogation toutes les 10 ms : négligeable (non mesuré précisément) |

Message envoyé, environ 200 octets :
```json
{"type":"levels","seq":42,"l":[-43.0,-38.0,…],"r":[-47.9,-35.8,…],"silent":false,"rate":44100}
```
Les nombres sont formatés avec une **culture invariante**, car la locale française de Windows écrirait `-43,0`.

---

## 4. Backend (`devialet_web/spectrum.py`)

- **Un seul agent à la fois** : une nouvelle connexion ferme l'ancienne, dont les messages tardifs sont ignorés.
- **Accès à `/api/spectrum/agent`** réservé à la boucle locale (`127.0.0.1`, `::1`). Une connexion portant un en-tête `Origin`, donc venant d'une page web, est refusée (code 1008).
- **Validation** : exactement 10 nombres finis par canal, ramenés à [−90, +6] dB. Un message de plus de 4 Ko ou de type inconnu (par exemple des échantillons) est rejeté. Seuls `seq`, `l`, `r` et `silent` sont relayés.
- **Débit vers les navigateurs** : au plus 30 trames/s par navigateur, dans un emplacement « dernière valeur » ; un navigateur lent ne crée pas de retard.
- **Détection d'arrêt** : la fermeture du WebSocket est vue immédiatement. Un agent figé (pas de message depuis 5 s, alors que l'agent envoie un signe de vie toutes les 2 s) est signalé par un contrôle toutes les secondes, et la capture lui est retirée.
- **Disponibilité** : `available` + `reason` en français (« Analyseur désactivé », « Agent Windows non connecté », « Agent Windows sans signe de vie », « Sortie audio introuvable : … », « Erreur de capture : … »).
- **Arrêt du serveur** : `capture: false` est envoyé à l'agent, qui libère le périphérique puis se reconnecte quand le serveur revient. Côté agent, la reconnexion se fait avec une attente croissante de 1 à 5 s.

## 5. Interface (`static/spectrum.js`, `static/spectrum-ballistics.js`)

- **Style** : afficheur fluorescent sur fond noir, 30 segments par barre (2 dB chacun), dégradé du cyan vers le vert, segments éteints visibles en transparence, graduations de 0 à −60 dB, fréquences sous les barres, groupes **L** et **R** séparés.
- **Animation** : `requestAnimationFrame` (60 FPS), indépendante des 25 trames/s reçues ; suspendue quand l'onglet est caché. Montée immédiate, descente d'environ 33 dB/s. **Peak hold de 800 ms**, puis chute accélérée (gravité). Le pic est un segment lumineux indépendant, avec un halo.
- **Adaptation** : `ResizeObserver` et `devicePixelRatio`. Le panneau peut être plus large que la colonne principale (jusqu'à 960 px).
- **Commandes** :
  - Activer / Désactiver : réglage côté serveur ; le choix est mémorisé dans le navigateur et rétabli après un redémarrage du serveur.
  - Peak hold.
  - Stéréo / Mono : en mono, moyenne en puissance de L et R ; mode forcé si la source est mono.
- **Indisponibilité** : écran figé à zéro, avec « Analyse audio indisponible » et la raison, ou « Analyseur désactivé ». **Aucune animation simulée.** Si les données s'interrompent plus de 1,5 s, les barres retombent.
- Le navigateur se reconnecte tout seul au backend, avec une attente croissante de 1 à 5 s.

## 6. Tests

| Domaine | Où | Nombre |
|---|---|---|
| FFT (comparée à une DFT naïve), taille invalide | `SelfTest.cs` | 2 |
| Bandes : sinusoïde pleine échelle dans chacune des 10 bandes (0,0 dB, bandes non adjacentes < −60 dB) | `SelfTest.cs` | 10 |
| Calibrage −20 dBFS, séparation gauche/droite (deux cas), source mono | `SelfTest.cs` | 4 |
| Silence (plancher et indicateur), retour au silence, discontinuité, cadence de 25/s, JSON sous locale `fr-FR`, échappement, contrôle | `SelfTest.cs` | 7 |
| Montée, descente, peak hold de 800 ms, chute accélérée, réarmement, peak hold désactivé, silence, onglet caché, remise à zéro, échelle en dB | `tests/js/spectrum-ballistics.test.cjs` (`node --test`) | 10 |
| Validation, accès local uniquement, pilotage de la capture, relais, aucun échantillon relayé, débit, déconnexion et **reconnexion**, remplacement d'agent, agent figé, périphérique absent ou retrouvé, arrêt, REST, WebSocket de bout en bout | `tests/test_web_spectrum.py` | 17 |
| Garde statique : l'agent n'écrit pas sur disque et n'ouvre aucun port | `tests/test_spectrum_frontend_and_agent.py` | 1 |

Les autotests C# (23) et les tests JavaScript (10) sont lancés depuis `pytest`, avec un test de compilation pour l'agent. Ils sont ignorés automatiquement hors WSL ou sans Node.

**Total `pytest` : 122 réussis** (les 102 existants inclus). Aucun test n'utilise les Phantom ni un périphérique audio.

## 7. Limites connues

- **Décalage avec le son** : le Bluetooth ajoute environ 150 à 300 ms entre Windows et les enceintes, et l'analyse porte sur le signal **avant** son envoi. Le spectre est donc légèrement en avance sur le son entendu. Une temporisation réglable dans l'interface est possible si c'est gênant.
- **Ce qui est mesuré** : le mixage Windows envoyé à « My Phantom System ». Une source jouée sur les Phantom par un autre appareil (iPhone, AirPlay) n'apparaît pas.
- **Sécurité** : `/api/spectrum/ws` et `POST /api/spectrum` sont accessibles comme le reste de l'API (sans authentification, comme la V1). Seul l'endpoint de l'agent est limité à la boucle locale.
- **Démarrage** : l'agent se lance à la main. Le démarrage automatique (tâche planifiée) reste à faire, comme étudié dans `hifi-controller-v23.md` §5.
- **Rendu** : vérifié par les tests et par un flux réel de bout en bout, mais pas encore par une capture d'écran de la page.
