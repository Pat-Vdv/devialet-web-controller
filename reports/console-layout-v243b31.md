# V2.4.3-B3.1 : réorganisation des blocs de la console Hi-Fi

Date : 2026-10-30. Périmètre validé : **déplacement vertical uniquement**, avec sauvegarde automatique. Depuis B3.1.2 (§8), le déplacement se fait **depuis l'en-tête du bloc** ; la petite poignée ⠿ des versions B3.1 et B3.1.1 a été supprimée. Pas de redimensionnement ni de disposition en colonnes.

Aucune API FastAPI, aucun fichier Equalizer APO, aucun réglage Bluetooth ou réseau n'a été modifié. Aucun son n'a été envoyé aux Phantom. Les essais ont tourné sur un serveur de démonstration avec des enceintes simulées et un égaliseur dans un dossier temporaire. Le serveur réel (port 8000) sert les nouveaux fichiers après un simple rechargement de la page.

## 1. Fonctionnement (état B3.1.2)

| Action | Comment |
|---|---|
| Déplacer un bloc | **Souris** : cliquer dans une zone libre de l'en-tête du bloc (titre, pastille d'état, espace vide), maintenir et glisser vers le haut ou le bas. Curseur `grab` sur l'en-tête, `grabbing` pendant le déplacement. **Tactile ou stylet** : appui maintenu environ 0,3 s sur l'en-tête (le bloc s'entoure d'un contour), puis glisser. |
| Retour visuel | Le bloc saisi suit le pointeur, soulevé (contour aux couleurs du thème et ombre). Une **ligne lumineuse** montre où il sera déposé. Elle est atténuée quand la position ne change pas. |
| Animation | Les blocs glissent jusqu'à leur nouvelle place en 200 ms. Pas d'animation si le système demande de réduire les mouvements. |
| Page longue (téléphone) | La page défile seule quand on approche le bloc du haut ou du bas de l'écran. |
| Annuler | Touche Échap pendant le glisser. Un clic sur l'en-tête sans mouvement (moins de 6 px) ne déplace rien. Au doigt, un glissement avant la fin de l'appui maintenu fait défiler la page. |
| Clavier | Chaque bloc a deux boutons **Monter ▲** et **Descendre ▼**, invisibles à l'écran tant qu'ils n'ont pas le focus. Avec Tab, ils apparaissent en petite pastille flottante au début de l'en-tête, sans décaler la mise en page. Entrée, ou flèches ↑/↓ (une place) et Début/Fin (première ou dernière place). Les lecteurs d'écran les annoncent toujours, et chaque déplacement est annoncé (« Égaliseur graphique en position 1 sur 5 »). |
| Sauvegarde | Automatique, à chaque déplacement, dans le `localStorage` du navigateur (clé `devialet.layout`). L'ordre est réappliqué au chargement, avant le premier affichage. |
| Réinitialiser la disposition | Bouton sous les blocs, grisé quand l'ordre est déjà celui d'origine. Il rétablit l'ordre d'origine et efface la sauvegarde. |
| Plusieurs onglets | Un déplacement fait dans un onglet est repris par les autres. |

Les blocs déplaçables sont, dans l'ordre d'origine : **Lecteur audio, Analyseur de spectre, Commandes audio (BASS–VOLUME–TREBLE), Égaliseur graphique, Sortie audio Windows**.

Trois éléments restent fixes :
- l'en-tête (marque, connexion, thème), toujours en haut ;
- le panneau « Informations système », toujours en bas ;
- le message d'erreurs général. Il était entre l'égaliseur et la sortie Bluetooth ; il est maintenant juste sous l'en-tête, pour ne pas dépendre de l'ordre des blocs.

**Protection contre les déplacements accidentels**
- Seul l'en-tête réagit au glisser, jamais le corps du bloc. Les curseurs, potentiomètres et le fond des cartes ne déclenchent donc jamais de déplacement : c'est vérifié dans Edge (glisser un curseur de l'égaliseur ou le fond d'une carte de 400 px ne change rien).
- Dans l'en-tête même, les éléments interactifs sont ignorés : boutons, liens, champs, listes, étiquettes, curseurs et potentiomètres (`role=slider`), et tout élément focusable.
- Sur écran tactile, aucun en-tête ne bloque le défilement. Un glissement normal fait défiler la page. Le défilement n'est bloqué qu'une fois le bloc saisi par l'appui maintenu.
- Pas de déplacement pendant le plein écran de l'analyseur.

## 2. Architecture

| Fichier | Rôle |
|---|---|
| `static/layout-core.js` (nouveau) | Logique pure, testée avec node. Elle couvre l'ordre, le chargement, la réparation et la sauvegarde, les déplacements (`move`, `moveBy`) et le calcul de la place de dépôt. |
| `static/layout.js` (nouveau) | Partie DOM. Elle ajoute les poignées et gère le glisser (événements pointeur), le clavier, l'animation, la sauvegarde et le bouton de réinitialisation. |
| `static/index.html` | Les cinq blocs sont dans `<div class="blocks" id="blocks">`. Chacun porte `data-block` (identifiant stable) et `data-block-label` (nom annoncé). L'en-tête qui reçoit la poignée est marqué `data-block-head`. Viennent ensuite la barre « Réinitialiser la disposition » et les deux scripts. |
| `static/app.css` | Section « F. Movable blocks », plus deux ajouts : écart réduit sur téléphone, animation de mise sous tension bloc par bloc. |

**Choix techniques**
- **Sans dépendance.**
  - Événements pointeur plutôt que le glisser-déposer HTML5, qui ne fonctionne pas au doigt.
  - Environ 330 lignes au total, commentaires compris.
- **Les éléments sont déplacés, jamais recréés.**
  - Le déplacement utilise `moveBefore` quand le navigateur le propose (il conserve le focus), sinon `insertBefore` puis le focus est rendu.
  - États, écouteurs, minuteries, canvas de l'analyseur et envois en cours de l'égaliseur restent intacts. L'essai Edge le vérifie : mêmes nœuds après déplacement, et un curseur de l'égaliseur comme le volume fonctionnent toujours ensuite.
- **L'ordre d'origine est l'ordre du HTML.**
  - Un bloc ajouté plus tard dans `index.html` est reconnu sans toucher au JavaScript.
  - L'ordre d'origine est enregistré comme « rien de sauvegardé » (la clé est effacée). Ainsi, un futur bloc garde sa place prévue chez quelqu'un qui n'a jamais rien déplacé.
- **Sauvegarde versionnée** : `{"version": 1, "order": [...]}`.
- **Réparation au chargement** (puis réécriture de la sauvegarde) :

  | Cas | Comportement |
  |---|---|
  | JSON illisible, mauvais type | Ordre d'origine, sauvegarde effacée |
  | Identifiant inconnu (bloc supprimé, valeur trafiquée) | Ignoré |
  | Doublon | Ignoré |
  | **Bloc absent de la sauvegarde** (nouveau bloc) | **Inséré après le bloc qui le précède dans l'ordre d'origine**, ou en tête s'il n'en a aucun. Aucun bloc n'est jamais perdu |
  | Stockage bloqué ou plein (navigation privée) | Ordre d'origine, pas d'erreur. La disposition n'est simplement pas mémorisée |

- **Défilement automatique** cadencé par une minuterie de 16 ms plutôt que par `requestAnimationFrame`. Le rendu est le même, mais il continue même quand le navigateur ralentit l'affichage, et il est vérifiable sans interface (voir §4).

## 3. Compatibilité

- **Thèmes.**
  - La poignée et les boutons prennent la couleur atténuée de chaque carte : `--muted` pour les cartes simples, `--tone-dim` pour les panneaux aluminium, `--panel-dim` pour l'analyseur.
  - Au survol et au focus, ils prennent la couleur d'accent de la carte : `--accent`, `--tone-led` ou `--spec-accent`.
  - Le bloc soulevé et la ligne de dépôt utilisent `--accent`.
  - Aucune couleur fixe (vérifié par test). Captures dans les quatre thèmes : `work/layout-e2e/layout-themes.png`.
- **Commandes audio et mises à jour.**
  - Aucun code des panneaux n'a été modifié : `tone.js`, `eq.js`, `spectrum.js`, `app.js` et `bluetooth.js` sont inchangés.
  - `layout.js` ne fait aucun appel réseau et ne touche à aucun identifiant de commande (vérifié par test).
  - Les relectures périodiques continuent sur les nœuds déplacés.
- **Taille des poignées.** 28×32 px à la souris, **40×42 px dès qu'un écran tactile est présent** (`any-pointer: coarse`, depuis B3.1.1, voir §7). Des marges verticales négatives gardent la hauteur d'origine des en-têtes.

## 4. Tests

**Automatisés** : 346 tests Python (333 avant B3.1) et 93 tests JavaScript (80 avant), tous réussis. Après B3.1.1 : 348 tests Python et 93 tests JavaScript (§7).

| Suite | Contenu |
|---|---|
| `tests/js/layout-core.test.cjs` (13) | Ordre d'origine, sauvegarde et rechargement, déplacements bornés, Monter/Descendre aux extrémités, 7 contenus invalides, identifiants inconnus et doublons, tableau nu accepté, **migration avec nouveaux blocs** (au milieu, en tête, plusieurs à la suite), bloc retiré, réinitialisation, stockage qui lève une exception, place de dépôt |
| `tests/test_console_layout.py` (13, 15 après B3.1.1) | Cinq blocs dans le conteneur, dans l'ordre d'origine, avec identifiants uniques ; un en-tête de poignée par bloc ; en-tête, erreurs et informations système fixes ; bouton et textes accessibles ; scripts placés juste après les blocs ; **écouteurs pointeur uniquement sur la poignée** ; pas de `draggable` ; éléments déplacés et jamais recréés ; clavier ; stockage via `layout-core` ; style par variables de thème, `touch-action`, `any-pointer: coarse`, plein écran, mouvement réduit ; **identifiants des commandes audio intacts et jamais manipulés par `layout.js`** ; syntaxe ; fichiers servis |

**Essai de bout en bout dans Edge (sans interface).**
- Le script `work/layout-e2e/layout-e2e.js` est injecté dans la vraie page servie par le serveur de démonstration.
- Chaque phase est une nouvelle instance d'Edge sur le **même profil** : c'est un vrai rechargement, avec le `localStorage` conservé.
- Pour relancer : `work/layout-e2e/demo_eq.py`, puis `e2e.sh`.

| Phase | Vérifié | Résultat |
|---|---|---|
| 1 | Profil vierge, ordre d'origine, 5 poignées, `touch-action: none`, bouton de réinitialisation grisé | ✅ |
| 1 | Glisser un curseur de l'égaliseur ou le fond d'une carte : ordre inchangé. Appui sur la poignée sans mouvement : inchangé | ✅ |
| 1 | Glisser l'égaliseur au-dessus du lecteur : classes « soulevé » et ligne de dépôt pendant le geste, rien ne bouge avant de relâcher, ordre `eq, player, spectrum, audio, bluetooth` après, sauvegarde écrite | ✅ |
| 1 | Mêmes nœuds (carte, curseur, canvas) ; styles nettoyés | ✅ |
| 1 | Curseur 1 kHz à −4,5 après déplacement : API à −4,5, afficheur « −4,5 ». Volume à 20 : API à 20 | ✅ |
| 1 | Flèche ↑ sur la poignée Bluetooth : monte d'une place, focus conservé, annonce « Sortie audio Windows en position 4 sur 5 ». Bouton Descendre : revient. Boutons grisés aux extrémités | ✅ |
| 1 | Échap pendant un glisser : annulé. Glisser le lecteur tout en bas | ✅ |
| 2 | **Après rechargement** : ordre restauré. « Réinitialiser la disposition » : ordre d'origine, clé effacée, bouton grisé | ✅ |
| 3 | Sauvegarde `garbage{` : ordre d'origine, clé effacée | ✅ |
| 4 | Sauvegarde `["ghost","bluetooth","eq"]` : aucun bloc perdu, `ghost` ignoré, ordre réparé et réenregistré | ✅ |
| 5 | Fenêtre de 700 px, bloc tenu en haut de l'écran : la page défile de 1561 à 0 px, bloc déposé en premier | ✅ |

**Captures.** Fichiers dans `work/layout-e2e/`, aucun débordement horizontal à 1280, 760, 390 et 360 px :
- `layout-themes.png` : 4 thèmes à 1280 px ;
- `layout-detail.png` : glisser en cours ;
- `layout-heads.png` : en-têtes, et focus clavier avec ▲▼ ;
- `layout-narrow.png` : 760 / 390 / 360 px, et glisser à 360 px.

Les largeurs inférieures à 500 px sont émulées dans un cadre, Edge sans interface ne descendant pas en dessous.

**Limite de l'essai.** Les gestes sont simulés par des événements pointeur créés dans la page ; ils passent par le même code, mais ce ne sont pas de vrais doigts. Je n'ai pas pu piloter Edge de l'extérieur : exposer son port de débogage a été refusé, à juste titre. Le comportement tactile réel reste donc à confirmer sur votre téléphone ou tablette : saisie de la poignée, défilement normal ailleurs (§6).

## 5. Risques résiduels

- La disposition est propre à chaque navigateur et à chaque appareil (`localStorage`). Le téléphone et le PC ont chacun la leur ; c'est voulu, sans persistance côté serveur.
- Renommer un `data-block` existant ferait perdre sa position sauvegardée : le bloc reviendrait à sa place d'origine, sans être perdu. C'est indiqué en commentaire dans `index.html`.
- Si `layout.js` ne se charge pas, la page s'affiche dans l'ordre d'origine, sans poignées. Tout le reste fonctionne.

## 6. Validation à faire de votre côté

Rechargez http://localhost:8000 (Ctrl+F5). Rien de ce qui suit ne modifie le son.
1. **PC, souris** : glisser l'égaliseur par sa poignée ⠿ en haut de la page, puis recharger : il doit rester en haut.
2. Manipuler un curseur, un potentiomètre et le volume : aucun bloc ne doit bouger.
3. **Clavier** : Tab jusqu'à une poignée, puis ↑/↓.
4. **Téléphone ou tablette** :
   - glisser une poignée : la page doit défiler seule près des bords ;
   - faire défiler la page en touchant ailleurs : rien ne doit bouger.
5. « Réinitialiser la disposition », puis changer de thème.

B4 (préréglages) est en pause. Les calculs préparatoires (préamplis et remontées prévisibles entre préréglages) sont dans le dossier temporaire de la session ; rien n'est intégré au projet.

## 7. B3.1.1 : corrections ergonomiques (2026-10-30)

Trois corrections, toutes dans `app.css`, section « F. Movable blocks ». Aucun changement en JavaScript, dans les modules audio, les API, Equalizer APO, les Phantom, le réseau ou le Bluetooth.

| # | Problème | Correction |
|---|---|---|
| 1 | **Ligne de destination cachée.** En descendant un grand bloc (analyseur, égaliseur), la place visée est souvent sous la carte soulevée, qui suit le doigt par son en-tête et s'étend vers le bas. La ligne (`z-index: 4`) passait sous la carte (`z-index: 5`) | `.drop-marker` passe à `z-index: 6`. Une seule valeur changée |
| 2 | **Poignée trop près du bord sur téléphone.** Au tactile, la zone de la poignée était ramenée de 12 px dans la marge de la carte : elle commençait à 13 px du bord de l'écran, dans la zone des gestes système (retour Android, balayage Safari, environ 20 à 30 px) | Au tactile, plus de marge négative à gauche. La zone tactile commence à **25 px** du bord (marge de la page + bordure + marge intérieure de la carte à 360 px), et les points ⠿ sont centrés à environ **45 px** |
| 2 bis | Conséquence relevée pendant la vérification : à 360 px, l'en-tête de l'analyseur ne pouvait pas se replier, et la pastille « Agent absent » dépassait de la carte. Ce n'était pas un débordement de la page. Le dépassement existait déjà de quelques pixels en B3.1 | Le titre de l'analyseur peut passer sur deux lignes (« Analyseur de / spectre »). La piste centrale du plein écran garde la priorité |
| 3 | **PC hybrides.** `pointer: coarse` ne visait que les appareils dont le pointeur principal est tactile. Un PC portable à écran tactile utilisé à la souris gardait la petite poignée | `@media (any-pointer: coarse)` : la grande poignée dès qu'un écran tactile existe. C'était la seule règle tactile du CSS ; aucune autre n'était concernée |

La navigation clavier, les boutons Monter et Descendre, les annonces aux lecteurs d'écran et les quatre thèmes sont inchangés.

### Tests

- **Automatisés** : **348 tests Python**, dont 2 nouveaux dans `tests/test_console_layout.py`, et **93 tests JavaScript**, tous réussis. Les nouveaux tests vérifient que :
  - la ligne de destination est au-dessus du bloc soulevé ;
  - au tactile, la poignée mesure 40×42 px, sans marge négative à gauche ;
  - le titre de l'analyseur peut se replier ;
  - le CSS utilise `any-pointer: coarse` et non plus `pointer: coarse`.
- **Edge, glisser vers le bas sans relâcher** : analyseur et égaliseur, à 1280 px (Technics, Pioneer), 360 px (Vintage Amber) et 390 px (Classic).
  - Dans les quatre cas, la ligne se trouve **dans la zone couverte par la carte soulevée**, c'est-à-dire exactement la situation du problème.
  - Elle s'affiche maintenant par-dessus (`z-index` 6 contre 5, vérifié dans le navigateur et en capture : `work/layout-e2e/marker-over-card.png`).
- **Edge, phase 6 du scénario** (nouvelle) : déposer réellement les grands blocs plus bas, puis contrôler les commandes audio.

  | Étape | Résultat |
  |---|---|
  | Analyseur glissé sous l'égaliseur | `player, audio, eq, spectrum, bluetooth` ✅ |
  | Égaliseur glissé tout en bas | `player, audio, spectrum, bluetooth, eq` ✅ |
  | Styles nettoyés après les déplacements | ✅ |
  | Curseur 250 Hz à +3 | API +3, afficheur « +3 » ✅ |
  | Bouton Activé/Désactivé | ✅ |
  | Volume à 17, puis bouton « + » | 19 ✅ |
  | Potentiomètre des basses au clavier | +1 ✅ |
  | Analyseur | toujours piloté ✅ |

  Les phases 1 à 5 ont été rejouées sans changement de résultat.
- **Captures des 4 thèmes × 4 largeurs** :
  - 1280 et 760 px à la souris ;
  - 390 et 360 px avec les règles tactiles ;
  - en plus, 1280 px avec les règles tactiles (PC hybride) et 360 px à la souris.

  Résultats :
  - **aucun débordement horizontal** ;
  - **aucun élément ne dépasse de sa carte** (sonde sur tous les éléments des cinq blocs) ;
  - hauteurs d'en-tête identiques avec la petite et la grande poignée.

  Captures dans `work/layout-e2e/shots/` et `work/layout-e2e/phones-coarse.png`.

**Comment les règles tactiles ont été affichées.** Edge sans interface ne se déclare pas tactile. Pour les captures seulement, le serveur de démonstration sert une copie du CSS où `@media (any-pointer: coarse)` est remplacé par `@media all`. On voit ainsi exactement le rendu de ces règles, mais **pas la détection réelle d'un écran tactile par le navigateur**. Celle-ci, comme les gestes au doigt, n'a **pas** été validée ici : les gestes restent simulés. La confirmation sur un vrai téléphone et, si possible, sur un PC hybride reste nécessaire.

### Risques restants

- **Gestes système.** La zone tactile commence à 25 px du bord, les points à 45 px. Un doigt posé tout au bord de la poignée peut encore déclencher le geste « retour » si la sensibilité Android est réglée au maximum. Poser le doigt sur les points suffit.
- **En-tête de l'analyseur sur deux lignes** à 360 px : il gagne environ 7 px de hauteur, c'est voulu.

## 8. B3.1.2 : déplacement depuis l'en-tête (2026-10-30)

À l'usage, la poignée ⠿ était peu visible et peu intuitive. Le bloc se déplace maintenant **depuis son en-tête**, et les poignées sont supprimées.

### Modifications

| Fichier | Changement |
|---|---|
| `index.html` | `data-block-head` est placé sur la ligne d'en-tête complète (`.spectrum-head`, `.tone-head` ×2, `.systems-head`). Les enveloppes ajoutées autour des titres pour les poignées sont retirées : ces en-têtes ont retrouvé leur structure V2.4.2. Le lecteur garde son enveloppe `.block-head`, parce que `app.js` réécrit le texte de `#source-name`. Le texte d'aide pour les lecteurs d'écran est mis à jour |
| `layout.js` | Les écouteurs pointeur sont posés sur l'en-tête. Une pression sur un élément interactif de l'en-tête est ignorée (liste `INTERACTIVE`). **Souris** : départ après 6 px. **Tactile et stylet** : appui maintenu de 300 ms, abandonné si le doigt bouge de plus de 8 px avant (c'est alors un défilement). Une fois le bloc saisi, `touchmove` est annulé (écouteur non passif) pour que la page ne défile pas sous le doigt. Le menu contextuel de l'appui long est neutralisé sur l'en-tête, avec une petite vibration quand l'appareil le permet. Les boutons Monter et Descendre restent. Animation, sauvegarde, réinitialisation, Échap et défilement automatique sont inchangés |
| `app.css` (section F) | Curseur `grab` sur l'en-tête et `grabbing` pendant le déplacement ; les commandes de l'en-tête gardent leur curseur. Texte de l'en-tête non sélectionnable. Zone de saisie du lecteur agrandie de 8 px en haut et en bas, sans changer la mise en page. Contour « saisi » au tactile. Pastille flottante Monter/Descendre visible seulement au focus, aux couleurs `--field-*` de chaque thème. Ligne de destination toujours au-dessus du bloc déplacé (`z-index` 6). Boutons agrandis sur écran tactile (`any-pointer: coarse`). Toutes les règles de poignée sont supprimées, ainsi que la règle de B3.1.1 qui permettait au titre de l'analyseur de passer sur deux lignes |

Aucun module audio, aucune API, ni Equalizer APO, ni réglage des Phantom, du Bluetooth ou du réseau n'a été modifié. Seuls `index.html`, `app.css` et `layout.js` ont changé dans l'application. Les fichiers `config.txt` et `devialet-eq.txt` sont inchangés.

### Tests

- **Automatisés** : **349 tests Python** et **93 tests JavaScript**, tous réussis. `tests/test_console_layout.py` est adapté et passe à 16 tests. Il vérifie :
  - un seul `data-block-head` par bloc, placé sur la ligne d'en-tête complète ;
  - des écouteurs pointeur sur l'en-tête uniquement ;
  - la liste des éléments interactifs ignorés et son application à l'intérieur de l'en-tête seulement ;
  - au tactile, glissement = défilement et appui maintenu = déplacement, `touchmove` annulé seulement une fois le bloc saisi, aucun `touch-action` qui bloquerait le défilement ;
  - plus aucune poignée ;
  - les boutons clavier masqués sans `display: none` ;
  - les curseurs des commandes de l'en-tête et la taille au tactile.

  `tests/js/layout-core.test.cjs` (13) est inchangé : la logique d'ordre n'a pas bougé.
- **Edge, scénario complet rejoué** (phases 1 à 7). La pression part du titre de l'en-tête, résolu par `elementFromPoint` comme un vrai clic.

  | Vérifié | Résultat |
  |---|---|
  | Aucune poignée, curseur `grab` sur l'en-tête, pas de `touch-action` bloquant | ✅ |
  | Déplacement depuis l'en-tête : égaliseur au-dessus du lecteur, ligne de destination et contour pendant le geste | ✅ |
  | Clic sur l'en-tête sans mouvement, glisser d'un curseur, glisser du fond d'une carte : aucun déplacement | ✅ |
  | Sauvegarde, restauration après rechargement, réinitialisation, sauvegarde invalide, identifiant inconnu | ✅ |
  | Échap pendant un glisser : annulé | ✅ |
  | Clavier : Tab sur « Monter » (pastille visible, 30 px), ↑ (monte, focus conservé, annonce), « Descendre » (revient), boutons grisés aux extrémités | ✅ |
  | Analyseur et égaliseur déposés plus bas ; défilement automatique dans une fenêtre de 700 px | ✅ |
  | **Tactile simulé** : glissement rapide de 40 px sur l'en-tête de l'égaliseur | Rien ne bouge, pas de contour (défilement) ✅ |
  | **Tactile simulé** : appui maintenu 400 ms | Contour « saisi », puis glisser : égaliseur en tête ✅ |
  | **Tactile simulé** : appui maintenu interrompu par le navigateur (`pointercancel`) | Rien ne bouge ✅ |
  | Pression puis glisser de 360 px depuis un bouton de l'en-tête (« Descendre », « Quitter » du plein écran) | Aucun déplacement ✅ |
  | Pression sur la pastille « Bluetooth » (zone libre) | Le bloc se déplace ✅ |
  | Commandes audio après déplacements : curseur de l'égaliseur, bouton Activé, volume (curseur et « + »), potentiomètre des basses au clavier, analyseur | ✅ |

- **Captures des 4 thèmes × 4 largeurs** (1280 et 760 px à la souris, 390 et 360 px avec les règles tactiles) :
  - aucun débordement horizontal ;
  - aucun élément ne dépasse de sa carte ;
  - curseur `grab` sur les en-têtes ;
  - hauteurs d'en-tête identiques à celles d'avant B3.1 (l'en-tête de l'analyseur retrouve sa ligne unique à 360 px).

  Glisser l'analyseur (1280 px) et l'égaliseur (360 px) vers le bas : la ligne de destination est dans la zone couverte par la carte soulevée, et s'affiche par-dessus, avec le curseur `grabbing`. La pastille clavier a été vérifiée en Classic et Pioneer. Images dans `work/layout-e2e/shots/` et `work/layout-e2e/b312-check.png`.

**Non validé ici.** Les gestes sont **simulés**, y compris le « tactile ». Trois comportements réels ne peuvent être vérifiés que sur votre appareil :
- le défilement natif de la page lors d'un glissement sur un en-tête ;
- le blocage du défilement une fois le bloc saisi ;
- la neutralisation du menu de l'appui long.

### Limites et risques restants

- **Découverte au tactile.** Sur téléphone, l'appui maintenu n'est pas visible avant d'être essayé. C'est le geste habituel des listes réordonnables des téléphones, et l'aide destinée aux lecteurs d'écran le décrit. Une mention visible sous les blocs reste possible si vous le jugez utile.
- **Navigateurs sans écouteur `touchmove` bloquant.** Si un navigateur commençait malgré tout à défiler après la saisie, il enverrait `pointercancel` : le déplacement serait abandonné proprement, sans bloc perdu ni mal placé.
- **Pastille clavier.** Quand elle a le focus, elle recouvre temporairement le début du titre du bloc (environ 60 px). C'est voulu, pour ne rien décaler.
- **Titre non sélectionnable.** Le texte des en-têtes ne peut plus être sélectionné à la souris, pour qu'un glisser ne sélectionne pas de texte.

### Validation à faire de votre côté

Rechargez http://localhost:8000 (Ctrl+F5). Rien de ce qui suit ne modifie le son.
1. **PC** : saisir l'en-tête « ANALYSEUR DE SPECTRE », glisser vers le bas sous l'égaliseur, relâcher, puis recharger.
2. **PC** : cliquer sur les boutons, curseurs, potentiomètres et listes des blocs : aucun déplacement.
3. **PC** : Échap pendant un glisser ; Tab jusqu'aux boutons ▲▼.
4. **Téléphone** :
   - faire défiler la page en glissant sur un en-tête : la page doit défiler ;
   - appui maintenu sur un en-tête, puis glisser : le bloc se déplace et la page ne défile pas sous le doigt (sauf près des bords).
5. « Réinitialiser la disposition ».


## 9. B3.1.3 : petits rectangles gris à gauche des titres (2026-10-30)

**Symptôme.** Sur votre navigateur, deux petits rectangles gris apparaissaient au-dessus et à gauche du titre des cinq blocs, alors que le déplacement par l'en-tête fonctionnait.

**Origine exacte.** Ce ne sont pas des restes de poignée dans le code : le HTML et le CSS servis ne contiennent plus aucune poignée. Ce sont les **boutons Monter et Descendre**, que `layout.js` ajoute à chaque en-tête pour le clavier, affichés **sans aucun style**.
- Le journal du serveur (`work/web.log`) montre que votre navigateur a rechargé `layout.js` (version B3.1.2) mais **n'a plus redemandé `app.css` depuis avant B3.1**. Il réutilisait une copie en cache, considérée comme encore valable parce que le fichier n'avait pas changé depuis longtemps à ce moment-là.
- Sans les règles de la section F, ces boutons ne sont plus masqués jusqu'au focus : ils s'affichent comme des boutons gris par défaut.
- **Reproduit dans Edge** (`work/layout-e2e/stale-css-repro.png`). Page et script actuels avec la feuille de style d'avant B3.1 : les deux rectangles apparaissent exactement à cet endroit. Avec la feuille actuelle : rien.
- Dans un profil Edge vierge, la page du serveur réel (port 8000), agrandie ×2, ne montre aucun rectangle.

**Correction** (dans `index.html` uniquement, aucun changement de comportement) :
- `app.css`, `layout-core.js` et `layout.js` sont chargés avec un même numéro de version, `?v=243b313`. Un navigateur qui a gardé une ancienne copie est obligé de télécharger les trois fichiers ensemble ; il ne peut plus combiner un nouveau script avec une ancienne feuille de style.
- Un commentaire dans `index.html` rappelle de changer ce numéro à chaque modification de ces fichiers.
- Un test vérifie que les trois fichiers portent la même version et sont bien servis avec elle.

Le déplacement depuis l'en-tête, les curseurs `grab`/`grabbing`, la sauvegarde et les boutons Monter et Descendre (invisibles jusqu'au focus clavier) sont inchangés. Les modules audio, les API, Equalizer APO et les réglages des Phantom ne sont pas touchés ; `config.txt` et `devialet-eq.txt` sont identiques.

**Tests**
- 350 tests Python (1 de plus) et 93 tests JavaScript, tous réussis.
- Scénario Edge rejoué en entier (phases 1 à 7), sans changement de résultat :
  - **réussi** : cinq blocs déplaçables depuis l'en-tête ;
  - **réussi** : aucun déplacement depuis une commande ;
  - **réussi** : sauvegarde et restauration après rechargement, réinitialisation ;
  - **réussi** : clavier (Monter/Descendre, flèches) et Échap ;
  - **réussi** : appui maintenu au tactile simulé ;
  - **réussi** : commandes audio (égaliseur, volume, potentiomètre, analyseur) qui fonctionnent.
- Captures des quatre thèmes à 1280 et 360 px : aucun rectangle, aucun débordement, rien qui dépasse des cartes. Titres alignés au même endroit qu'avant B3.1 (25 px du bord de la carte à 360 px).

**De votre côté.** Un rechargement normal (F5) suffit : la page demande désormais `app.css?v=243b313`, une adresse que votre cache ne connaît pas.

## 10. Clôture de B3.1 (2026-10-31)

**État.** B3.1 est close pour l'usage sur PC.
- Vous avez vérifié le déplacement des blocs à la souris.
- Le correctif de cache (§9) est en place.
- Fonctionnalités et tests sont conservés tels quels.

**Reste à confirmer : le tactile réel.** Tous les essais tactiles faits ici sont simulés par des événements synthétiques dans Edge (appui maintenu, glissement, annulation). Le comportement d'un vrai doigt sur un vrai écran n'est pas validé. À vérifier sur téléphone ou tablette :
1. un glissement simple sur un en-tête fait défiler la page ;
2. un appui maintenu d'environ 0,3 s puis un glissement déplace le bloc ;
3. aucun menu contextuel n'apparaît.

Les limites connues sont au §5.

**Git.** Le dossier `~/projects/devialet` n'est pas un dépôt Git (`git status` : « not a git repository »). Il n'y a donc ni historique ni modifications « en attente » au sens de Git, et aucun commit n'a été fait. Fichiers de B3.1 :
- créés : `layout-core.js`, `layout.js`, `tests/js/layout-core.test.cjs`, `tests/test_console_layout.py` ;
- modifiés : `index.html`, `app.css`, `README.md`.

**Suite (B4).** Le numéro de version des fichiers statiques passe de `?v=243b313` à `?v=243b4`, parce que `app.css` change avec les préréglages. Les scripts de l'égaliseur ont désormais le même numéro. Le scénario Edge de B3.1 a été rejoué après B4 sans différence (voir `reports/graphic-eq-presets.md`, Validation).
