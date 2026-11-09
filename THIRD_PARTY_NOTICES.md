# Tiers : licences et mentions

## Ce que couvre la licence MIT du dépôt

Uniquement le code et la documentation originaux de ce dépôt :
- le contrôleur web (`devialet_web/`) et l'outil de diagnostic (`devialet_diag/`) ;
- l'agent Windows (`agent/windows/`), les scripts (`scripts/`) et le prototype (`prototypes/`) ;
- les tests (`tests/`), les exemples (`examples/`) et les rapports publiés (`reports/`).

## Ce que ce dépôt ne contient pas, et ne doit jamais contenir

- L'application Android Devialet (APK), ses ressources, ses bibliothèques natives et son code décompilé.
- Les schémas protobuf reconstruits depuis cette application.
- Les clés et identifiants de services tiers présents dans cette application.
- Tout firmware ou fichier de configuration provenant d'une enceinte.

L'API locale « IP Control » des enceintes est utilisée telle qu'elle répond sur le réseau. Le dépôt ne fournit aucune documentation officielle de Devialet.

## Marques

« Devialet » et « Phantom » sont des marques de Devialet. Ce projet est **indépendant**. Il n'est ni affilié à Devialet, ni approuvé, ni soutenu par Devialet. Les marques ne sont citées que pour indiquer la compatibilité.

## Dépendances Python (installées par pip, non redistribuées)

| Paquet | Licence | Rôle |
|---|---|---|
| FastAPI, Starlette, Pydantic | MIT | serveur web et validation |
| uvicorn | BSD-3-Clause | serveur ASGI |
| httpx, httpcore | BSD-3-Clause | client HTTP vers les enceintes |
| websockets | BSD-3-Clause | WebSocket |
| zeroconf | LGPL-2.1-or-later | découverte mDNS, utilisée sans modification |
| bleak | MIT | diagnostic BLE |
| pytest | MIT | tests |

Les versions testées sont dans `constraints.txt`. Vérifiez les licences des versions que vous installez : elles peuvent changer.

## Logiciels externes utilisés, mais non fournis

| Logiciel | Licence | Remarque |
|---|---|---|
| Equalizer APO | GPL-2.0 | à installer par l'utilisateur ; le contrôleur n'écrit que `devialet-eq.txt` dans son dossier |
| .NET Framework `csc.exe` | Microsoft | compilateur fourni avec Windows, utilisé par `agent/windows/build.cmd` |
| Ghidra, JADX, Apktool, OpenJDK | Apache-2.0 ; GPL-2.0 avec Classpath Exception | outils d'analyse éventuels, jamais inclus dans le dépôt |
