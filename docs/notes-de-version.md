# Notes de version

Chaque version de l'application a sa section, de la plus récente à la plus ancienne. Les packs de contenus ont leur
propre numérotation (date) et ne figurent pas ici. Chaque section se termine par « À faire après la mise à jour » :
lisez-la avant de mettre à jour une instance.

## 1.2.0 — 19 octobre 2026

Le tableau de bord des générations donne les mêmes chiffres pour chaque type de contenu, temps de génération compris.

**Nouveautés**
- **Temps de génération pour tous les types** : QCM, cours, podcasts, lexiques et versions audio affichent chacun la
  durée du dernier document généré, la moyenne des dernières générations, le temps écoulé de la tâche en cours et
  une estimation du temps restant (« — » quand l'information n'existe pas, pour les générations anciennes).
- **Mêmes compteurs partout** : faits sur le total, en file, en cours, échecs ; les tuiles du haut signalent aussi
  les échecs.
- **Podcasts** : la durée d'écriture du script et celle de l'enregistrement sont conservées séparément ; le temps
  restant tient compte des séries qui n'attendent plus que l'enregistrement.

**À faire après la mise à jour**
- Rien : la migration s'applique au démarrage et reprend les durées d'enregistrement des séries déjà publiées.

## 1.1.0 — 19 octobre 2026

Un planning de lecture qui s'adapte au rythme de chacun, et une gamification qui ne pénalise plus les jours sans
lecture prévus.

**Nouveautés**
- **Jours de lecture** : dans « Mon planning », chacun choisit les jours de la semaine où il lit (du lundi au
  vendredi proposé pour un nouveau planning) et son heure de lecture.
- **Ajouter à mon agenda** : le planning s'exporte en fichier `.ics`, un rendez-vous par jour de lecture, à importer
  dans Thunderbird, Google Agenda, Proton Calendar ou Outlook.
- **Jours de repos** : les jours absents du planning ne cassent plus la série de jours (flammes) et ne consomment pas
  de gel ; apprendre quand même ces jours-là prolonge la série.
- **Mode vacances** (Mon compte → Sécurité et données) : jusqu'à 90 jours sans lecture planifiée, série protégée,
  aucun gel utilisé ; le planning reprend automatiquement au retour.
- **Page « Mon planning » réorganisée** : réglages du rythme de lecture, chiffres clés, export vers l'agenda.

**Corrections**
- Versions audio : un passage qui faisait boucler l'IA (réponse sans fin) ne fait plus échouer tout le guide ; il
  est retenté, puis lu avec l'adaptation automatique si besoin.
- Les liens vers une section de page (ex. « Mode vacances ») ne sont plus masqués par la barre du haut.
- Tests d'inscription indépendants de la configuration locale (intégration continue).

**À faire après la mise à jour**
- Rien : les migrations s'appliquent au démarrage ; les plannings existants gardent « tous les jours » et l'heure
  12 h 30.

## 1.0.0 — 16 octobre 2026

Première version installable chez un client, sur site ou dans le cloud.

**Nouveautés**
- Catalogue des guides, référentiels et certifications de l'ANSSI, lecture et recherche dans le texte complet.
- Tests de connaissances (QCM) par guide, révision du jour, cours (leçons, quiz, examen), podcasts à deux voix,
  versions audio des guides, lexiques, parcours de lecture, progression et récompenses.
- Atelier de création pour les rédacteurs, relecture et publication par les gestionnaires de contenu.
- IA locale facultative (génération, assistant de lecture, synthèse vocale), sans envoi de données à l'extérieur.
- Installation par `scripts/install.sh`, packs de contenus et mises à jour signées.

**Sécurité**
- Double authentification obligatoire pour les rôles élevés, journal d'audit, déconnexion après inactivité.
- Réseaux internes sans accès Internet, rôle de base de données sans droits d'administration, sauvegardes chiffrées.

**À faire après la mise à jour**
- Rien (première version).
