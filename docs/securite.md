# Sécurité

Mesures de sécurité de la plateforme et de son installation. Détail de l'installation : [installation.md](installation.md).

## Mesures en place

- **Secrets** : uniquement dans `.env` (droits 600, ignoré par Git et par l'image Docker). Valeurs différentes en dev et en prod.
- **Mots de passe** : hachage Argon2id, 12 caractères minimum, mots de passe courants refusés.
- **Brute-force** : django-axes bloque un couple pseudo + IP pendant 30 minutes après 5 échecs. Nginx limite à 10 requêtes par minute les pages de connexion et d'inscription.
- **Double authentification** (codes TOTP d'une application : Proton Pass, Aegis, FreeOTP…) : obligatoire pour les administrateurs, gestionnaires et rédacteurs (`MFA_REQUIRED_FOR_PRIVILEGED=1`), facultative pour les apprenants. 10 codes de secours à usage unique, un code ne sert jamais deux fois, déconnexion après 5 codes faux. Téléphone perdu : un administrateur supprime la ligne dans **Administration → Doubles authentifications**, le compte la réactive à la connexion suivante.
- **Sessions** : cookies `Secure`, `HttpOnly` et `SameSite`, expiration après 8 h ou à la fermeture du navigateur ; les comptes à droits élevés sont déconnectés après 30 min d'inactivité (`PRIVILEGED_IDLE_MINUTES`). Protection CSRF sur tous les formulaires. Déconnexion en POST uniquement.
- **Journal d'audit** (**Administration → Journal d'audit**, en lecture seule ; aussi dans les journaux du conteneur, logger `audit`) : connexions et échecs, double authentification, rôles donnés ou retirés, comptes désactivés, publications, renvois, suppressions, pilotage des générations, export ou suppression de données. Chaque utilisateur voit son activité récente dans **Sécurité et données**.
- **Limites de débit dans l'application** (cache partagé en base) : recherche 30/min, aide de l'IA dans l'atelier 60/h, demandes de version audio 10/h, inscriptions 5/h par adresse ; assistant de lecture 30 questions/h.
- **Transport** : TLS 1.2 et 1.3 avec suites robustes, redirection HTTP vers HTTPS. Les en-têtes CSP, nosniff, Referrer-Policy, Permissions-Policy et anti-clickjacking sont envoyés.
- **Conteneurs** : utilisateur non-root, système de fichiers en lecture seule, toutes les capabilities retirées, `no-new-privileges`. La base n'est accessible que par un réseau interne, et les journaux sont limités en taille.
- **Fichiers** : les PDF sont contrôlés à l'envoi (extension, taille, signature `%PDF-`) et servis uniquement aux utilisateurs connectés, sans URL publique. Les lectures partielles d'audio sont servies par morceaux de 2 Mo au plus (pas de fichier entier chargé en mémoire).
- **Données personnelles (RGPD)** : chaque utilisateur télécharge ses données (JSON) et supprime son compte depuis **Sécurité et données** (un compte à droits élevés est supprimé par un administrateur). `manage.py purge_donnees` applique les durées de conservation : journal d'audit 365 jours (`AUDIT_RETENTION_DAYS`), traces de l'assistant et tentatives de connexion 90 jours, sessions expirées. Lancée à chaque démarrage et chaque nuit (tâche planifiée par `install.sh`).
- **Administration** : chemin personnalisable (`DJANGO_ADMIN_URL_PATH`).
- **Installation** (`scripts/install.sh`, voir [installation.md](installation.md)) : secrets aléatoires, rôle de base de données sans droits d'administration, réseaux internes sans accès Internet, limites de ressources, Let's Encrypt, sauvegardes chiffrées et purge planifiées.
- **Chaîne logicielle** : la CI exécute tests, `check --deploy` (qui refuse une `DJANGO_SECRET_KEY` de moins de 50 caractères), Bandit et pip-audit. Dependabot propose les mises à jour, et l'image de base est rafraîchie à chaque déploiement.

- **Mises à jour** : archives de l'application et packs de contenus signés par l'éditeur et vérifiés par l'instance,
  sauvegarde chiffrée avant chaque mise à jour (voir [mettre-a-jour-une-instance.md](mettre-a-jour-une-instance.md)).
- **Contenus** : sources ANSSI citées (Licence Ouverte v2.0), mentions de marque pour les certifications tierces,
  pages légales remplies avec les réglages de l'instance.

## Recommandations pour le serveur

- Exposé sur Internet : nom de domaine et certificat Let's Encrypt (`install.sh --tls letsencrypt` active aussi HSTS).
- SSH : authentification par clé uniquement, `PermitRootLogin no`.
- Administration réservée au réseau de l'organisation : `ADMIN_ALLOW_FROM` dans `.env`.
- Copie de `.backup-key` dans un coffre-fort, hors du serveur ; test de restauration mensuel.
