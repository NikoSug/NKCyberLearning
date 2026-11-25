# Fonctionnalités

Ce que fait la plateforme, fonction par fonction, et comment chaque fonction est produite (import, IA locale, relecture). Pour l'installation et l'exploitation, voir le [sommaire de la documentation](README.md).

## Sommaire

- [Rôles et droits](#rôles-et-droits)
- [Contenus](#contenus)
- [Recherche](#recherche-et-filtres-du-catalogue)
- [Versions audio](#version-audio-des-guides-synthèse-vocale)
- [QCM](#génération-des-qcm-par-ia-locale-ollama)
- [Cours](#cours-générés-par-ia-locale)
- [Podcasts](#podcasts-générés-par-ia-locale)
- [Atelier de création](#atelier-de-création-ajouts-par-les-rédacteurs-et-les-gestionnaires)
- [Lexiques](#lexique-des-guides-sigles-et-termes)
- [Relecture](#relecture)
- [Tableau de bord des générations](#tableau-de-bord-des-générations)
- [Assistant de lecture](#assistant-de-lecture)

Les commandes `.\dev …` sont les raccourcis du poste de développement ; sur un serveur, utilisez `docker compose exec web python manage.py …`.

## Rôles et droits

| Rôle | Comment l'attribuer | Peut faire |
|---|---|---|
| Utilisateur | Créé par un administrateur (`createsuperuser`, administration), ou inscription publique si `SIGNUP_ENABLED=1` | Apprendre : guides, tests, cours, podcasts, parcours, progression |
| Rédacteur | Menu **Gestion → Utilisateurs et rôles** (administrateur) | Écrire tous les types de contenus dans l'atelier de création, avec l'aide de l'IA ; ils sont relus avant publication |
| Gestionnaire de contenu | Menu **Gestion → Utilisateurs et rôles** (donne aussi le statut équipe), ou l'administration | Relire et publier ce qui est écrit ou généré, piloter les générations, rédiger ; ajouter et modifier guides, catégories, certifications ; consulter les lectures |
| Administrateur | `createsuperuser` ou **Statut superutilisateur** | Tout, y compris la gestion des comptes |

Un compte créé par le formulaire public n'a jamais de droits élevés. Le groupe est recréé automatiquement à chaque migration. L'historique des lectures est en lecture seule, même pour l'administrateur.

## Contenus

Le dépôt ne livre aucun contenu : une instance neuve a un catalogue vide. On le remplit :

- depuis l'**atelier** (`/atelier/`) : domaines, guides (dépôt du PDF : nombre de pages, temps de lecture estimé à
  2,5 min par page, texte extrait), référentiels (grille tirée d'une trame d'évaluation Excel ou du PDF),
  certifications et documents cités ;
- avec un **pack de contenus** (`scripts/mettre-a-jour-contenus.sh`, voir docs/mettre-a-jour-une-instance.md) ;
- pour les QCM, avec des fichiers `catalogue/data/quiz/<identifiant du guide>.json` chargés par
  `python manage.py import_quizzes` (format décrit en tête de la commande).

`.\dev import` charge ces QCM et indexe le texte des guides.

## Recherche et filtres du catalogue

- **Recherche** (loupe de la barre du haut, `catalogue/search.py`) : guides, **texte complet des guides** (extrait centré sur le passage trouvé), cours et podcasts publiés, lexique (le sigle exact d'abord, avec sa définition), référentiels et certifications. Un onglet par type avec le nombre de résultats ; « Tout » montre les meilleurs de chaque type. Sans tenir compte des accents ni des majuscules, mots cherchés surlignés, classement par pertinence (titre, puis résumé, puis reste). Filtres : domaine, niveau, format (version audio disponible, avec QCM).
- **Texte des guides** : extrait des PDF et conservé (`GuideSection`, le même que celui de l'assistant de lecture). `.\dev import` ou `python manage.py index_texts` l'indexe pour les nouveaux guides (`--refaire` pour tout réextraire).
- **Catalogue** : filtre « Avec / Sans version audio » à côté du filtre QCM, et badge « 🎧 Audio » (lien vers le lecteur) sur chaque guide dont la version audio est prête.

## Version audio des guides (synthèse vocale)

Chaque guide peut être écouté (bouton « 🎧 Écouter » sur sa fiche). La version audio est générée à la première demande :

- **`tts`** (profil `ia`, carte graphique NVIDIA) : Kyutai TTS 1.6B (modèle français/anglais, licence CC BY 4.0) avec la voix « développeuse » enregistrée par Kyutai (CC0), via la bibliothèque `moshi` (MIT). Modèle et voix sont téléchargés au premier usage dans le volume `tts_models`, à des révisions figées (`tts/server.py`). Le modèle (≈ 4 Go de mémoire graphique) n'est chargé que pendant la lecture d'un guide, puis libéré. Le service n'est joignable que sur le réseau interne et n'est jamais importé par le code de l'application (échanges HTTP uniquement).
- **`narrator`** (profil `ia`) : file d'attente (`manage.py narrate --boucle`). Il extrait le texte du PDF par chapitres (`catalogue/narration_text.py` : sommaire, en-têtes et pieds de page retirés…), le fait **réécrire pour l'oral** par l'IA locale (`catalogue/narration_script.py` : listes en phrases, sigles développés une fois, références et artefacts retirés, sans rien ajouter), puis le fait lire par `tts` et encode un MP3 par chapitre (96 kbit/s, volume normalisé). Les deux phases se suivent pour ne jamais avoir l'IA et la voix ensemble sur une carte de 8 Go : l'IA est déchargée avant la lecture. Réécritures et chapitres lus sont gardés : une pause ou une interruption reprend où elle s'était arrêtée.
- **Robustesse** : si la carte graphique part en erreur (« CUDA error: unknown error », pilote réinitialisé…), le service `tts` se déclare indisponible et redémarre seul ; le `narrator` et `podcastgen` attendent et réessaient environ 3 minutes (carte saturée ou voix qui redémarre) avant de compter un échec.
- **Assistant en pause pendant la synthèse** : tant qu'une voix enregistre (audio des guides, podcasts), l'assistant répond « en pause pendant l'enregistrement d'un audio » au lieu de solliciter l'IA, qui ne tient pas en même temps que la voix sur une carte de 8 Go. Il reprend dès que la voix libère la carte (au plus tard 3 minutes après le dernier passage lu).
- Le lecteur enchaîne les chapitres, reprend où l'on s'était arrêté et règle la vitesse. Écouter tous les chapitres jusqu'au bout marque le guide comme lu (XP et test compris).
- Ordre de grandeur : environ 720 Ko par minute d'audio ; la lecture va environ 2 fois plus vite que le temps réel sur la carte graphique, la réécriture ajoute quelques secondes par page.

```bash
docker compose exec web python manage.py narrate --tout     # met en file tous les guides (après les demandes)
docker compose exec web python manage.py narrate --guide <slug>
```

En développement : `.\dev ia` (démarre aussi `tts` et `narrator`) puis `.\dev audio --tout`. Les générations se suivent dans `docker compose logs -f narrator` et dans l'administration (« Versions audio », action « Remettre en file »).

## Génération des QCM par IA locale (Ollama)

Les questionnaires des guides qui n'en ont pas peuvent être proposés par un modèle de langage exécuté sur la machine (aucune donnée ne sort) puis **relus par un gestionnaire de contenu avant publication**.

- **`ollama`** (profil Compose `ia`) : serveur Ollama avec accès au GPU NVIDIA (pilote + NVIDIA Container Toolkit requis), modèles conservés dans le volume `ollama_models`. Modèles par défaut : `gemma3:12b` pour les générations (variable `QUIZ_LLM_MODEL`) et `qwen3:14b` pour les avis du pré-relecteur (`PREREVIEW_LLM_MODEL`) ; avec 8 Go de VRAM, une partie tourne sur le processeur (PASSI : 36 min de génération).
- **`quizgen`** (profil `ia`) : file d'attente (`manage.py generate_quizzes --boucle`). Pour chaque guide, le texte du PDF est découpé en 6 parties (une par série, difficulté croissante), le modèle rédige 5 questions par série au format JSON imposé, les questions sont filtrées (doublons, « toutes les réponses », leurres trop révélateurs…) puis **vérifiées par le modèle lui-même** qui doit retrouver la bonne réponse. Enfin le [pré-relecteur IA](#pré-relecteur-ia) relit chaque série à côté du texte du guide, régénère les questions ratées et laisse des notes au relecteur.
- Le résultat est un **brouillon** : menu **Gestion → ✍️ Relecture** (gestionnaires de contenu et superutilisateurs). Chaque question peut être acceptée, corrigée ou rejetée ; les rejetées peuvent être régénérées. La publication exige 5 questions acceptées par série ; elle crée le QCM et l'exporte dans `catalogue/data/quiz/<id>.json` (réimporté par `import_quizzes`).
- Ordre de grandeur (RTX 3070) : environ 3 minutes par guide, 2 guides en parallèle (`OLLAMA_NUM_PARALLEL`).

```bash
docker compose --profile ia up -d ollama quizgen
docker compose exec ollama ollama pull gemma3:12b
docker compose exec ollama ollama pull qwen3:14b
docker compose exec web python manage.py generate_quizzes --tout      # met en file tous les guides sans QCM
docker compose exec web python manage.py generate_quizzes --guide <slug>
```

En développement : `.\dev ia` puis `.\dev qcm --tout`. Un guide sans QCM peut aussi être mis en file depuis sa fiche (« Générer le QCM »). Suivi : `docker compose logs -f quizgen`.

## Cours générés par IA locale

Rubrique **Cours** de la barre du haut. Deux sortes de cours, rédigés par l'IA locale (même modèle que les QCM) à partir des guides de l'ANSSI, en langage simple :

- **Cours par thème** (un par domaine du catalogue) : l'IA propose un plan de 4 à 6 leçons progressives, puis rédige chaque leçon à partir des passages des guides du thème les plus proches de son objectif.
- **Cours par guide** : la version simplifiée d'un guide, une leçon par partie (selon sa longueur).

Chaque leçon (environ 5 minutes : introduction, parties, exemple concret, points à retenir, sources) se termine par un **quiz de 4 questions corrigées aussitôt** ; il faut 75 % de bonnes réponses pour ouvrir la leçon suivante. Un **examen final** (10 questions, réussi à 70 %) valide le cours. Barème : leçon réussie 15 XP (+5 sans faute), examen réussi 40 XP (+10 sans faute) et 10 émeraudes, une seule fois.

- Service **`coursegen`** (profil `ia`, démarré par `.\dev ia`) : file de génération, avec pause, mode jeu et coupe-circuit comme les autres (carte « Cours par IA » du tableau de bord des générations). Les leçons déjà rédigées sont conservées si la génération est interrompue. Le [pré-relecteur IA](#pré-relecteur-ia) contrôle ensuite chaque leçon et son quiz avant la relecture.
- **Relecture** (menu **Gestion → ✍️ Relecture**, onglet Cours) : une leçon par page, avec l'aperçu, le texte et les questions modifiables ; la publication exige au moins 3 questions par leçon et 5 pour l'examen. Rien n'est visible des apprenants avant.

```bash
docker compose exec web python manage.py generate_courses --themes      # un cours par thème
docker compose exec web python manage.py generate_courses --guides      # un cours par guide (plusieurs heures)
docker compose exec web python manage.py generate_courses --theme <slug> # ou --guide <slug>
```

En développement : `.\dev cours --themes`. Ordre de grandeur : environ 1 à 2 minutes par leçon sur une RTX 3070 (plus si les QCM sont générés en même temps).

## Podcasts générés par IA locale

Rubrique **Podcasts** de la barre du haut. Une **série par thème** du catalogue, inspirée des guides de l'ANSSI sans en suivre un en particulier : 3 à 5 épisodes de 7 à 9 minutes où deux voix discutent du sujet. **Clara** anime et pose les questions d'un auditeur débutant, **Hugo** explique avec des exemples concrets ; chaque épisode se termine par un récapitulatif.

1. **Écriture** (service **`podcastgen`**, profil `ia`) : l'IA locale propose le plan de la série, puis écrit le dialogue de chaque épisode à partir des passages des guides du thème les plus proches de son sujet (même recherche que l'assistant de lecture et les cours).
2. **Relecture** (Relecture → onglet Podcasts) : un épisode par page, avec la transcription et le script modifiable (une réplique par paragraphe, « Clara : … » / « Hugo : … »). « Faire réécrire par l'IA » refait un seul épisode. **Valider et enregistrer** envoie la série à l'enregistrement.
3. **Enregistrement** (même service) : l'IA est déchargée de la carte graphique, puis Kyutai TTS lit le dialogue à deux voix (« développeuse » et « fabieng », enregistrements de Kyutai, CC0), par petits groupes de répliques pour garder un enchaînement naturel ; un MP3 par épisode. La série est publiée à la fin.

L'écoute reprend où l'on s'était arrêté (même lecteur que les versions audio, transcription par épisode). Barème : 10 XP par épisode écouté jusqu'au bout, 20 XP pour la série entière, une seule fois. Plan, scripts et épisodes déjà enregistrés sont conservés si la génération est interrompue (pause, mode jeu) ; les enregistrements validés passent avant les nouvelles écritures.

```bash
docker compose exec web python manage.py generate_podcasts --themes       # une série par thème
docker compose exec web python manage.py generate_podcasts --theme <slug> # un thème
```

En développement : `.\dev podcast --themes` (ou « Générer » sur la carte Podcasts du tableau de bord des générations).

## Atelier de création (ajouts par les rédacteurs et les gestionnaires)

Menu **Gestion → 🖋️ Atelier de création** (`/atelier/`, app `studio`) : une carte par type de contenu, avec « + Ajouter », la liste (brouillons, à relire, publiés) et, pour les gestionnaires, ce qui attend une relecture.

| Famille | Types | Éditeur |
|---|---|---|
| Catalogue ANSSI | Guides (fiche + PDF), Domaines (gestionnaires seulement), Référentiels (PDF, trame, grille d'exigences, documents cités), Programmes d'examen (domaines pondérés) | Éditeur commun (`studio/catalogue_views.py`) : formulaire en sections, panneau d'état, blocs (PDF, grille, documents cités, étapes) |
| Certifications et parcours | Certifications (référentiels et domaines liés), Parcours sur mesure (guides choisis dans un ordre choisi, un mot par étape) | Éditeur commun ; les parcours publiés apparaissent dans l'onglet Parcours du catalogue |
| Apprendre | Cours, Podcasts, QCM d'un guide (6 séries), Lexique d'un guide | Pages d'écriture et de relecture ouvertes au rédacteur pour ses brouillons |
| Audio | Version audio enregistrée (un MP3 par chapitre) | `studio/audio_views.py` ; à la publication, elle remplace la voix de synthèse du guide |

- **Cycle de vie** (`studio/editorial.py`) : en rédaction → soumis (à relire) → publié, ou renvoyé au rédacteur. Un ajout reste invisible des apprenants tant qu'il n'est pas publié ; un rédacteur ne voit et ne modifie que ses propres brouillons ; un gestionnaire voit tout, modifie aussi les contenus publiés et publie directement. Seul un brouillon jamais publié se supprime depuis l'atelier.
- **Grilles d'exigences en texte** (`studio/grid.py`) : `## Chapitre`, `- R12 : Exigence [pondération]`, sous-ligne indentée `  - …`, précision `  > …`. Import possible depuis la trame Excel ou le sommaire du PDF ; une ligne qui garde sa référence garde la progression des apprenants. Les documents cités se retrouvent aussi dans le PDF (« Retrouver les documents cités »).
- **Aide de l'IA** : résumé d'un guide ou d'un référentiel à partir de son PDF, simplifier / corriger un texte ; pour les cours et podcasts, brouillon, développement, mise en dialogue, vérification de la fidélité, questions ; pour un QCM, questions proposées série par série (vérifiées par une seconde lecture) ou complétion de toutes les séries en arrière-plan ; pour un lexique, relevé complet par l'IA en arrière-plan. Les premiers jets de l'IA (cours, podcasts, QCM, lexiques demandés par un rédacteur) reviennent dans l'atelier, en rédaction.
- **Contenus publiés** : un gestionnaire modifie aussi un cours, une série de podcasts ou un lexique déjà publiés (effet immédiat). Pour un podcast, un texte d'épisode modifié marque l'épisode « à réenregistrer » ; « 🎙️ Réenregistrer » relance l'enregistrement de ces épisodes seulement, la série restant écoutable (ancienne version) en attendant.
- **Domaines** : l'éditeur d'un domaine montre ses guides (ajouter un guide, le déplacer vers un autre domaine), les certifications qu'il prépare (rattacher, détacher) et ce qui en dépend (cours, podcasts, parcours).
- **Lexiques** : un guide n'a qu'un lexique ; le choisir à nouveau ouvre celui qui existe pour le compléter (gestionnaires, ou son rédacteur).
- **Liens de retour** : « ← Retour » ramène à la page précédente du site quand on en vient ; sinon le lien ouvre la liste sur l'onglet de l'état du contenu (brouillons, à relire, publiés).
- **Versions audio enregistrées** : MP3 contrôlé (durée lue dans les trames, 55 Mo au plus par chapitre, limite de nginx), écoute protégée pendant la relecture ; le lecteur et la validation de lecture sont ceux des versions de synthèse.

## Lexique des guides (sigles et termes)

Pour ne pas être perdu pendant la lecture : chaque guide peut avoir un lexique de ses sigles (SI, DMZ, PSSI…) et termes techniques, avec leur forme développée et une définition simple.

- Généré par l'IA locale (service **`glossarygen`**, profil `ia`) : le texte du guide est analysé par parties ; les sigles repérés automatiquement sont signalés au modèle, et une entrée n'est gardée que si le terme figure vraiment dans le guide (pas d'invention). Relu avant publication, comme les QCM et les cours.
- Affichage : bouton « 📖 Lexique » (avec recherche) sur la fiche du guide, pendant la lecture du PDF, l'écoute et les leçons ; dans les leçons, la première apparition de chaque terme est soulignée et un clic ouvre sa définition. Onglet « Lexique » du catalogue : tous les termes publiés, par lettre. Masqué pendant les tests.

```bash
docker compose exec web python manage.py generate_glossaries --tout          # tous les guides sans lexique
docker compose exec web python manage.py generate_glossaries --guide <slug>
```

En développement : `.\dev lexique --tout`. Ordre de grandeur : quelques minutes par guide.

## Relecture

Menu **Gestion → ✍️ Relecture** : une seule fenêtre, avec un onglet par type (« Tout » rassemble ce qui attend une relecture, les plus anciens d'abord), les générations en cours, les échecs (« Relancer ») et les publications récentes. Les pages de détail restent propres à chaque type (une question, une leçon ou un lexique à la fois).

### Pré-relecteur IA

Avant la relecture humaine, l'IA locale contrôle elle-même ce qu'elle vient de produire (`catalogue/prereview.py`) — **QCM et cours** pour l'instant. Elle ne publie rien et ne décide de rien : la relecture humaine reste obligatoire.

- **Quand** : à la fin de chaque génération, dans le même service (`quizgen`, `coursegen`) et donc dans le même tour de passage : pas de deuxième IA sur la carte graphique. Pause, mode jeu et coupe-circuit s'appliquent : la pré-relecture s'arrête entre deux séries (ou deux leçons) et reprend là où elle en était.
- **Ce qu'elle regarde** : chaque série de QCM est relue à côté de la partie du guide dont elle est tirée ; chaque leçon de cours à côté des extraits qui ont servi à l'écrire, l'examen à côté des leçons. Pour chaque question, un avis : *bonne*, *douteuse* (formulation floue, leurre défendable…) ou *ratée* (réponse non justifiée ou contredite par le texte, plusieurs bonnes réponses, erreur factuelle). Pour une leçon, elle relève aussi les affirmations que les extraits ne soutiennent pas. Les doublons (questions trop proches dans tout le QCM) sont repérés sans IA.
- **Ce qu'elle fait** : une question ratée est **régénérée d'office** puis relue à son tour (2 essais par question, 10 par QCM ou par cours) ; si elle reste ratée, elle est seulement signalée. Les questions déjà décidées ou corrigées par un humain, et les contenus écrits par un rédacteur, ne sont pas touchés.
- **Ce que voit le relecteur** : un encadré « 🔎 Pré-relecteur IA » avec le bilan (questions douteuses, régénérées) et, sur chaque question ou leçon, la note correspondante (« Question douteuse — réponse non justifiée par le texte : le guide recommande 12 caractères, pas 8 »). Les questions douteuses sont marquées « à vérifier » dans la carte des questions et la liste de relecture, et « Accepter les vérifiées » les laisse de côté.
- **Panne de l'IA** pendant la pré-relecture : le contenu passe quand même en relecture, avec une note qui le signale.
- **Désactiver** : bouton « Pré-relecteur IA » dans le bandeau « Ordre de passage » du tableau de bord des générations, ou `PREREVIEW_ENABLED=0` dans `.env` pour toute l'installation. Sans IA (`AI_ENABLED=0`), elle ne tourne jamais. Désactivée, tout se passe comme avant.
- Coût : environ un appel au modèle par série ou par leçon (plus les régénérations), soit quelques dizaines de secondes de plus par guide.

## Tableau de bord des générations

Menu **Gestion → 📊 Générations** (gestionnaires de contenu) : pour les QCM par IA et pour les versions audio, couverture des guides, file d'attente, générations en cours (série ou chapitre), échecs (bouton « Réessayer »), rythme moyen et temps restant estimé, graphique des 14 derniers jours, état du service (« Service arrêté » s'il ne donne plus signe de vie depuis 5 minutes). La page se met à jour toute seule.

- **Lancer / Arrêter les services** (superutilisateurs) : démarre ou arrête les conteneurs de génération (Ollama, quizgen, coursegen, podcastgen, glossarygen, tts, narrator). Le site n'a volontairement aucun accès à Docker (ce serait lui donner la main sur la machine) : il enregistre une demande, et l'**agent du poste** (`scripts/ia-agent.ps1`, démarré en arrière-plan par `.\dev up`, ou `.\dev agent`) la récupère toutes les 8 secondes et exécute une commande fixée d'avance ; journal dans `.ia-agent.log`. Sans agent, les boutons sont grisés.
- **Tout mettre en pause / Tout reprendre** (en haut de la page) : met en pause d'un coup les QCM, les cours, les lexiques et les audios, sans couper l'assistant.
- **Pause / Reprendre** : le drapeau est enregistré en base (il survit à un redémarrage). Une génération en cours s'arrête à la prochaine étape — entre deux séries de QCM, entre deux chapitres audio — et retourne dans la file ; à la reprise, les séries et chapitres déjà produits sont conservés (un chapitre audio n'est resynthétisé que si son texte a changé).
- **Mettre en file** tous les guides sans QCM ou sans version audio, en un clic.
- **Ordre de passage** (bandeau en haut, ← →) : **une seule génération à la fois**, la première de l'ordre qui a du travail (par défaut : lexiques, QCM, cours, podcasts, audio). Toutes partagent la carte graphique : en parallèle, l'IA et la voix se disputent la mémoire et tout ralentit. Un service ne prend un nouvel élément que si aucun autre ne travaille et qu'aucune génération placée avant lui n'attend (`generation.holder`) ; l'élément en cours n'est jamais coupé, une génération en pause ou arrêtée laisse passer les suivantes, et les versions audio demandées par un utilisateur passent devant tout. Les QCM gardent leurs 2 guides en parallèle. Une carte qui attend affiche « Attend son tour ». Les commandes lancées à la main (sans `--boucle`) ne suivent pas cet ordre.
- **Gérer la file** (lien sous « Prochains dans la file » de chaque carte, `catalogue/queues.py`) : la file complète dans l'ordre où le service la traitera, avec ⤒ ↑ ↓ ⤓ pour changer l'ordre, ✕ pour retirer un élément et « Tout retirer ». L'ordre choisi est celui que suivent vraiment les services (`QUEUE_ORDER` de chaque modèle) ; ce qui est mis en file ensuite passe après. Pour l'audio, les demandes des utilisateurs restent devant, sauf si l'on place un autre guide avant elles. Retirer ne perd rien de relu ou publié : brouillon abandonné (le guide redevient « à générer »), série validée renvoyée en relecture, régénération audio annulée (l'ancienne version redevient écoutable si elle est complète).
- En ligne de commande, `narrate` et `generate_quizzes` respectent aussi la pause.
- **Mode jeu** (bandeau du tableau de bord, menu **Gestion**, ou `.\dev jeu` / `.\dev fin-jeu`) : met en pause les deux générations (effet en quelques secondes), coupe l'assistant pour tout le monde et décharge le modèle d'IA de la carte graphique. À la sortie, tout reprend comme avant ; les pauses posées séparément restent en place.

## Assistant de lecture

Le bouton « 💬 Une question ? », présent sur toutes les pages une fois connecté, ouvre un assistant. Sur les pages d'un guide (fiche, lecture, écoute, test), l'apprenant demande une explication sur ce qu'il n'a pas compris (il peut citer le passage concerné, ou le sélectionner dans la transcription d'un chapitre) et l'assistant s'appuie sur le texte du guide ; ailleurs, il répond en tuteur de cybersécurité généraliste. La conversation est conservée pendant la session de l'onglet (dans le navigateur uniquement).

- Il utilise le même service `ollama` et le même modèle que les QCM : il faut que le profil `ia` tourne (`.\dev ia`), sinon l'assistant indique qu'il est indisponible.
- Le texte du guide est extrait du PDF à la première question puis conservé (« Chapitres des guides (texte) ») ; les passages les plus proches de la question, et le chapitre en cours d'écoute, sont fournis au modèle avec la consigne de s'appuyer sur le guide.
- La réponse s'affiche au fil de l'eau (gunicorn en fils d'exécution, nginx sans mise en tampon sur `/guides/<slug>/question/`). Limite : 30 questions par heure et par utilisateur ; seule la date de la question est enregistrée côté serveur, pas son contenu.
