# Égaliseur graphique 10 bandes : réalisation (V2.4.3-B)

- **Base** : [`graphic-eq-validation-v243a2.md`](graphic-eq-validation-v243a2.md). T0–T5 ont réussi ; le plan de réalisation est au §8.
- **Règles** :
  - `config.txt` n'est jamais écrit automatiquement ;
  - ne pas toucher aux autres réglages audio de Windows, à Equalizer APO, au réseau ni aux Phantom ;
  - aucun commit (le dossier n'est d'ailleurs pas un dépôt Git).

Légende : ✅ vérifié · 🟡 probable · 🧪 à tester.

## B1 : préparation de la configuration

### 1. Constats

| Point | Valeur | Statut |
|---|---|---|
| Dossier de configuration | `C:\Program Files\EqualizerAPO\config` (registre `HKLM\SOFTWARE\EqualizerAPO\ConfigPath`) ; vu de WSL : `/mnt/c/Program Files/EqualizerAPO/config` | ✅ |
| Droits | `BUILTIN\Utilisateurs` en contrôle total `(OI)(CI)(F)` : le backend écrit sans élévation | ✅ `icacls` |
| Directive d'inclusion | `Include: <fichier>`, chemin relatif au dossier `config`. Elle figure dans l'exemple livré `multichannel.txt` (`Include: demo.txt`) | ✅ |
| Portée du périphérique | `Device: My Phantom System` s'applique aux lignes qui suivent, y compris au fichier inclus | ✅ (même mécanisme que T0–T5) |
| Format des filtres | `Filter: ON PK Fc <f> Hz Gain <g> dB Q 1.41`, précédé de `Preamp: <p> dB` | ✅ T1–T4 (±0,3 dB) |
| Rechargement | À chaud, en une à deux secondes après l'écriture | ✅ V3, T0–T5 |
| Remplacement atomique | Écriture d'un fichier temporaire dans le même dossier, puis `os.replace` : fonctionne sur `/mnt/c` (testé dans `%TEMP%`, rien de résiduel) | ✅ |
| Fichier inclus absent | Aucun filtre appliqué, son neutre, pas de coupure | ✅ V5 |
| Ligne invalide | La ligne est ignorée, les autres sont appliquées | 🟡 → le backend n'écrit que du texte généré et validé |
| Encodage | ASCII et fins de ligne CRLF, comme les fichiers de test qui ont fonctionné | ✅ |

### 2. Fichiers préparés (`work/eq-apo/`)

**`config.txt`** (nouveau contenu, à mettre en place à la main) :
```
# Equalizer APO - Devialet Web Controller V2.4.3-B
# Reglages dans devialet-eq.txt (ecrit par le controleur web).
# Sauvegarde de la version precedente : config.txt.avant-v243b
Device: My Phantom System
Include: devialet-eq.txt
```
`Device: My Phantom System` reste dans `config.txt` : c'est vous qui contrôlez à quelle sortie s'applique l'égaliseur, et le backend ne peut pas élargir cette portée.

**`devialet-eq.txt`** (neutre, structure définitive) :
```
# Devialet Web Controller - egaliseur 10 bandes (V2.4.3-B)
# Fichier ecrit par le controleur web : ne pas modifier a la main.
# devialet-state: {"enabled": true, "preset": "flat", "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
Preamp: 0 dB
Filter: ON PK Fc 31 Hz Gain 0 dB Q 1.41
… (10 lignes, 31 Hz à 16 kHz, toutes à 0 dB)
```
- Les **10 filtres à 0 dB** ont été mesurés comme transparents (T1, ±0,2 dB).
- La ligne `devialet-state` porte l'état complet (actif, préréglage, gains). Le fichier Equalizer APO est ainsi **la seule source de vérité** et sert de persistance : il n'y a pas de second système de configuration. Le backend relit cette ligne au démarrage ; si elle est absente ou invalide, il **n'écrit rien** et signale l'état « non géré ».
- Égaliseur **désactivé** : `Preamp: 0 dB` sans aucun filtre (l'état reste dans la ligne `devialet-state`).

**`devialet-eq-test-1k.txt`** : identique, avec 1 kHz à −12 dB. Il sert uniquement à prouver que l'inclusion est bien suivie (V3).

**`backup/config.txt.2026-10-29`** : copie de votre `config.txt` actuel (neutre), faite en lecture seule.

### 3. Stratégie de préamplification

Préampli = −(maximum réel de la réponse cumulée + **1 dB de marge**), seulement si ce maximum dépasse 0 dB. Il est calculé sur la réponse réelle des 10 filtres qui se chevauchent, pas sur la valeur des curseurs.

| Réglage | Maximum de la réponse | Préampli | Borne stricte (Σ\|h\|) |
|---|---|---|---|
| 1 kHz +12 seul | +12,0 dB | −13,0 dB | +13,6 dB |
| Rock (prototype) | +5,9 dB | −6,9 dB | +11,3 dB |
| Toutes bandes +12 | +18,4 dB | −19,4 dB | +26,9 dB |
| Toutes bandes −12 | −1,1 dB | 0 dB | **+6,2 dB** |

Limite honnête : compenser le maximum de la réponse garantit qu'**aucune fréquence n'est amplifiée** en régime établi. Cela **ne garantit pas** l'absence de dépassement de crête, car les déphasages des filtres peuvent faire monter une crête transitoire. La seule garantie mathématique est la borne Σ|h|, qui dépasse 0 dB même pour une simple coupe. Elle imposerait 6 à 27 dB de perte permanente, ce qui est inutilisable.

Retenu :
- marge de 1 dB au lieu de 0,5 dB dans le prototype ;
- plage limitée à ±12 dB ;
- préampli affiché dans l'interface ;
- la musique réelle est en général bien en dessous de 0 dBFS : la crête du bruit rose de test est à −6,3 dBFS.

Le risque résiduel est documenté, et non nié.

Le niveau perdu à cause du préampli ne sera **jamais** compensé automatiquement par le volume des Phantom.

## Procédure manuelle de mise en place 🧑

(dans un invite de commandes Windows, sans droits administrateur ; le bruit rose peut tourner en boucle sur « My Phantom System » pour les mesures)

```
set S=\\wsl.localhost\<distribution>\home\<utilisateur>\<projet>\work\eq-apo
set C=C:\Program Files\EqualizerAPO\config
```
1. **Sauvegarde** : `copy "%C%\config.txt" "%C%\config.txt.avant-v243b"`
2. **Fichier inclus d'abord**, pour que l'inclusion ne pointe jamais vers un fichier absent : `copy /Y "%S%\devialet-eq.txt" "%C%\devialet-eq.txt"`
3. **Nouveau `config.txt`** : `copy /Y "%S%\config.txt" "%C%\config.txt"`

### Vérification

| # | Action | Attendu |
|---|---|---|
| V1 | `type "%C%\config.txt"` | Les 5 lignes ci-dessus |
| V2 | Moi : lecture depuis WSL et mesure de 20 s | `IDENTIQUE` à T0 (neutre) |
| V3 | `copy /Y "%S%\devialet-eq-test-1k.txt" "%C%\devialet-eq.txt"` ; moi : mesure | Coupe audible ; `APRÈS`, 1 kHz ≈ −8,8 dB → **l'inclusion fonctionne** |
| V4 | `copy /Y "%S%\devialet-eq.txt" "%C%\devialet-eq.txt"` ; moi : mesure | Son d'origine ; `IDENTIQUE` à T0 |
| V5 (optionnel) | `ren "%C%\devialet-eq.txt" devialet-eq.off` ; moi : mesure ; puis `ren "%C%\devialet-eq.off" devialet-eq.txt` | Son neutre, sans coupure : confirme le comportement « fichier absent » pour le diagnostic B5 |

### Résultats (2026-10-29) ✅

La mise en place a été faite par vous. Lors du premier essai, la copie de `config.txt` ne s'était pas faite ; elle a été refaite avec le chemin complet (« 1 fichier(s) copié(s) »).

| # | Résultat | Écart max |
|---|---|---|
| V1 | `config.txt` identique à la version préparée (vérifié depuis WSL et PowerShell) | — |
| V2 | Inclusion du fichier neutre : `IDENTIQUE` à T0 | 0,3 dB |
| V3 | Coupe 1 kHz via `devialet-eq.txt` : audible ; 1 kHz −8,8 / −8,8 attendu, `APRÈS` → **l'inclusion est suivie** | 0,2 dB |
| V4 | Retour au fichier neutre : `IDENTIQUE` à T0 | 0,4 dB |
| V5 | `devialet-eq.txt` absent : son **neutre, sans coupure** (502 trames continues), `IDENTIQUE` à T0 → le comportement 🟡 est vérifié ✅ | 0,1 dB |

Conséquence pour B5 : un fichier absent est **silencieux pour l'oreille**, puisque l'égaliseur cesse simplement d'agir. Le diagnostic doit donc vérifier que le fichier est présent et lisible.

État final :
- `config.txt` = inclusion ;
- `devialet-eq.txt` = neutre ;
- sauvegarde `config.txt.avant-v243b` présente.

### Retour arrière

`copy /Y "%C%\config.txt.avant-v243b" "%C%\config.txt"`. `devialet-eq.txt` n'est alors plus lu ; vous pouvez le supprimer avec `del "%C%\devialet-eq.txt"`. Une autre copie de sauvegarde existe côté WSL : `work/eq-apo/backup/config.txt.2026-10-29`.

En urgence : Paramètres → Son → My Phantom System → Améliorations audio : Désactivé.

---

## B2 : backend

### 1. Fichiers

| Fichier | Rôle |
|---|---|
| `devialet_web/graphic_eq.py` (nouveau) | Service `GraphicEq` : lecture, analyse, rendu, préampli, écriture atomique, verrou, diagnostic |
| `devialet_web/app.py` | Modèles Pydantic `EqGainsBody`, `EqBandBody`, `EqBandsBody`, `EqResetBody`, `EqRevision` ; 8 routes `/api/eq…` ; paramètre `graphic_eq` de `create_app` |
| `devialet_web/__main__.py` | Option `--eapo-config` (variable `DEVIALET_EAPO_CONFIG`, défaut `/mnt/c/Program Files/EqualizerAPO/config`, chaîne vide = égaliseur désactivé) |
| `tests/test_graphic_eq.py` (nouveau) | 63 tests, uniquement dans des dossiers temporaires |

Paramètres DSP repris de `prototypes/eq10.py`, sans réécriture :
- bandes, plage −12 à +12 dB par pas de 0,5 dB, Q 1,41 ;
- fonctions `validate_gains` et `max_boost_db` (filtre RBJ, celui qu'utilise `Filter: ON PK`) ;
- la seule valeur modifiée est la marge, portée à 1 dB (décision B1) ;
- le calcul se fait à 44,1 kHz, le format de « My Phantom System ».

**Sécurité des tests** : sans paramètre `graphic_eq`, `create_app` n'a **pas** d'égaliseur. Seul `__main__` construit le service avec le chemin réel, si bien qu'aucun test ne peut écrire dans Program Files. J'ai vérifié par empreinte SHA-256 que `config.txt` et `devialet-eq.txt` de production étaient identiques avant et après chaque série de tests.

### 2. Comportement

| Opération | Effet sur `devialet-eq.txt` | Réglages mémorisés |
|---|---|---|
| Modifier une ou plusieurs bandes | Filtres et préampli régénérés | Mis à jour ; préréglage `flat` si tout est à 0, sinon `custom` |
| **Désactiver** | `Preamp: 0 dB`, **aucun filtre** (neutre, comme V5) | **Conservés** dans `devialet-state` |
| **Activer** | Filtres et préampli restaurés | Inchangés |
| **Réinitialiser** | 10 filtres à 0 dB, préampli 0 (octet pour octet le fichier B1) | **Remis à 0**, égaliseur activé |
| Bandes modifiées pendant la désactivation | Reste neutre | Mis à jour, appliqués à la réactivation |
| Aucune différence | **Pas d'écriture** | — |

**Démarrage** : rien n'est écrit. Chaque requête relit le fichier, qui est la seule source de vérité. Le fichier B1 en production est reconnu comme géré et cohérent ✅ (vérifié en lecture par l'API réelle).

**Cohérence** :
- les lignes actives (`Preamp`, `Filter`) doivent être exactement celles que génèrent les métadonnées ;
- les espaces et les commentaires ajoutés sont tolérés ;
- un filtre modifié, ajouté ou supprimé à la main donne l'état `inconsistent`, et le fichier est refusé et **non modifié** ;
- l'absence de métadonnées, des métadonnées invalides ou en double donnent l'état `unmanaged`.

Une seule opération peut remplacer un tel fichier : `POST /api/eq/reset` avec `{"force": true}`. Elle est explicite et sauvegarde d'abord l'ancien contenu dans `devialet-eq.unmanaged.bak`.

**Fichier absent** : l'état devient `missing`. Les modifications sont refusées ; seule la réinitialisation recrée le fichier neutre.

### 3. Écriture atomique et concurrence

1. Les valeurs sont validées avant de prendre le verrou.
2. Le verrou `asyncio.Lock` est pris pour toute la séquence : relire, vérifier, écrire, relire.
3. Le contenu complet est généré, en ASCII avec des fins de ligne CRLF.
4. Il est écrit dans `.devialet-eq.<aléa>.tmp`, **dans le même dossier**, avec `flush` et `fsync`.
5. `os.replace` remplace le fichier cible.
6. Le fichier est relu et comparé octet pour octet. En cas de différence, l'erreur `write_failed` est renvoyée.
7. En cas d'échec, le fichier temporaire est supprimé. Le nouvel état n'est renvoyé qu'après une relecture conforme.

**Dossier réel** : sonde faite avec des fichiers temporaires seulement. `mkstemp`, `fsync`, le remplacement d'un fichier existant par `os.replace` (4,5 ms) et le `fsync` du dossier fonctionnent sur `/mnt/c/Program Files/EqualizerAPO/config`. Il n'est resté aucun résidu, et les fichiers de production sont inchangés ✅.

Deux réserves :
- sur `/mnt/c` (le système de fichiers 9P de WSL), `os.replace` correspond à un renommage NTFS avec remplacement. C'est atomique pour un lecteur, mais la durabilité en cas de coupure de courant dépend de Windows ;
- le verrou protège un seul processus serveur. La règle « pas de second serveur sur le port 8000 » évite la concurrence entre processus.

**Révision** :
- chaque réponse contient `revision`, une empreinte du fichier ;
- une requête de modification peut la renvoyer : si le fichier a changé entre-temps, la réponse est `409 conflict` ;
- même sans révision, chaque modification part du fichier relu, donc deux changements de bandes simultanés **ne s'annulent jamais**.

Le test de concurrence (10 bandes modifiées en parallèle) échoue lorsqu'on retire le verrou : je l'ai vérifié par mutation.

### 4. Préampli

`safety_preamp_db` = −(maximum de la réponse réelle cumulée + 1 dB), uniquement si ce maximum dépasse 0 dB. Il vaut 0 dB lorsque toutes les bandes sont à 0 ou qu'il n'y a que des coupures. Exemples testés :
- 1 kHz à +12 dB → −13 dB ;
- toutes les bandes à +12 dB → −19,4 dB ;
- 1 kHz et 2 kHz à +6 dB → moins de −7,5 dB, plus que le plus grand curseur.

Le préampli **appliqué** (`preamp`) vaut 0 lorsque l'égaliseur est désactivé. `safetyPreamp` indique celui qui s'appliquera à la réactivation. Les limites face aux crêtes transitoires sont au §3 de B1. Le volume des Phantom n'est jamais modifié.

### 5. API

Toutes les réponses de réglage ont la même forme :
```json
{"status": "ok", "available": true, "message": null,
 "bands": [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
 "min": -12, "max": 12, "step": 0.5,
 "enabled": true, "preset": "flat", "gains": [0, …], "preamp": 0, "safetyPreamp": 0,
 "revision": "c724691f056ac8fd", "file": "/mnt/c/…/devialet-eq.txt", "processing": "unverified"}
```

| Route | Corps | Rôle |
|---|---|---|
| `GET /api/eq` | — | État. Les problèmes de fichier font partie de la réponse (`status` ≠ `ok`, `available: false`) et ne sont pas des erreurs HTTP |
| `PUT /api/eq` | `{"gains": [10 valeurs], "enabled"?: bool, "revision"?}` | Remplace les 10 bandes |
| `PATCH /api/eq/bands` | `{"bands": {"1000": -3, "4000": 2.5}, "revision"?}` | Modifie plusieurs bandes |
| `PATCH /api/eq/bands/{fréquence}` | `{"gain": -3, "revision"?}` | Modifie une bande |
| `POST /api/eq/enable` / `disable` | `{"revision"?}` facultatif | Active ou désactive en conservant les réglages |
| `POST /api/eq/reset` | `{"force"?: false, "revision"?}` facultatif | Remet tout à 0 dB |
| `GET /api/eq/status` | — | Diagnostic détaillé (ci-dessous) |

Gains : nombres stricts (`3` ou `2.5`) ; `"3"`, `true` et NaN sont refusés.

Erreurs : `{"detail": {"code": …, "message": …}}`.

| Code | HTTP | Cas |
|---|---|---|
| `invalid` | 422 | Valeur hors plage, hors pas de 0,5 dB, bande inconnue (Pydantic renvoie aussi 422 pour un type invalide) |
| `missing` | 409 | `devialet-eq.txt` absent |
| `unmanaged` | 409 | Métadonnées absentes ou invalides |
| `inconsistent` | 409 | Filtres différents des métadonnées |
| `conflict` | 409 | Révision périmée |
| `unavailable` | 503 | Dossier Equalizer APO introuvable |
| `disabled` | 503 | Serveur lancé sans égaliseur (`--eapo-config ""`) |
| `unreadable` / `permission` / `write_failed` | 500 | Lecture impossible, accès refusé, échec d'écriture ou de relecture |

`GET /api/eq/status` rend chaque point séparément, **sans jamais conclure que le traitement est actif** :

| Champ | Contenu |
|---|---|
| `configDir` | Dossier présent, accessible en écriture (indication de `os.access`) |
| `include` | `config.txt` lu (jamais écrit) : `present` / `absent` / `missing` / `unreadable`, et la sortie (`Device`) qui précède l'inclusion |
| `file` | État de `devialet-eq.txt` (`ok` / `missing` / `unmanaged` / `inconsistent` / …) |
| `agent` | Agent Windows connecté et état de capture |
| `device` | `available` si l'agent capture une sortie dont le nom contient celui de la ligne `Device:` de `config.txt` (par exemple « My Phantom System ») ; `unknown` si l'analyseur est désactivé ou l'agent absent |
| `processing` | Toujours `unverified` : seules l'écoute ou une mesure de l'analyseur le confirment. B5 précisera la procédure |

### 6. Sécurité

- Aucun chemin ni texte Equalizer APO n'est accepté depuis HTTP. Les champs inconnus sont ignorés (testé avec `file` et `config`).
- Le nom du fichier écrit est fixe, vérifié par une assertion avant chaque écriture. `config.txt` n'est jamais ouvert en écriture (testé après toutes les opérations).
- Seules des valeurs numériques validées produisent des lignes `Filter`.
- Accès réseau : celui de l'existant, sans authentification. Le serveur écoute sur `0.0.0.0` dans WSL2 en mode NAT, donc il n'est joignable que depuis ce PC. À ne pas exposer au-delà, ni sur Internet.

### 7. Tests

| Suite | Résultat |
|---|---|
| `tests/test_graphic_eq.py` | **63 réussis** : métadonnées, format, préampli, une ou plusieurs bandes, activer/désactiver/réinitialiser, persistance après redémarrage simulé, fichier absent, non géré, incohérent, permissions (dossier en lecture seule, fichier illisible), échec de remplacement, relecture différente, atomicité, concurrence, révision, diagnostic, API et codes d'erreur, absence d'entrée de chemin |
| Python complet | **323 réussis** (260 + 63), aucune régression sur le volume, BASS/TREBLE, Bluetooth ni l'analyseur |
| JavaScript | **69 réussis** (5 suites) |
| API réelle, lecture seule | `GET /api/eq` → `ok`, neutre ; `GET /api/eq/status` → inclusion présente, `Device: My Phantom System`, `processing: unverified` ; fichiers de production inchangés |

Ces tests valident le logiciel, **pas** le son. La validation audio est la procédure ci-dessous.

### 8. Validation manuelle sur les Phantom 🧪

Elle n'est lancée qu'avec votre accord. Préparation :
- bruit rose en boucle sur « My Phantom System », volume bas ;
- agent connecté et analyseur activé.

Les requêtes `curl` sont envoyées depuis WSL (par moi, avec votre accord) ; vous écoutez à chaque étape.

| # | Requête | Vérification fichier | Écoute | Mesure (20 s) |
|---|---|---|---|---|
| 1 | `GET /api/eq` | `status ok`, gains à 0 | — | `IDENTIQUE` à T0 |
| 2 | `PATCH /api/eq/bands/1000 {"gain": -12}` | Ligne `Fc 1000 Hz Gain -12 dB`, préampli 0 | Son creux | `APRÈS`, 1 kHz ≈ −8,8 dB |
| 3 | Lecture de `devialet-eq.txt` | Métadonnées et filtres cohérents | — | — |
| 4 | `POST /api/eq/disable` | Aucun filtre, gains conservés dans les métadonnées | Son d'origine | `IDENTIQUE` à T0 |
| 5 | `POST /api/eq/enable` | Coupe à 1 kHz restaurée | Son creux | `APRÈS`, 1 kHz ≈ −8,8 dB |
| 6 | `POST /api/eq/reset` | Fichier identique au fichier B1 | Son d'origine | `IDENTIQUE` à T0 |

En option, une amplification avec préampli : `PATCH /api/eq/bands/4000 {"gain": 6}`, puis contrôle `--preamp -7 --pk 4000 6 1.41`, puis réinitialisation.

### 9. Résultats de la validation réelle (2026-10-29) ✅

Conditions de la validation :
- bruit rose en boucle sur « My Phantom System » ;
- agent connecté, capture de « Haut-parleurs (My Phantom System) » ;
- 20 s par mesure, soit environ 500 trames.

Les requêtes ont été envoyées depuis WSL vers l'API B2, et chaque modification audible a été acceptée par vous avant d'être lancée.

Seul `devialet-eq.txt` a été modifié, toujours par l'API. Après le redémarrage du serveur, l'analyseur a été réactivé par `POST /api/spectrum`, car il s'agit d'un réglage de l'application, non conservé d'un démarrage à l'autre.

| # | Requête | Fichier | Écoute | Mesure | Écart max |
|---|---|---|---|---|---|
| 1 | `GET /api/eq`, `GET /api/eq/status` | `ok`, neutre, `flat`, révision `c724691f…` ; inclusion présente (`My Phantom System`) | — | `IDENTIQUE` à T0 (bruit rose relancé après une première mesure silencieuse) | 0,2 dB |
| 2–3 | `PATCH /api/eq/bands/1000 {"gain": -12, "revision": …}` → 200 | Métadonnées `custom`, `-12` ; `Filter: ON PK Fc 1000 Hz Gain -12 dB Q 1.41` ; préampli 0 (coupure seule) | Son creux ✅ | `APRÈS`, 1 kHz **−8,8 / −8,8** | 0,3 dB |
| 4 | `POST /api/eq/disable` → 200 | `enabled: false`, gains conservés, **aucun filtre**, `Preamp: 0 dB` | Son d'origine ✅ | `IDENTIQUE` | 0,3 dB |
| 5 | `POST /api/eq/enable` → 200 | Filtre 1 kHz −12 dB **restauré depuis les métadonnées** | Son creux ✅ | `APRÈS`, 1 kHz **−8,8 / −8,8** | 0,1 dB |
| 6 | `POST /api/eq/reset` → 200 | `flat`, 10 × 0 dB, **identique octet pour octet au fichier B1** | Son d'origine | `IDENTIQUE` à T0 | 0,2 dB |

État final :
- `devialet-eq.txt` neutre et identique au fichier B1 ;
- `config.txt` **inchangé** (empreinte SHA-256 identique avant et après) ;
- aucun fichier temporaire ni de sauvegarde résiduel ;
- `/api/eq/status` : fichier `ok`, inclusion `present`, agent `capturing`, sortie `available`, traitement `unverified` (comme prévu, il n'est jamais déclaré automatiquement).

Non-régression après la validation :
- 323 tests Python et 69 tests JavaScript réussis ;
- `/api/tone` disponible et cohérent (BASS 0, TREBLE +4, inchangés) ;
- `/api/state` sans erreur, leader 192.0.2.11 ;
- aucune réponse 409, 422 ni 500 dans le journal du serveur.

**Conclusion B2** : l'API pilote réellement le son envoyé aux Phantom. La modification d'une bande, la désactivation sans perte des réglages, la réactivation et la réinitialisation se comportent comme spécifié, entendues et mesurées à ±0,3 dB.

**Suite** : B3, l'interface graphique, n'a pas commencé ; elle attend votre accord.

---

## B3 : interface graphique

### 1. Fichiers

| Fichier | Rôle |
|---|---|
| `devialet_web/static/index.html` | Carte « Égaliseur graphique » placée **sous** BASS–VOLUME–TREBLE et au-dessus de la sortie Bluetooth ; scripts `eq-core.js` puis `eq.js` après `tone-core.js` |
| `devialet_web/static/eq-core.js` (nouveau) | Logique pure, testée avec node : bandes, normalisation au pas de 0,5 dB, afficheurs (« +6 », « −1,5 », « 0 »), position sur l'échelle, état du panneau à partir de `/api/eq` |
| `devialet_web/static/eq.js` (nouveau) | Partie DOM : construit les 10 curseurs, les relie à `/api/eq`, relit l'état toutes les 4 s |
| `devialet_web/static/tone-core.js` | Le coalesceur `createToneSync` accepte deux options, `bands` et `normalize`, pour servir aussi aux 10 bandes. Comportement par défaut **inchangé** (17 tests d'origine, plus un test dédié) |
| `devialet_web/static/app.css` | Section « E. Graphic equalizer », règles responsives à 760 et 420 px, animation de mise sous tension |
| `tests/js/eq-core.test.cjs` (nouveau) | 11 tests |
| `tests/test_eq_ui.py` (nouveau) | 10 vérifications statiques : structure, accessibilité, câblage, jetons de thème, fichiers servis |

### 2. Ergonomie

- **10 curseurs verticaux**, de −12 à +12 dB par pas de 0,5 dB. Ce sont des curseurs natifs tournés de −90°, ce qui donne :
  - le clavier : flèches ±0,5 dB, Page ↑/↓, Début/Fin ;
  - le tactile et l'accessibilité (`aria-label` « Égaliseur 1 kHz », `aria-valuetext` avec la valeur appliquée) ;
  - **aucune capture de la molette**, si bien que le défilement de la page ne modifie jamais un curseur.
- **Échelle commune** (+12, +6, 0, −6, −12), graduations tous les 3 dB et **ligne 0 dB plus claire** sur chaque glissière. L'échelle est décalée d'un demi-capuchon pour s'aligner sur le centre du capuchon (vérifié en capture : +6, 0, −12 et +2,5 tombent juste).
- **Afficheur numérique** sous chaque curseur, au style des afficheurs BASS/TREBLE. Il montre la valeur **relue dans le fichier**, et la cible pendant l'envoi, dans une autre couleur.
- **Double-clic** : la bande revient à 0 dB.
- **Bouton Activé/Désactivé** de style chaîne hi-fi, avec une LED et `aria-pressed`. Désactivé, le son est neutre, les curseurs restent réglables et une ligne d'information (en couleur atténuée, pas en rouge) le rappelle.
- **Préampli** affiché en permanence (« −7,8 dB »). Une fois l'égaliseur désactivé, l'info-bulle indique le préampli qui s'appliquera à la réactivation.
- **Réinitialiser** : grisé quand tout est déjà à 0.
- **Envoi** : par le coalesceur de BASS/TREBLE. Un seul envoi part à la fois, espacé d'au moins 200 ms ; les mouvements faits entre-temps sont regroupés en un seul `PATCH /api/eq/bands` et la dernière valeur l'emporte. Activer, désactiver ou réinitialiser attendent d'abord que les mouvements en file soient envoyés.
- **Aucun rechargement de page** : la relecture toutes les 4 s reprend une modification faite ailleurs (autre onglet).
- **Fichier en défaut** : curseurs verrouillés et message explicite pour `missing`, `unmanaged`, `inconsistent`, `unavailable`, `disabled`, ou serveur injoignable. Seul « fichier absent » permet de réinitialiser, ce qui recrée le fichier neutre. Le traitement n'est **jamais** annoncé comme actif (B5).
- **Aide** : « Agit sur le son de Windows envoyé en Bluetooth à « My Phantom System », pas sur les autres sources ». Les réglages BASS/TREBLE des Phantom, eux, s'appliquent à toutes les sources.

### 3. Thèmes et responsive

- **Aucune couleur nouvelle** : la carte reprend le panneau `.tone` (aluminium brossé) et ses jetons. Les LED, afficheurs, graduations et bordures viennent de `--tone-led`, `--tone-digit`, `--display-bg`, `--panel-border` et des autres jetons du panneau.
- Les seules couleurs codées en dur sont les gris neutres du capuchon métallique et des ombres ; un test vérifie qu'il n'y en a pas d'autre.
- Vérifié en capture pour les quatre thèmes (Technics, Pioneer, Vintage Amber, Classic) ainsi que pour les états désactivé et fichier absent.

| Largeur | Débordement horizontal | Afficheurs tronqués |
|---|---|---|
| 1280 px | 0 | 0 |
| 760 px | 0 | 0 |
| 390 px | 0 | 0 |
| 360 px | 0 | 0 |

Sous 420 px, l'échelle est plus étroite, le curseur fait 24 px de large et les afficheurs sont plus petits ; les contrôles passent à la ligne.

**Correction en cours de route** : la première version utilisait `writing-mode: vertical-lr`. Avec un curseur entièrement restylé, Edge plaçait tous les capuchons en haut. Le passage au curseur horizontal tourné de −90° a corrigé le problème (vérifié en capture).

### 4. Essai de bout en bout (Edge headless)

Il a tourné contre un serveur de démonstration : enceintes simulées et égaliseur dans un **dossier temporaire**, sans les Phantom ni Equalizer APO. Le script modifie le curseur 1 kHz puis clique sur les boutons, et relit `/api/eq` après chaque action.

| Action | Résultat |
|---|---|
| Curseur 1 kHz à −6 puis −7,5 (rapidement) | Un seul envoi, `gains[1 kHz] = −7,5`, préréglage `custom`, afficheur « −7,5 » |
| Clic sur Activé | `enabled: false`, −7,5 conservé, `aria-pressed=false`, pastille « Désactivé » |
| Clic sur Désactivé | `enabled: true`, −7,5 restauré |
| Clic sur Réinitialiser | 10 × 0, activé, bouton grisé, préampli « 0 dB » |

### 5. Tests

- Python : **333 réussis** (323 + 10).
- JavaScript : **80 réussis** (69 + 11).
- Les fichiers de production d'Equalizer APO n'ont pas changé pendant B3 (`devialet-eq.txt` toujours identique au fichier B1).

### 6. Point d'attention pour B4

À cause du préampli, un changement d'état peut **remonter brusquement le niveau**. Par exemple, avec +6 dB sur une bande, le préampli vaut −7 dB : désactiver ou réinitialiser remonte de 7 dB toutes les autres bandes, d'un coup. Ce n'est pas dangereux pour le matériel, puisque le son ne dépasse jamais le niveau d'origine, mais c'est perceptible.

À traiter en B4 avec les préréglages. Deux pistes :
- une transition par paliers, avec quelques écritures successives espacées de 100 à 150 ms ;
- ou au minimum un avertissement lorsque le saut dépasse 6 dB.

> **Traité en B4** : avertissement avec confirmation au-delà de 6 dB, préréglages limités à 5,8 dB de saut, pas de transition par paliers. Voir [`graphic-eq-presets.md`](graphic-eq-presets.md).

### 7. Validation à faire par vous 🧪

L'interface est servie par le serveur réel, il suffit de recharger la page. **Agir sur un curseur modifie le vrai `devialet-eq.txt`, donc le son.** Suggestion :
1. 1 kHz à −12 : le son se creuse, comme en B2 ;
2. Désactivé puis Activé ;
3. Réinitialiser.

Vérifiez aussi les quatre thèmes (sélecteur en haut) et l'affichage sur votre téléphone.
