# Analyseur de spectre : interface visuelle V2.3.1

- **Date** : 2026-10-27
- **Périmètre** : présentation de l'analyseur uniquement.
- **Fichiers modifiés** : `devialet_web/static/` uniquement.
- **Non modifiés** : agent C#, FFT, backend, signal audio, réglages des Phantom.

## 1. Ce qui change

| Demande | Réalisation |
|---|---|
| Analyseur agrandi | Écran de **350 px** sur ordinateur ; `clamp(220px, 62vw, 350px)` sous 720 px de large. Le canvas est redimensionné à la taille CSS × `devicePixelRatio`, donc jamais étiré : les barres ne sont pas déformées. Le texte (graduations, fréquences, L/R) suit la hauteur, entre 10 et 18 px |
| Conservé | 20 barres, séparation **L · GAUCHE** / **R · DROITE**, graduations de 0 à −60 dB, fréquences, indicateurs de pic, mode mono |
| Trois thèmes | *Technics* : noir, cyan vers vert fluorescent, pics blancs. *Pioneer* : noir bleuté, bleu électrique, pics bleu très clair. *Vintage Amber* : noir chaud, orange vers ambre, pics jaune clair |
| Variables CSS | Chaque thème est un bloc `.spectrum[data-theme="…"]` qui définit `--spec-bg`, `--spec-bg-glow`, `--spec-seg-low`, `--spec-seg-high`, `--spec-seg-off`, `--spec-peak`, `--spec-peak-glow`, `--spec-grid`, `--spec-label`, `--spec-accent` et `--spec-accent-text`. Le canvas lit ces mêmes variables (`readPalette`). Un test vérifie que chaque thème les définit toutes |
| Plein écran | API Fullscreen sur le panneau lui-même, sans nouvelle fenêtre ; repli sur `webkitRequestFullscreen`. L'écran occupe toute la hauteur disponible, avec les réglages sur une ligne en dessous. **Titre et artiste** affichés discrètement en haut, rafraîchis toutes les 3 s depuis `/api/state`, uniquement en plein écran. Bouton « Quitter ✕ » ; **Échap** est géré nativement par le navigateur, et l'interface suit l'événement `fullscreenchange` |
| Erreurs du plein écran | API absente, désactivée ou requête refusée : un message est affiché sous l'analyseur (« Plein écran refusé : … ») et aucune exception ne remonte. Le bouton est désactivé si l'API n'existe pas |
| Sensibilité | Curseur de −12 à +12 dB par pas de 1 dB, valeur affichée (« +6 dB »). Elle s'ajoute aux niveaux **au moment de l'affichage** (`applySensitivity`, qui ne modifie pas ses entrées) et se réapplique immédiatement à la dernière trame reçue. Le son, l'agent, le backend et le volume des Phantom ne sont pas touchés |
| Peak hold | Liste : 500 ms, 1 s (par défaut) ou 2 s. La case « Peak hold » permet toujours de le désactiver. Le changement de durée passe par `SpectrumBallistics.configure()` et **s'applique immédiatement**, y compris à un pic déjà maintenu. La chute accélérée après le maintien est conservée |
| Préférences | `localStorage["devialet.spectrum"]` : `theme`, `sensitivity`, `peakHoldMs`, `peakHold`, `mode`, plus `enabled` (V2.3). Toutes les valeurs sont validées au chargement (`sanitize`) ; une clé inconnue, comme `fullscreen`, n'est jamais restaurée. Un JSON corrompu ou un stockage bloqué (navigation privée) donnent les valeurs par défaut, sans erreur. Les préférences V2.3 sont reprises et complétées |

## 2. Organisation du code

| Fichier | Rôle |
|---|---|
| `static/spectrum-settings.js` | **Nouveau**, sans DOM : thèmes, `sanitize` / `load` / `save`, `applySensitivity`, couleurs, `readPalette`, `createFullscreen` (contrôleur avec résultats `{ok, error}`) |
| `static/spectrum-ballistics.js` | Ajout de la seule fonction `configure()`. Le comportement par défaut et les 10 tests d'origine sont inchangés |
| `static/spectrum.js` | Branchement des réglages, application du thème, plein écran, titre et artiste en plein écran, texte adaptatif. La connexion WebSocket et la logique de disponibilité sont inchangées |
| `static/index.html` | Rangée de réglages (thème, sensibilité, peak hold et durée, stéréo/mono, plein écran), titre et bouton « Quitter » |
| `static/app.css` | Variables par thème, écran de 350 px, styles `:fullscreen` et `:-webkit-full-screen` |

Le backend relit les fichiers statiques à chaque requête : **un rechargement de la page suffit**, sans redémarrer le serveur.

## 3. Tests

| Fichier | Contenu | Nombre |
|---|---|---|
| `tests/js/spectrum-settings.test.cjs` | Thèmes (sélection, repli, `__proto__`), palette depuis les variables CSS et repli, couleurs ; sauvegarde et restauration, plein écran jamais persisté, JSON corrompu, stockage bloqué, migration depuis la V2.3 ; sensibilité (bornes, arrondi, données non modifiées, libellé) ; durées 500/1000/2000 ; plein écran (entrée et sortie, sortie externe par Échap, préfixe webkit, refus, API désactivée ou absente, échec de sortie) | 19 |
| `tests/js/spectrum-ballistics.test.cjs` | Les 10 tests d'animation de la V2.3 (régression), plus le maintien à 500, 1 000 et 2 000 ms, la prise en compte immédiate d'un changement de durée, et le fait que `configure` ne touche pas les autres paramètres | 15 |
| `tests/test_spectrum_visual.py` | Chaque thème définit toutes les variables ; JS et CSS déclarent les mêmes thèmes ; écran de 350 px et adaptation sur petit écran ; règles de plein écran ; présence des réglages et des options ; ordre des scripts ; fichiers servis ; fonctions V1, V2.1 et V2.2 toujours présentes | 5 |

`pytest` lance les tests JavaScript avec `node --test`. **Total : 127 tests `pytest` réussis**, dont les 122 existants et l'ensemble des 34 tests JavaScript.

## 4. Limites

- **Rendu** : l'apparence des thèmes et du plein écran est vérifiée par les tests statiques et la logique, mais pas encore visuellement dans Chrome pour cette version.
- **Titre et artiste en plein écran** : ils dépendent des métadonnées que les Phantom publient. Pour une source Bluetooth depuis Windows, ces champs sont souvent vides (`title: ""` observé pour `DESKTOP-EXAMPLE`). Dans ce cas, rien n'est affiché.
- **Sensibilité** : à +12 dB, les passages forts saturent l'affichage (barres en haut). C'est voulu, car le réglage ne fait que décaler l'échelle.
