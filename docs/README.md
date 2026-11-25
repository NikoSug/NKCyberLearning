# Documentation de CyberLearning

## Par besoin

| Je veux… | Document | Pour qui |
|---|---|---|
| Installer une nouvelle instance | [Installation](installation.md) · [English](installation.en.md) | administrateur client, éditeur |
| Appliquer une mise à jour | [Mettre à jour une instance](mettre-a-jour-une-instance.md) | administrateur client |
| Savoir ce que fait la plateforme | [Fonctionnalités](fonctionnalites.md) | tous |
| Connaître les mesures de sécurité | [Sécurité](securite.md) | administrateur, RSSI, client |
| Savoir ce qui a changé | [Notes de version](notes-de-version.md) | tous |
| Modifier le code | [Développement](developpement.md) | développeur (non livré aux clients) |
| Justifier la configuration pylint et les remarques restantes | [Qualité du code](qualite-code.md) | développeur, relecteur |

## L'essentiel

- **Une instance par client**, sur site ou dans le cloud, installée par `./scripts/install.sh`.
- **Deux sortes de mises à jour**, livrées et signées par l'éditeur :

  | | Application | Contenus |
  |---|---|---|
  | Numérotation | `X.Y.Z` (fichier `app/VERSION`) | date (`2026.11`) |
  | Côté éditeur | `./scripts/publier-version.sh` | `./scripts/publier-contenus.sh` |
  | Côté client | `./scripts/deploy.sh` | `./scripts/mettre-a-jour-contenus.sh` (automatique si un canal est configuré) |

- Avant chaque mise à jour, une **sauvegarde chiffrée** est faite ; les comptes, la progression et les réglages des
  utilisateurs ne sont **jamais** touchés.

## Règle de rédaction

La documentation vit ici, dans le dépôt, avec le code : toute modification qui change une procédure, une commande
ou un réglage met à jour le document concerné dans le même commit, et ajoute une ligne aux
[notes de version](notes-de-version.md).
