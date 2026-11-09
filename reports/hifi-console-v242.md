# Hi-Fi Console : refonte de l'interface (V2.4.2)

- **Date** : 2026-10-28
- **Périmètre** : présentation et ergonomie uniquement, dans `devialet_web/static/`.
- **Non modifiés** : backend, protocole IP Control, agent C#, FFT, logique de l'analyseur et des potentiomètres, limites du volume, Bluetooth, réglages des Phantom.
- **Aucune requête** vers les vraies enceintes pour cette version. Le contrôle visuel a été fait sur des Phantom simulées (port 8765).

## 1. Modifications réalisées

| Demande | Réalisation |
|---|---|
| Quatre thèmes globaux | *Classic*, *Technics*, *Pioneer* et *Vintage Amber*, appliqués à toute la page par `<html data-theme>` |
| Une seule source de vérité | Un seul sélecteur (`#theme-select`, dans l'en-tête) et une seule clé `localStorage`, `devialet.theme`. Le sélecteur de l'analyseur est supprimé ; sa palette se déduit du thème global |
| Pas de flash au chargement | `theme.js` est chargé de façon **bloquante dans `<head>`** : le thème enregistré est posé sur `<html>` avant le premier affichage |
| Migration V2.3.1 | Si `devialet.theme` est absent, le thème choisi dans l'analyseur V2.3.1 (`devialet.spectrum.theme`) devient le thème global, puis il est enregistré. La sensibilité, le peak hold, sa durée, le mode stéréo/mono et l'activation restent dans `devialet.spectrum`, sans modification |
| Façade | En-tête façon façade d'amplificateur : DEVIALET, PHANTOM II, nom du système, voyant de connexion, sélecteur de thème |
| Analyseur au centre | Pleine largeur (jusqu'à 1 120 px), 20 bandes, graduations, peak hold, sensibilité, mono/stéréo, plein écran et animations inchangés |
| BASS · VOLUME · TREBLE | Un seul panneau en aluminium brossé. Les deux potentiomètres sont ceux de la V2.4.1, avec le même code. Au centre, le volume : grand afficheur lumineux, curseur, boutons − et +, plafond de 50 inchangé. Le bouton « Neutre » est conservé |
| Bluetooth | Apparence seulement : mêmes éléments, mêmes actions, assistant d'appairage inchangé |
| Informations système | Panneau repliable (`<details>`) : les deux enceintes (rôle, nom, IP, firmware, badge Leader), plus la recherche et les résultats de découverte. Le résumé fermé indique par exemple « 2/2 enceintes joignables · leader 192.0.2.11 » |
| Connexion | Voyant à trois états : **Connecté**, **Connexion perdue**, puis **Reconnexion en cours…** (voyant clignotant). En cas de coupure, le détail précise que les commandes sont indisponibles, mais que le son Bluetooth et l'analyseur continuent. Si le serveur ne répond plus, le dernier volume n'est plus affiché comme valeur actuelle : il est remplacé par « — » et l'afficheur s'éteint |
| Mise sous tension | Lors de l'ouverture seulement : apparition douce des panneaux (520 ms, en décalé) et illumination progressive des afficheurs (900 ms). Durée totale d'environ 1 s, interactions jamais bloquées, rien de rejoué au changement de thème, aucune animation avec `prefers-reduced-motion` |
| Plein écran | Le sélecteur de thème unique est **déplacé** dans la barre de l'analyseur en plein écran, puis remis dans l'en-tête à la sortie. Le changement de thème reste donc possible en plein écran, sans doublon |
| Correction au passage | Le bouton lecture/pause affichait ses deux icônes en même temps, car l'attribut `hidden` n'est pas appliqué aux SVG intégrés dans la page. Une règle CSS corrige cela |

## 2. Fichiers

| Fichier | Statut | Rôle |
|---|---|---|
| `static/theme.js` | **Nouveau** | Thèmes, chargement et sauvegarde, migration, application, événement `devialet:themechange`, déplacement du sélecteur, mise sous tension |
| `static/link-status.js` | **Nouveau** | États de connexion (fonction pure) |
| `static/index.html` | Réorganisé | Nouvel ordre ; identifiants existants conservés ; sélecteur de l'analyseur supprimé |
| `static/app.css` | Réécrit | Variables de thème, façade, console, responsive, mise sous tension. Les palettes `.spectrum[data-theme]` sont **reprises à l'identique** |
| `static/app.js` | Affichage seulement | Voyant de connexion, résumé du panneau système, valeurs non confirmées masquées si le serveur ne répond plus |
| `static/spectrum.js` | Thème seulement | Palette lue depuis le thème global ; déplacement du sélecteur en plein écran. Rendu, données et animations inchangés |
| `tests/js/theme.test.cjs`, `tests/js/link-status.test.cjs`, `tests/test_hifi_console.py` | **Nouveaux** | Voir §5 |
| `tests/test_spectrum_visual.py` | Adapté | Le test V2.3.1 attendait `#spectrum-theme`, supprimé à la demande ; il vérifie désormais `#theme-select` |
| `README.md` | Mis à jour | Section « Façade Hi-Fi » |

`tone.js`, `tone-core.js`, `bluetooth.js`, `discovery.js`, `spectrum-settings.js`, `spectrum-ballistics.js`, le backend et l'agent n'ont pas été modifiés.

## 3. Fonctionnement des thèmes

- **Variables globales**, définies par thème dans `:root[data-theme="…"]` :
  - `--bg`, `--card`, `--card-border`, `--text`, `--muted`, `--line` ;
  - `--accent`, `--accent-text`, `--ok`, `--warn`, `--bad`, `--focus` ;
  - `--field-*` pour les listes déroulantes ;
  - `--panel*` pour les façades sombres (analyseur, console) ;
  - `--display-*` pour les afficheurs ;
  - `--led*` pour les voyants et repères des potentiomètres.
  
  Tous les composants lisent ces variables : fond, en-tête, cartes, boutons, badges, volume, potentiomètres, lecteur, analyseur, Bluetooth, découverte, champs, erreurs, états désactivés et focus clavier.
- **Les thèmes** :

  | Thème | Fond | Accents | Afficheurs | Analyseur |
  |---|---|---|---|---|
  | Classic | Clair, cartes blanches | Or foncé | Ambre | Palette Technics |
  | Technics | Anthracite, panneaux graphite | Cyan | Vert fluorescent | Technics |
  | Pioneer | Bleu nuit | Bleu électrique | Bleus | Pioneer |
  | Vintage Amber | Noir chaud | Orange | Ambre | Vintage Amber |

  Classic garde aussi sa variante sombre lorsque Windows est en mode sombre, comme avant.
- **Contraste** : toutes les paires texte/fond dépassent **4,5:1** (WCAG AA) dans les quatre thèmes, vérifié par un test. Pour y arriver, l'or de Classic est assombri (`#8f6618`, 5,1:1 sur fond blanc).
- **Thème par défaut** (aucune préférence) : Technics. Une installation V2.3.1 reprend le thème choisi dans l'analyseur ; vous étiez sur Pioneer, donc la page s'ouvrira en Pioneer.
- Ajouter un thème : un bloc `:root[data-theme]` et une entrée dans `THEMES` (`theme.js`). Si besoin, une palette d'analyseur s'ajoute avec un bloc `.spectrum[data-theme]`.

## 4. Organisation de la page

```
A. Façade      DEVIALET / PHANTOM II · My Phantom System · ● Connecté · Thème [▾]
B. Lecteur     source · titre · artiste · ⏮ ⏯ ⏭  (aucune métadonnée inventée)
C. Analyseur   20 bandes stéréo, réglages, plein écran
D. Console     BASS (potentiomètre)  —  VOLUME (afficheur, curseur, − +)  —  TREBLE (potentiomètre) · Neutre
E. Bluetooth   état, appareil, qualité, réglages Windows, assistant
F. ▸ Informations système (repliable) : enceintes, IP, firmware, leader, recherche, résultats
```

**Responsive** :
- au-delà de 760 px, BASS, VOLUME et TREBLE sont sur une ligne ;
- de 421 à 760 px, le volume passe en haut et BASS et TREBLE s'alignent en dessous ;
- à 420 px et moins, l'en-tête passe en colonne, les potentiomètres se réduisent à 150 px et les enceintes s'empilent.

Les potentiomètres restent utilisables au toucher (`touch-action: none`).

**Contrôle visuel** (Chrome sans interface, enceintes simulées) : à 1 280, 800, 390 et 360 px, `scrollWidth = clientWidth` dans tous les cas, donc **aucun défilement horizontal**.

## 5. Tests

| Fichier | Contenu | Nombre |
|---|---|---|
| `tests/js/theme.test.cjs` | 4 thèmes, repli, palette dérivée ; application et événement ; sauvegarde et restauration ; stockage bloqué ou corrompu ; **migration V2.3.1** avec préférences de l'analyseur intactes ; priorité du thème global ; plein écran jamais mémorisé ; thème posé avant le premier affichage ; mise sous tension unique ; sélecteur (4 options, sauvegarde, synchronisation) ; **déplacement du sélecteur en plein écran, sans doublon** ; changement de thème en plein écran répercuté sur l'analyseur | 13 |
| `tests/js/link-status.test.cjs` | Connecté ; coupure : perdue, reconnexion en cours, connecté ; serveur injoignable ; jamais connecté ; tonalités et libellés | 5 |
| `tests/test_hifi_console.py` | Variables complètes par thème ; noms identiques en JS et CSS ; **contrastes AA** ×4 ; composants branchés sur les variables ; `color-scheme` ; **sélecteur unique** ; script de thème bloquant dans `<head>` ; analyseur suivant le thème et déplacement en plein écran ; ordre des panneaux ; contenu de l'en-tête ; console BASS < VOLUME < TREBLE avec les potentiomètres V2.4.1 ; panneau système et découverte ; identifiants du lecteur et du Bluetooth ; ordre des scripts ; affichage des déconnexions ; règles responsive ; clavier ; mise sous tension et mouvement réduit ; fichiers servis, API et plafond du volume inchangés | 25 |

**Résultats** :
- suite Python complète : **207 réussis**, soit les 182 de la V2.4.1 et 25 nouveaux ;
- suite JavaScript complète (`node --test tests/js/*.test.cjs`) : **69 réussis**, soit 51 + 18.

Les tests des potentiomètres (clavier, souris, double-clic, limitation des envois) et de la tonalité côté serveur passent sans modification, ce qui montre qu'ils fonctionnent toujours après la réorganisation.

## 6. Limites

- **Fréquences de l'analyseur sur smartphone** : sous 400 px environ, les libellés de fréquence se chevauchent dans le canvas. Ce rendu existait déjà ; il n'a pas été changé, car l'analyseur est hors périmètre.
- **Thème par défaut Technics** : une nouvelle installation s'ouvre en sombre. Classic reste disponible dans le sélecteur.
- **Ancienne clé** : `devialet.spectrum.theme` reste dans le stockage pour compatibilité. Elle n'est plus lue une fois `devialet.theme` enregistré.
- **Écrans « déconnexion »** : vérifiés par les tests, mais pas par une capture avec une vraie coupure réseau.

## 7. Validation dans Chrome

Le serveur du port 8000 relit les fichiers statiques : **il n'y a pas à le redémarrer**, et le backend n'a pas changé.

1. Ouvrez **http://localhost:8000** et rechargez avec **Ctrl+F5**. La page doit s'ouvrir dans votre thème précédent (Pioneer), avec la courte animation de mise sous tension.
2. **Thèmes** : changez le thème dans l'en-tête. Toute la page change immédiatement, y compris les afficheurs, les repères des potentiomètres et l'analyseur. Rechargez : le choix est conservé, sans flash d'un autre thème.
3. **Préférences** : vérifiez que la sensibilité, le peak hold, sa durée et le mode stéréo/mono sont ceux d'avant.
4. **Plein écran** : passez en plein écran. Le sélecteur de thème apparaît dans la barre de l'analyseur ; changez de thème, puis quittez avec Échap. Le sélecteur revient dans l'en-tête. Au rechargement, le plein écran n'est pas rétabli.
5. **Console** : tournez BASS et TREBLE à la souris et au clavier (Tab, puis les flèches) ; double-clic pour 0 dB ; « Neutre ». Réglez le volume au curseur et avec − et + ; le plafond de 50 est inchangé.
6. **Informations système** : dépliez le panneau. Vous y trouvez les enceintes, les IP, le firmware, le badge Leader et le bouton « Rechercher les enceintes ».
7. **Responsive** : F12, puis la barre d'outils des appareils (Ctrl+Maj+M), en 390 px et 800 px : pas de défilement horizontal, contrôles utilisables.
8. **Déconnexion** (facultatif) : coupez le Wi-Fi du PC quelques secondes. Le voyant passe par « Connexion perdue » puis « Reconnexion en cours… », et revient à « Connecté ». L'analyseur continue.
9. **Mouvement réduit** (facultatif) : F12, ouvrez le panneau Rendering, puis choisissez « Emulate CSS prefers-reduced-motion: reduce ». Il n'y a plus d'animation au chargement.

Tests :
```bash
cd ~/projects/devialet && .venv/bin/python -m pytest -q
node --test tests/js/*.test.cjs
```
