# Développement

Travailler sur le code de CyberLearning sur son poste. Pour installer une instance chez un client, voir
[installation.md](installation.md) ; pour proposer une modification, [CONTRIBUTING.md](../CONTRIBUTING.md).

**Stack** : Django 5.2 LTS · PostgreSQL 17 · Gunicorn · nginx (TLS) · Docker Compose · IA locale facultative
(Ollama, Kyutai TTS).

---

## Architecture

```
        Poste de développement               GitHub                    Instances clientes
 ┌───────────────────────────────┐  push  ┌──────────────┐  archive  ┌────────────────────────────┐
 │ docker compose (surcouche dev) │ ─────► │ dépôt + CI   │  signée   │ install.sh / deploy.sh      │
 │ http://localhost:8000          │        │ (tests, audit)│ ────────► │ nginx ─► web ─► db          │
 │ + IA locale (profil « ia »)    │        └──────────────┘  + packs  │ (+ IA si carte graphique)   │
 └───────────────────────────────┘                          de contenus└────────────────────────────┘
```

Seul nginx publie des ports. L'application, la base et l'IA sont sur des réseaux Docker internes, sans accès à
Internet (voir [securite.md](securite.md)).

## Organisation du code

```
.
├── app/                     Code Django
│   ├── config/              Réglages (settings.py), URLs, pages légales, contexte des gabarits
│   ├── accounts/            Comptes, rôles, double authentification, journal d'audit, RGPD, limites de débit
│   ├── catalogue/           Guides, référentiels, certifications, lecture, QCM, recherche, versions audio,
│   │                        lexiques, assistant, générations par IA, packs de contenus (packs.py)
│   ├── courses/             Cours (leçons, quiz, examen) et leur génération
│   ├── podcasts/            Podcasts à deux voix et leur génération
│   ├── studio/              Atelier de création (ajouts, relecture, aide de l'IA)
│   ├── progression/         XP, niveaux, émeraudes, boutique
│   ├── social/              Amis
│   ├── templates/  static/  Gabarits et fichiers statiques (CSS, JS sans script en ligne : CSP stricte)
│   └── VERSION              Numéro de version de l'application
├── tts/                     Service de synthèse vocale (image séparée, GPU)
├── nginx/templates/         Configuration de nginx (gabarit rempli au démarrage)
├── scripts/                 install.sh, deploy.sh, backup.sh, restore.sh, mettre-a-jour-contenus.sh,
│                            publier-version.sh, publier-contenus.sh, dev.ps1 (raccourci .\dev)
├── docs/                    Documentation (ce dossier)
├── Dockerfile               Image de l'application, multi-étapes, utilisateur non-root
├── docker-compose.yml       Services (production et base du développement)
├── docker-compose.dev.yml   Surcouche de développement (runserver, code monté, port 8000)
├── .env.example             Modèle de configuration (le vrai .env n'est jamais commité)
└── .github/                 CI (tests, check --deploy, Bandit, pip-audit, Compose) et Dependabot
```

---

## Créer le dépôt GitHub (une fois)

1. Sur GitHub : **New repository** → **Private** → aucune option d'initialisation.
2. Dans le dossier du projet :
   ```bash
   git init -b main
   git add .
   git status                # vérifiez qu'aucun .env, certificat ni clé n'apparaît
   git commit -m "Initialisation du projet CyberLearning"
   git remote add origin git@github.com:<compte>/CyberLearning.git
   git push -u origin main
   ```
3. Protégez la branche principale : **Settings → Branches → Add rule** sur `main` (*Require status checks*).
4. Activez **Settings → Code security** : *Dependabot alerts*, *Secret scanning*, *Push protection*.

## Démarrer en local

```bash
cp .env.example .env
# Dans .env : DJANGO_SECRET_KEY (50 caractères au moins) et POSTGRES_PASSWORD (commandes dans le fichier),
#             DJANGO_ALLOWED_HOSTS=localhost, SIGNUP_ENABLED=1 si besoin
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Puis http://localhost:8000. L'administration est sur `http://localhost:8000/<DJANGO_ADMIN_URL_PATH>/`.

**Sous Windows**, le raccourci `.\dev` (PowerShell, à la racine du projet) remplace ces longues commandes :

```powershell
.\dev up          # construit et démarre (http://localhost:8000)
.\dev test        # lance les tests (.\dev tq : seulement les échecs et le résumé)
.\dev superuser   # crée le compte administrateur
.\dev import      # charge les QCM de catalogue/data/quiz/ et indexe le texte des guides
.\dev ia          # démarre l'IA locale (Ollama, voix, files de génération)
.\dev logs        # suit les journaux
.\dev down        # arrête tout
.\dev help        # liste complète
```

Sous Linux/macOS : `alias dc='docker compose -f docker-compose.yml -f docker-compose.dev.yml'`.

> Les comptes à droits élevés doivent activer la double authentification à la connexion (application TOTP).
> En développement seulement, `MFA_REQUIRED_FOR_PRIVILEGED=0` dans `.env` la rend facultative.

## Branches et versions

| Branche | Contenu | Qui y écrit |
|---|---|---|
| `dev` | le développement en cours : tout commit du quotidien, les mises à jour Dependabot | développeurs |
| `main` | uniquement des versions publiées, chacune étiquetée `vX.Y.Z` : c'est ce que reçoivent les clients | publication d'une version seulement |

```
dev   ──●──●──●──●──────●──●──●───────▶   travail quotidien
               \          main  ──────────●  v1.0.0   ●  v1.1.0 ──▶   versions livrées (fusion de dev, puis publier-version.sh)
```

- Chaque changement suit **une issue → une branche → une PR vers `dev`** :
  1. `.\dev feature "titre"` crée l'issue et la branche `feat/N-titre` depuis `dev` (`.\dev feature 15` : issue existante) ;
  2. commits sur cette branche, une étape cohérente par commit ;
  3. `.\dev pr` lance `.\dev tq`, pousse la branche et ouvre la PR (« Closes #N » : l'issue se ferme à la fusion) ;
  4. `.\dev merge [n° PR]` attend la CI verte, fusionne (commit de fusion) et revient sur `dev` à jour.
- `dev` est la branche par défaut sur GitHub (sans quoi « Closes #N » n'agirait qu'à la publication sur `main`).
- GitHub ne protège pas les branches d'un dépôt privé gratuit : `.githooks/pre-push` refuse sur le poste tout push
  direct sur `dev` ou `main` (`.\dev` active le hook via `core.hooksPath`). Seule la publication d'une version le
  contourne, avec `CL_PUBLICATION=1 git push ...`.
- Ne commitez jamais directement sur `main`. Une version y arrive par fusion de `dev`, puis
  `publier-version.sh`, qui refuse de s'exécuter ailleurs.
- Un correctif urgent sur une version livrée : faites-le sur `dev`, puis publiez une version corrective (`X.Y.Z+1`).
- Les outils de développement et de l'éditeur (`.github/`, `.\dev`, Compose de dev,
  `publier-*.sh`, ce document et le guide de publication) restent dans le dépôt, sur les deux branches, mais sont
  exclus de l'archive livrée aux clients (`export-ignore` dans `.gitattributes`).

## Règles de travail

- **Tests** : `.\dev tq` avant chaque commit (`.\dev test` pour la sortie complète) ; la CI les relance avec `check --deploy`, Bandit, pip-audit et la
  validation des fichiers Compose et des scripts.
- **Modèles** : après une modification, `.\dev makemigrations` puis `.\dev migrate`, et commitez la migration avec
  le modèle. Une migration doit tourner sur une base remplie et sans perte de données.
- **Nouveau réglage** : valeur par défaut sûre dans `settings.py` (les instances existantes ne l'ont pas dans leur
  `.env`), ajout dans `.env.example`, mention dans les notes de version.
- **Gabarits** : pas de script en ligne (la CSP l'interdit) ; tout le JavaScript est dans `static/js/app.js`.
- **Dépendances** : on modifie `requirements.in` (production) ou `requirements-dev.in` (outils), jamais les `.txt`
  directement. Puis `bash scripts/verrouiller-dependances.sh` régénère `requirements.txt` et `requirements-dev.txt`
  (versions exactes et empreintes, vérifiées par `pip install --require-hashes`) ; `--upgrade` monte les versions.
  Reconstruisez ensuite l'image (`.\dev up`), y compris pour les services d'IA qui la partagent.
- **Hooks Git** : `.githooks/pre-commit` refuse un commit contenant un secret (gitleaks, Docker requis) ;
  `.githooks/pre-push` refuse le push direct sur `dev` et `main`.
- **Secrets** : jamais de `.env`, `.env.db`, `.backup-key`, clé de signature, certificat ni fichier de `app/media/`
  dans Git.
- **Livrer** : `scripts/publier-version.sh` (versions) et `scripts/publier-contenus.sh` (packs de contenus).

## Pistes

- Quiz de révision espacée et rappels pour entretenir les connaissances.
