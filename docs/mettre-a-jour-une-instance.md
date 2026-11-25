# Mettre à jour une instance (administrateur client)

Ce guide s'adresse à l'administrateur qui exploite une instance CyberLearning, sur site ou dans le cloud. Il
explique comment appliquer les mises à jour livrées par l'éditeur, les vérifier et revenir en arrière si besoin.
Les commandes se lancent depuis le dossier de l'instance (par exemple `~/cyberlearning`), avec le compte qui
l'exploite.

---

## 1. Ce qu'il faut savoir

Il y a deux sortes de mises à jour, indépendantes :

| | Application | Contenus |
|---|---|---|
| Ce qui change | fonctions, écrans, corrections, sécurité | guides, QCM, lexiques, cours, podcasts, versions audio |
| Livrée sous forme de | archive `cyberlearning-X.Y.Z.tar.gz` + `.sig`, ou dépôt Git | pack `cyberlearning-contenus-<version>.tar` + `.sig`, ou canal HTTPS |
| Commande | `./scripts/deploy.sh` | `./scripts/mettre-a-jour-contenus.sh` |
| Interruption de service | 1 à 5 minutes (redémarrage des services) | aucune (import pendant que le site tourne) |
| Automatique ? | non : vous choisissez le moment | oui si un canal est configuré (chaque dimanche à 4 h 30) |

**Ce qui n'est jamais touché par une mise à jour** : les comptes, la progression, les lectures, les résultats des
tests, les réglages (`.env`, `.env.db`), la clé des sauvegardes, les certificats. Une sauvegarde chiffrée est faite
automatiquement avant chaque mise à jour.

**Ordre** : si l'éditeur livre à la fois une version de l'application et des contenus, appliquez **d'abord
l'application**, puis les contenus.

### Connaître sa version

- Application : `cat app/VERSION`, ou en bas de page (visible des administrateurs), ou en tête de l'administration.
- Contenus : `docker compose exec web python manage.py importer_contenus --version-installee`, ou
  **Administration → Imports de contenus** (historique complet).

---

## 2. Avant une mise à jour

1. **Lisez les notes de version** (`docs/notes-de-version.md`, livré avec l'archive) : la rubrique « À faire après la mise à
   jour » indique les éventuelles actions de votre part.
2. **Vérifiez l'espace disque** : `df -h . /var/lib/docker`. Prévoyez au moins 2 fois la taille du pack de contenus
   et 10 Go pour l'application. Un disque plein bloque Docker.
3. **Vérifiez que la clé des sauvegardes est en lieu sûr**, hors du serveur (`.backup-key`) : sans elle, la
   sauvegarde faite avant la mise à jour serait inutilisable.
4. **Choisissez le moment** pour l'application : de préférence hors des heures d'utilisation.

---

## 3. Mettre à jour l'application

### 3.1 Avec l'archive livrée par l'éditeur

Copiez l'archive **et** sa signature sur le serveur (par exemple dans `~/packs/`), puis :

```bash
./scripts/deploy.sh ~/packs/cyberlearning-1.1.0.tar.gz
```

### 3.2 Avec un dépôt Git

```bash
./scripts/deploy.sh
```

### 3.3 Ce que fait le script

1. Vérifie la **signature de l'éditeur** (si `contenus.pub` est présent) : une archive modifiée ou d'une autre
   origine est refusée.
2. Fait une **sauvegarde chiffrée** (base et fichiers).
3. Installe la nouvelle version (archive décompressée, ou dépôt mis à jour) et affiche
   `Version : 1.0.0 → 1.1.0`.
4. Reconstruit les images avec les dernières mises à jour de sécurité du système, redémarre les services et
   applique les migrations de la base.
5. Vérifie la configuration de sécurité.

### 3.4 Après

- Appliquez la rubrique « À faire après la mise à jour » des notes de version (souvent : rien ; parfois un réglage
  à ajouter dans `.env` suivi de `docker compose up -d`).
- Vérifiez (§ 5).

---

## 4. Mettre à jour les contenus

### 4.1 Automatiquement (canal de mise à jour)

Si `CONTENT_UPDATE_URL` est renseigné dans `.env`, rien à faire : chaque dimanche à 4 h 30, l'instance vérifie si
une nouvelle version est publiée, la télécharge, vérifie sa signature, fait une sauvegarde et l'importe. Le
compte rendu est dans `backups/contenus.log`.

Pour lancer la vérification tout de suite :

```bash
./scripts/mettre-a-jour-contenus.sh
```

### 4.2 Avec un pack reçu

Copiez le pack **et** sa signature sur le serveur, puis faites d'abord un **essai** (rien n'est modifié), puis
l'import :

```bash
./scripts/mettre-a-jour-contenus.sh ~/packs/cyberlearning-contenus-2026.11.tar --essai
./scripts/mettre-a-jour-contenus.sh ~/packs/cyberlearning-contenus-2026.11.tar
```

### 4.3 Lire le bilan

```
Pack 2026.11 (produit par CyberLearning, 2026-11-12) : 912 fichiers vérifiés.
  Guides                         3 créés     12 mis à jour    171 inchangés     1 conservés    0 erreurs
      · guide-hygiene (modifié sur cette instance)
```

| Colonne | Signification |
|---|---|
| créés | nouveaux contenus ajoutés |
| mis à jour | contenus remplacés par leur nouvelle version, **sur place** : progression, tests passés et écoutes restent valables |
| inchangés | identiques au pack précédent |
| conservés | **non remplacés**, avec la raison : modifié sur cette instance, retiré ou repris en rédaction ici, écrit par un rédacteur de l'instance, guide absent |
| erreurs | contenus non importés (le reste l'est) : transmettez le bilan à l'éditeur |

Rien n'est jamais supprimé : un contenu retiré du pack par l'éditeur reste chez vous.

### 4.4 Récupérer la version de l'éditeur d'un contenu modifié ici

Les contenus « conservés » gardent vos modifications. Pour les remplacer quand même par la version de l'éditeur :

```bash
./scripts/mettre-a-jour-contenus.sh ~/packs/cyberlearning-contenus-2026.11.tar --forcer
```

Les contenus écrits par vos propres rédacteurs ne sont pas concernés : ils ne viennent pas de l'éditeur.

---

## 5. Vérifier après une mise à jour

```bash
docker compose ps                                   # tous « Up », web « healthy »
curl -sI https://<adresse-du-site> | head -n 1      # HTTP 200
cat app/VERSION                                     # nouvelle version de l'application
docker compose exec web python manage.py importer_contenus --version-installee   # version des contenus
```

Puis, dans le navigateur : connexion, ouverture d'un guide, d'un cours et d'un test ; la progression d'un
apprenant est toujours là.

---

## 6. Revenir en arrière

### Application

La sauvegarde faite juste avant la mise à jour est dans `backups/` (fichiers `db-<date>.dump.enc` et
`media-<date>.tar.gz.enc` les plus récents). Réinstallez la version précédente, **puis** restaurez la base :

```bash
./scripts/deploy.sh ~/packs/cyberlearning-1.0.0.tar.gz
./scripts/restore.sh backups/db-<date>.dump.enc backups/media-<date>.tar.gz.enc
```

Ne revenez pas à une version antérieure sans restaurer la base : la nouvelle version a pu la modifier
(migrations).

### Contenus

Un import ne supprime rien et ne touche pas aux données des utilisateurs. En cas de contenu erroné, demandez à
l'éditeur un pack corrigé. Pour revenir exactement à l'état d'avant, restaurez la sauvegarde faite avant l'import
(`restore.sh`, comme ci-dessus) : attention, la progression enregistrée depuis serait perdue.

---

## 7. En cas de problème

| Message | Cause et solution |
|---|---|
| `Signature absente` / `Signature invalide` | Le `.sig` n'a pas été copié à côté du fichier, ou le fichier a été modifié ou ne vient pas de l'éditeur. Récupérez à nouveau les deux fichiers ; ne contournez pas ce contrôle. |
| `Ce pack vient d'une version plus récente de l'application` | Mettez d'abord l'application à jour (§ 3), puis relancez l'import des contenus. |
| `Fichier altéré dans le pack` / `Données du pack altérées` | Téléchargement ou copie incomplet : récupérez à nouveau le pack. |
| `Read-only file system`, Docker ne répond plus | Disque plein : libérez de l'espace (voir [installation.md](installation.md), § 12.3). |
| `Contrôle de sécurité Django en échec` après `deploy.sh` | `docker compose exec web python manage.py check --deploy` indique le réglage en cause (souvent un réglage nouveau à ajouter dans `.env`, voir les notes de version). |
| Le site ne répond plus après la mise à jour | `docker compose logs web` (fin du journal), puis contactez l'éditeur avec ce journal ; au besoin, revenez en arrière (§ 6). |

Pour toute demande à l'éditeur, joignez : la version (`cat app/VERSION`), le bilan ou le journal concerné, et la
date de la mise à jour.
