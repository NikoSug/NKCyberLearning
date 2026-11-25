# Politique de sécurité

## Signaler une vulnérabilité

Ne créez pas d'issue publique. Utilisez le signalement privé de GitHub (onglet **Security** →
**Report a vulnerability**) en décrivant le composant touché, comment
reproduire et l'impact estimé.

## Modèle de menace

| Question | Réponse |
| --- | --- |
| Quelles données sensibles ? | Comptes des apprenants et des auteurs (identifiant, e-mail, mot de passe haché Argon2, secret de double authentification), progression et résultats, journal d'audit. Données personnelles au sens du RGPD. |
| Qui peut accéder à quoi ? | Apprenant : ses propres données et le catalogue publié. Auteur : ses contenus dans l'atelier. Gestionnaire de contenus et administrateur : relecture, publication, comptes. Contrôle côté serveur sur chaque vue (`accounts/tests_access.py`). |
| Points d'entrée exposés ? | Application web derrière nginx (HTTPS), formulaires de connexion et d'inscription, atelier, imports de documents (PDF, Excel), flux RSS externes, packs de contenus signés. |
| Services tiers qui reçoivent des données ? | Aucun par défaut : les générations d'IA (Ollama, synthèse vocale) tournent sur l'instance. Let's Encrypt pour le certificat. |
| Pire scénario ? | Prise de contrôle d'un compte administrateur ; fuite de la base (données des apprenants) ; vol de la clé de signature des contenus, qui permettrait de diffuser des packs falsifiés. |

## Mesures en place

Détail : [docs/securite.md](docs/securite.md).

- Mots de passe Argon2, limitation des tentatives (django-axes), double authentification, CSP sans script en ligne.
- Secrets hors du dépôt (`.env`, `.backup-key`, clé de signature) ; scan gitleaks en pre-commit et en CI.
- Dépendances verrouillées avec empreintes (`requirements.txt`) ; Dependabot, pip-audit, bandit et Trivy en CI.
- Conteneurs non-root, image de production sans pip, images de base à version fixée.

## Points d'attention

- La clé privée de signature des contenus (`contenus-signature.key`) ne doit vivre que dans un coffre (gestionnaire
  de mots de passe), jamais dans un dossier synchronisé ni dans l'image Docker.
