# CyberLearning

Plateforme pour apprendre et entretenir ses connaissances en cybersécurité, pensée pour les guides, référentiels
et certifications (ceux de l'ANSSI comme les vôtres). Une instance par organisation, installée sur site ou dans le
cloud.

Le dépôt fournit l'application, sans contenu : une instance neuve démarre avec un catalogue vide, que l'on remplit
depuis l'atelier (dépôt des PDF, trames d'évaluation, cours, QCM…) ou avec un pack de contenus.

- **Apprendre** : catalogue des guides (lecture, recherche dans le texte complet, versions audio), tests de
  connaissances, révision du jour, cours (leçons, quiz, examen), podcasts à deux voix, lexiques, parcours de lecture,
  progression et récompenses.
- **Produire** : atelier de création pour les rédacteurs, relecture et publication par les gestionnaires de
  contenu, aide d'une IA locale facultative (aucune donnée envoyée à l'extérieur).
- **Exploiter** : installation en une commande, mises à jour de l'application et des packs de contenus vérifiées
  par signature, double authentification, journal d'audit, sauvegardes chiffrées, conformité RGPD.

**Stack** : Django 5.2 LTS · PostgreSQL 17 · nginx · Docker Compose · Ollama et Kyutai TTS (facultatifs).
Version actuelle : voir [app/VERSION](app/VERSION) et les [notes de version](docs/notes-de-version.md).

## Documentation

Toute la documentation est dans [docs/](docs/README.md) :

| Pour | Document |
|---|---|
| Installer une instance | [docs/installation.md](docs/installation.md) · [English](docs/installation.en.md) |
| Mettre à jour une instance (administrateur client) | [docs/mettre-a-jour-une-instance.md](docs/mettre-a-jour-une-instance.md) |
| Connaître les fonctions | [docs/fonctionnalites.md](docs/fonctionnalites.md) |
| Sécurité | [docs/securite.md](docs/securite.md) |
| Développer, contribuer | [docs/developpement.md](docs/developpement.md) · [CONTRIBUTING.md](CONTRIBUTING.md) |
| Ce qui change d'une version à l'autre | [docs/notes-de-version.md](docs/notes-de-version.md) |

## Démarrage rapide (développement, branche `dev`, Windows)

```powershell
copy .env.example .env      # puis renseigner DJANGO_SECRET_KEY et POSTGRES_PASSWORD
.\dev up                    # http://localhost:8000
.\dev superuser
.\dev import
```

Détails : [docs/developpement.md](docs/developpement.md).

## Licence

Logiciel libre distribué sous la licence [CeCILL v2.1](LICENSE) (compatible GNU GPL). Les versions
française et anglaise du contrat font également foi ; texte français : <https://cecill.info>.
