# Égaliseur graphique 10 bandes : étude de faisabilité (V2.4.3-A)

- **Date** : 2026-10-28
- **Nature** : étude, plus un prototype logiciel isolé (`prototypes/eq10.py`).
- **Aucune modification** de Windows, aucune installation (ni Equalizer APO, ni pilote, ni périphérique virtuel), aucune commande aux Phantom.
- **Constat Windows**, relevé en lecture seule dans le registre :
  - Windows 11 Professionnel, build 26300 ;
  - sorties actives : « Haut-parleurs (My Phantom System) » en Bluetooth et « Haut-parleurs (Realtek(R) Audio) » ;
  - Equalizer APO n'est **pas** installé.

Légende : ✅ vérifié · 🟡 documenté ou déduit, non mesuré ici · 🧪 à tester après accord.

## 0. Conclusion

| | |
|---|---|
| **Recommandation** | **A. Equalizer APO** sur la sortie « My Phantom System », piloté par un fichier de configuration que notre agent Windows écrit. C'est la seule solution qui agit sur **toutes** les applications, sans latence ajoutée notable et sans pilote à écrire. Elle se désinstalle proprement |
| **Condition préalable** | Un test réel (§6) : installation manuelle d'Equalizer APO, sélection de « My Phantom System », vérification à l'oreille et avec l'analyseur. Rien n'est garanti avant ce test : la compatibilité Bluetooth sous Windows 11 a été corrigée dans les versions 1.3 et 1.4.2 🟡 |
| **Solution de repli, sans installation** | D1 : passage par la sortie Realtek existante, puis DSP dans l'agent, puis rendu vers « My Phantom System ». Faisable, mais plus fragile (dérive d'horloge, latence d'environ 60 à 120 ms en plus) |
| **À écarter** | B seul (capturer puis rejouer le son) : il **double** le son, puisque l'original continue de jouer sur « My Phantom System ». C (pilote virtuel) : dépendance noyau tierce, sans avantage sur A pour notre usage |

## 1. Comparaison des solutions

### A. Equalizer APO (logiciel libre, GPL)

| Point | Constat |
|---|---|
| Principe | Effet audio (APO) chargé par le moteur audio de Windows (`audiodg.exe`) sur la sortie choisie. Il traite tout ce qui y est joué : Chrome, YouTube, lecteurs, système 🟡 |
| Installation | Installateur à lancer **avec droits administrateur**. Le Configurator demande les sorties à équiper ; on coche **« My Phantom System »** uniquement. Un **redémarrage** est demandé 🟡 |
| Windows 11 et Bluetooth | La version 1.3 corrige le Bluetooth sous Windows 11 (installation en SFX/MFX par défaut dans ce cas, car l'EFX ne fonctionne pas). La 1.4.2 corrige GraphicEQ et Convolution, qui fonctionnaient souvent mal en Bluetooth sous Windows 11 🟡. Sinon, le menu « Troubleshooting options » permet de choisir SFX/EFX, SFX/MFX ou LFX/GFX |
| Pilotage depuis notre application | Par **fichier texte** : `config.txt` contient `Include: devialet\eq.txt`, et notre agent écrit `eq.txt`. Il n'y a pas d'API |
| Sans redémarrage | Les fichiers du dossier `config` sont relus automatiquement après modification 🟡 (vrai pour `config.txt` ; à vérifier pour un fichier inclus, d'où un sous-dossier de `config`) |
| Droits | `C:\Program Files\EqualizerAPO\config` est protégé. Il faut donner **une seule fois** à l'utilisateur le droit d'écrire dans `config\devialet\` (commande `icacls` ci-dessous, en administrateur). Ensuite, l'agent écrit sans élévation |
| Limites et risques | Une mise à jour de Windows ou du pilote Bluetooth peut désactiver l'APO : il faut relancer le Configurator. Une installation par sortie : un nouveau périphérique n'est pas couvert. Le réglage « Améliorations audio » de Windows doit rester actif pour cette sortie |
| Maintenance | Projet actif (1.4.2). Désinstallation par « Ajout/suppression de programmes », qui retire l'APO de toutes les sorties |
| Latence et CPU | Filtres IIR (biquads) : pas de latence ajoutée hormis le temps de groupe des filtres ; CPU négligeable (10 biquads × 2 canaux à 48 kHz) |

### B. DSP dans l'agent Windows (WASAPI)

| Point | Constat |
|---|---|
| Capture | Possible : loopback, ou capture par processus (Application Loopback, Windows 10 2004+) ✅ |
| Restitution | Possible : rendu WASAPI vers « My Phantom System » ✅ |
| **Problème bloquant** | Le son original est **déjà** joué sur « My Phantom System ». Rejouer une version égalisée donne les deux superposés, et WASAPI ne permet pas de retirer le flux original des autres applications. Rendre vers la sortie que l'on capture crée en plus une **boucle** (larsen numérique) |
| Conclusion | Inutilisable seul. Utilisable seulement si les applications jouent **ailleurs** que sur « My Phantom System » : c'est la solution D1 |

### C. Périphérique audio virtuel (VB-CABLE, Virtual Audio Cable…)

| Point | Constat |
|---|---|
| Routage | Les applications jouent vers le câble virtuel (sortie par défaut) ; l'agent le capture, égalise, puis rend vers « My Phantom System » |
| Dépendances | **Pilote noyau tiers** à installer, en partagiciel ou payant. Écrire notre propre pilote demanderait une signature : hors de portée |
| Latence | Capture + traitement + rendu : environ 30 à 100 ms ajoutés, plus le Bluetooth (déjà 150 à 250 ms) 🟡. Gênant pour la vidéo YouTube (décalage avec l'image) |
| Fiabilité | Dérive d'horloge entre le câble et la sortie Bluetooth, à compenser par rééchantillonnage. Si l'agent s'arrête, il n'y a **plus de son** |
| Conclusion | Possible, mais plus lourd et plus risqué que A, sans avantage pour nous |

### D. Autres solutions applicables

| | Principe | Avis |
|---|---|---|
| **D1. Sortie Realtek existante comme intermédiaire** | Sortie par défaut de Windows sur « Realtek », **coupée** ; l'agent capture son loopback (pris **avant** le volume et la coupure, par défaut ✅), égalise, puis rend vers « My Phantom System » | Aucune installation, réversible (on remet « My Phantom System » par défaut). Mais : dérive d'horloge entre deux cartes, latence en plus, plus de son si l'agent s'arrête, et la sortie Realtek doit rester active (prise débranchée = sortie inactive 🟡). **Repli expérimental** seulement |
| D2. Extension Chrome (Web Audio) | Égalise l'audio des onglets Chrome | Chrome uniquement, hors objectif « toutes applications ». Écarté |
| D3. « Améliorations audio » de Windows 11 | Aucun égaliseur graphique natif sous Windows 11 ; les effets dépendent du pilote, et le Bluetooth n'en propose pas 🟡 | Inexistant |

## 2. Points techniques

| Point | A. Equalizer APO | D1. Agent via Realtek |
|---|---|---|
| Sortie « My Phantom System » | Oui, si l'installation en SFX/MFX fonctionne sur ce Bluetooth 🧪 | Oui (rendu WASAPI) |
| Chrome reste la source | Oui, rien ne change pour Chrome | Oui, mais vers la sortie Realtek |
| Toutes les applications | Oui | Oui, celles qui suivent la sortie par défaut |
| **Analyseur (WASAPI loopback)** | Voir ci-dessous 🧪 | L'agent peut alimenter l'analyseur avec le signal **après** égalisation |
| Latence ajoutée | Négligeable | Environ 60 à 120 ms 🟡 |
| CPU | Négligeable | Faible (agent C#) |
| Qualité | Traitement en float 32 bits par Windows | Float 32 bits, rééchantillonnage pour la dérive d'horloge |
| Écrêtage | Préampli négatif automatique (§3) | Idem, plus un limiteur dans l'agent |
| Conservation | Fichier persistant, appliqué même sans notre application | Préférences de l'agent ; aucun effet si l'agent est arrêté |
| Déconnexion puis reconnexion Bluetooth | L'APO est lié à la sortie « My Phantom System » et revient avec elle 🧪 | L'agent doit détecter le retour de la sortie et rouvrir le flux |
| Redémarrage de Windows | Appliqué dès l'ouverture de session | L'agent doit démarrer avec la session |
| Changement de sortie | Seule « My Phantom System » est égalisée (`Device: My Phantom System`) | L'agent ne gère que « My Phantom System » |

**Place de l'égalisation par rapport à la capture de l'analyseur.** Ce n'est **pas supposé** ; voici ce que l'on sait :
- la capture loopback copie la **sortie du moteur audio**, après le mixage ✅ ;
- un effet **SFX** traite chaque flux **avant** le mixage, donc il se trouve avant la capture ✅ (architecture APO de Microsoft) ;
- Equalizer APO 1.3+ s'installe en **SFX/MFX** sur le Bluetooth de Windows 11 🟡. L'analyseur devrait donc afficher le signal **égalisé**. C'est à **mesurer** (§6, étape 4) ;
- s'il fallait utiliser le mode **EFX**, la documentation est contradictoire sur l'inclusion de l'EFX dans la capture loopback 🟡.

Si la mesure montre que l'analyseur ne voit pas l'égalisation, l'interface le dira (« Analyseur : signal avant égaliseur »), plutôt que de laisser croire le contraire.

## 3. Architecture proposée (option A)

```
Interface web ── /api/eq (GET état, POST bandes/préréglage/bypass)
      │
Backend FastAPI ── validation (10 × −12..+12 dB, pas de 0,5) · état JSON · retour d'état
      │  WebSocket existant /api/spectrum/agent (même agent, nouveaux messages)
Agent Windows ── écrit config\devialet\eq.txt (écriture atomique : fichier temporaire puis renommage)
      │           vérifie qu'Equalizer APO est installé et qu'il équipe « My Phantom System »
Equalizer APO (audiodg.exe) ── relit eq.txt → filtres appliqués au son envoyé à « My Phantom System »
```

- **Dix bandes indépendantes**, de −12 à +12 dB, par pas de 0,5 dB. Ce sont des filtres en cloche (`Filter: ON PK`, Q = 1,41, soit une octave), plutôt que `GraphicEQ` : la réponse obtenue est ainsi exactement celle que notre modèle calcule.
- **Préréglages** : Flat, Rock, Jazz, Classical, Vocal. Plus une réinitialisation (Flat) et l'état « personnalisé » dès qu'une bande change.
- **Bypass** : le fichier ne contient alors **aucun filtre**. Le son de « My Phantom System » redevient exactement l'original, et le réglage BASS/TREBLE des Phantom n'est pas concerné.
- **Anti-écrêtage** : préampli négatif calculé sur la **réponse réelle** des filtres, plus une marge de 0,5 dB. Les bandes voisines se chevauchent : tout à +12 dB donne **+18,4 dB** réels, d'où un préampli de −18,9 dB. Conséquence : un réglage très amplifié fait baisser le niveau général, qu'on compense avec le volume des Phantom (plafond inchangé).
- **Sauvegarde** : l'état est gardé par le backend (`~/.config/devialet-web/eq.json`) et réécrit par l'agent à chaque connexion.
- **Retour d'état « DSP réellement actif »**, fourni par l'agent :
  - Equalizer APO installé (registre) ;
  - APO enregistré sur « My Phantom System » (registre de la sortie) ;
  - fichier écrit et identique à la demande ;
  - « My Phantom System » est la sortie active.

  L'interface affiche **« Actif »** seulement si tout est vrai ; sinon « Installé mais non appliqué à My Phantom System », « Equalizer APO absent »…
  
  🟡 Equalizer APO ne fournit aucun accusé de lecture. Seule une mesure confirme l'effet, d'où la vérification par l'analyseur au premier réglage, si §2 montre qu'il voit le signal égalisé.

## 4. Sécurité et réversibilité

- **Phantom** : aucune commande réseau n'est ajoutée pour l'égaliseur. Firmware, configuration stéréo, BASS/TREBLE et plafond de volume ne sont pas touchés.
- **Bluetooth** : appairage et sortie inchangés.
- **Changements dans Windows** (option A), tous réversibles :
  1. Installer Equalizer APO et cocher « My Phantom System » dans le Configurator, puis redémarrer.
  2. Une seule fois, en administrateur :
     ```
     mkdir "C:\Program Files\EqualizerAPO\config\devialet"
     icacls "C:\Program Files\EqualizerAPO\config\devialet" /grant "%USERNAME%":(OI)(CI)M
     ```
  3. Ajouter la ligne `Include: devialet\eq.txt` dans `config.txt`.
- **Retour arrière** :
  - bypass dans l'interface : son original, effet immédiat ;
  - ou décocher « My Phantom System » dans le Configurator, puis redémarrer ;
  - ou désinstaller Equalizer APO.
  
  Aucune réinstallation du système audio n'est nécessaire.

## 5. Prototype isolé

`prototypes/eq10.py` (Python pur, sans dépendance) contient :
- le modèle : bandes, validation, préréglages, état JSON avec repli sûr si le fichier est corrompu ;
- les filtres : biquads en cloche selon les formules RBJ (*Audio EQ Cookbook*), réponse exacte de la cascade ;
- le préampli automatique ;
- la génération du texte de configuration Equalizer APO ;
- un traitement hors ligne pour la vérification.

Tests : `tests/test_eq10_prototype.py`, **40 réussis**. Ils vérifient notamment :
- le gain exact au centre de chaque bande ;
- l'indépendance des bandes éloignées ;
- Flat = bypass exact ;
- traitement hors ligne conforme au modèle ;
- l'addition des bandes qui se chevauchent ;
- l'absence d'écrêtage pour chaque préréglage, avec un sinus à pleine échelle sur 7 fréquences ;
- la validation et la persistance ;
- le texte Equalizer APO (préampli, filtres, bypass) ;
- la bande de 16 kHz à 44,1 kHz.

Suite complète : **247 tests réussis**.

Exemple généré (préréglage Rock) :
```
Device: My Phantom System
Preamp: -6.4 dB
Filter: ON PK Fc 31 Hz Gain 5.0 dB Q 1.41
…
Filter: ON PK Fc 16000 Hz Gain 5.0 dB Q 1.41
```

## 6. Test réel proposé (avec votre accord, avant tout développement)

1. **Installation manuelle** d'Equalizer APO 1.4.2 depuis SourceForge ; cocher **« My Phantom System »** seulement ; redémarrer.
2. Dans `config.txt`, mettre provisoirement `Preamp: -12 dB`. Avec YouTube dans Chrome, le niveau doit baisser nettement ; sinon, essayer les « Troubleshooting options » (SFX/MFX).
3. Remettre `Preamp: 0 dB` et ajouter `Filter: ON PK Fc 1000 Hz Gain 12 dB Q 1.41`. Effet audible immédiatement, sans redémarrage : cela vérifie le rechargement automatique.
4. **Analyseur** : avec un signal stable, comparer la bande 1 kHz avec et sans le filtre. Un écart d'environ +12 dB veut dire que la capture voit le son égalisé.
5. Couper puis reconnecter le Bluetooth, puis redémarrer Windows : vérifier que l'effet revient.
6. Tester `Include: devialet\eq.txt` avec un fichier dans le sous-dossier `config\devialet`.

Selon le résultat, la V2.4.3-B implémentera l'option A (agent, `/api/eq`, interface 10 curseurs), ou étudiera D1 si Equalizer APO ne fonctionne pas sur « My Phantom System ».

## Sources

- [Loopback Recording (Microsoft)](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
- [Audio Processing Object Architecture (Microsoft)](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture)
- [Windows 11 APIs for Audio Processing Objects (Microsoft)](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/windows-11-apis-for-audio-processing-objects)
- [KSPROPERTY_AUDIOLOOPBACK, points de capture avant et après le volume (Microsoft)](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/ksproperty-audioloopback)
- [Question : effets APO capturés en loopback ? (Microsoft Q&A)](https://learn.microsoft.com/en-us/answers/questions/689393/(loopback)capture-audio-are-apo-effects-captured-t)
- [Equalizer APO, téléchargements et notes de version (SourceForge)](https://sourceforge.net/projects/equalizerapo/files/)
- [Equalizer APO, Configuration reference](https://sourceforge.net/p/equalizerapo/wiki/Configuration%20reference/)
