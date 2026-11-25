# Qualité du code : pylint

Ce document justifie la configuration pylint du projet (`.pylintrc`) et les remarques laissées volontairement en
l'état, sous forme d'analyse de risque. Il est destiné aux développeurs et à toute personne qui relit la qualité du
code.

```
pylint app tts        # depuis la racine du dépôt ; nécessite pylint et pylint-django
```

## Résultat

| | Avant | Après |
|---|---|---|
| Configuration | aucune (réglages par défaut de pylint) | `.pylintrc` à la racine |
| Note | 7,83 / 10 | 9,60 / 10 (9,75 sans les extensions facultatives) |
| Erreurs | 37 | 24, toutes de fausses alertes (voir plus bas) |
| Avertissements | 115 | 126 (voir l'explication sous ce tableau) |
| Lignes de plus de 100 colonnes | 2 326 | 94 |

Le nombre d'avertissements augmente parce que pylint-django charge maintenant le projet et analyse donc plus de
code. Aucun de ces avertissements ne signale un défaut à corriger (voir l'analyse de risque).

Les notes sont mesurées avec les mêmes extensions facultatives que le rapport d'origine : `code_style`,
`set_membership`, `bad_builtin`, `broad_try_clause`, `redefined_variable_type`, `dict_init_mutate`,
`overlapping_exceptions`.

## Écarts avec la configuration par défaut

Le projet n'avait pas de fichier pylint. Le point de comparaison est donc la configuration par défaut de pylint.
Chaque écart est justifié ci-dessous.

### Réglages qui rendent l'analyse juste (aucune règle assouplie)

| Réglage | Pourquoi | Effet |
|---|---|---|
| `load-plugins=pylint_django` et `django-settings-module=config.settings` | Sans eux, pylint ne connaît pas les modèles Django (`objects`, champs, clés étrangères) et signale de fausses erreurs `no-member`. Il affichait aussi `django-not-configured` sur chaque module. | Supprime 8 erreurs `django-not-configured` et la plupart des `no-member` des modèles. |
| `init-hook` : `sys.path` et deux variables factices | Les réglages exigent `DJANGO_SECRET_KEY` et `POSTGRES_PASSWORD`. Les valeurs « lint » servent seulement à charger les réglages : aucune base n'est contactée et ce ne sont pas de vrais secrets. | Aucun effet sur l'application. |
| `ignore=migrations` | Les migrations sont générées par Django et ne se modifient pas à la main. | Retire environ 60 fichiers du périmètre. |
| `ignored-modules=numpy,torch,huggingface_hub,moshi` | Ces bibliothèques n'existent que dans le conteneur `tts`, pas dans l'environnement du poste. | Supprime 5 erreurs `import-error` sans objet. |
| `jobs=0` | Utilise tous les cœurs. | Seulement la vitesse. |

### Règles désactivées (choix de style assumés)

| Règle | Remarques évitées | Justification | Risque accepté |
|---|---|---|---|
| `missing-module-docstring`, `missing-class-docstring`, `missing-function-docstring` | ~1 150 | Les fonctions sont courtes et leurs noms explicites. Les fonctions non évidentes ont déjà une docstring (en français) et la logique délicate a un commentaire. Imposer une docstring partout produirait des textes qui répètent le nom de la fonction. | **Faible.** Une fonction complexe pourrait rester sans explication : c'est à vérifier en revue de code. |
| `import-outside-toplevel` | 47 | Imports tardifs volontaires : ils cassent les cycles entre applications (catalogue ↔ cours ↔ podcasts), retardent le chargement de dépendances lourdes, et `AppConfig.ready()` impose ce schéma pour les signaux. | **Faible.** Une erreur d'import apparaît à l'exécution plutôt qu'au démarrage. Les tests exercent ces chemins. |
| `too-many-ancestors` | 27 | Les vues génériques, formulaires et classes d'admin Django héritent de nombreuses classes du framework. Le projet n'y peut rien. | **Nul.** |

### Réglage maintenu : 100 colonnes

`max-line-length=100` (la valeur par défaut). Les lignes trop longues ont été coupées plutôt que d'élargir la limite
(voir plus bas). Les 94 lignes encore trop longues sont dans des chaînes de texte : pylint n'en signale que 16.

### Réglages repris d'un `pylintrc` de référence

La plupart de ses réglages sont repris tels quels (`fail-under=8.0`, seuils de `[DESIGN]`, `[VARIABLES]`,
`[SIMILARITIES]`, `[TYPECHECK]`…). Seul `max-args=4` (au lieu de 5) ajoute des remarques : 10
`too-many-arguments`, note 9,75 → 9,74. Écartés, car mesurés trop bruyants ou faux :

| Réglage de référence | Effet mesuré | Décision |
|---|---|---|
| `max-line-length=80` | +3 994 `line-too-long` | 100 colonnes conservées |
| `bad-names=…,i,j,k` avec `good-names=Run,_` | ~60 `disallowed-name` (compréhensions, `enumerate`) | `i`, `j`, `k` permis |
| `logging-format-style=new` | 6 fausses erreurs : `logging` attend `%s`, pas `{}` | non repris |
| `check-quote-consistency=yes` | +133 remarques de guillemets | non repris |
| `ignore-imports=no` | +17 `duplicate-code` sur les blocs d'imports | non repris |
| `bad-names-rgxs=[A-z]{1-2}` | regex invalide (`{1-2}` n'est pas un quantificateur) | non repris |
| `output-format=colorized`, `reports=yes` | codes couleur et long rapport illisibles en CI | non repris |

## Ce qui a été corrigé

- `catalogue/narration.py` : la docstring de `request()` était placée après le `if`, donc Python l'ignorait.
- Imports inutilisés (`Language` dans `quizgen.py`, `smtplib` dans `invitations.py`) et variables inutilisées
  (renommées `_x`).
- `except` redondants : `OSError` couvre déjà `FileNotFoundError` et `smtplib.SMTPException` (`packs.py`,
  `invitations.py`).
- `news.py` : la valeur par défaut d'un argument était une liste, qu'on pourrait modifier par erreur. C'est
  maintenant un tuple.
- `tests_calendar.py` : la chaîne contenant `\,` et `\;` est passée en `r"..."`.
- `generate_quizzes.py` : retrait d'un préfixe `f` sur une chaîne sans variable.
- Les deux `apps.py` : imports à effet de bord conservés et signalés comme tels à pylint.
- Lignes de plus de 100 colonnes coupées : 2 326 → 94.
  - Méthode : `black --line-ranges` sur les seules lignes trop longues, chaînes découpées par concaténation
    implicite, commentaires et docstrings recoupés.
  - Vérification : l'arbre syntaxique de chaque fichier est identique avant et après, sauf `tests_paths.py`, où
    un `lambda` est devenu une fonction `def`.

## Analyse de risque de ce qui n'est pas corrigé

Échelle : **nul** (fausse alerte ou contrainte du framework), **faible** (style ou maintenabilité, sans effet sur
le fonctionnement), **modéré** (peut retarder le diagnostic d'un incident). Aucune remarque restante n'est un
défaut fonctionnel ou de sécurité connu.

### Erreurs restantes (24) : fausses alertes, risque nul

| Remarque | Où | Pourquoi c'est faux |
|---|---|---|
| `no-member` sur `setnchannels`, `writeframes`… | `tests_narration.py`, `tts/server.py` | pylint confond `wave.open(…, "wb")` (écriture) avec la version en lecture. |
| `no-member` sur `assert_called_once`, `assert_called` | `tests_narration.py`, `podcasts/tests.py` | Les fonctions sont remplacées par des `mock` pendant le test, ce que pylint ne voit pas. |
| `no-member` sur `key`, `css`, `name` | `progression/views.py`, `tests_items.py` | Objets construits dynamiquement que pylint type comme `object`. |
| `no-member` sur `guide`, `referential` | `catalogue/models.py:464` | Champs déclarés dans la classe concrète, pas dans le mixin analysé. |
| `no-member` / `not-callable` | `catalogue/packs.py` | `MigrationRecorder.Migration` est un modèle créé à l'exécution ; `spec.roots` est bien une fonction. |
| `no-member` sur `client` | `tests_revision.py` | Mixin de test, utilisé seulement avec `TestCase`. |

### Avertissements (126)

| Remarque | Nombre | Risque | Analyse et mesure |
|---|---|---|---|
| `broad-exception-caught` (`except Exception`) | 18 | **Modéré** | Volontaire dans trois cas : <br>• boucles des services de génération (`generate_*`, `narrate`) : une erreur sur un document ne doit pas arrêter le service. L'erreur est consignée sur le document et dans la sortie d'erreur. <br>• lecture de PDF externes (`importers/pdf.py`, `textgrid.py`) : pypdf lève des exceptions variées sur des fichiers malformés. <br>• appels best-effort à l'IA (`generation.unload_model`, `narration.voice_name`/`release_voice`). <br>Point d'attention : `assistant.py:246` et `importers/pdf.py` ne journalisent pas l'erreur. Un incident y serait plus long à diagnostiquer. Amélioration possible : ajouter `logger.exception(...)`. |
| `too-many-try-statements` | 30 | Faible | Extension facultative très stricte (plus d'une instruction dans un `try`). Les blocs longs sont dans les services de génération. |
| `unused-argument` | 27 | Nul | Signatures imposées : récepteurs de signaux Django (`sender`), checks (`app_configs`), middleware (`process_view`), et fausses fonctions des tests qui reprennent la signature réelle. `podcastgen.rerecord(user=…)` et `studio._object(key, …)` gardent la même signature que leurs équivalents. |
| `protected-access` (`_fonction` d'un autre module) | 24 | Faible | Les tests appellent des fonctions internes, ce qui est normal. Côté code : `courses/stats.py` et `podcasts/stats.py` réutilisent les helpers privés de `generation.py`, et `progression` réutilise `_xp_summary`. Il s'agit de couplage interne : renommer ces helpers casserait les modules appelants, mais les tests le détecteraient. Amélioration possible : les rendre publics. |
| `bad-builtin` (`map`, `filter`) | 8 | Nul | Préférence de style de l'extension facultative. |
| `redefined-outer-name` | 7 | Faible | Variable locale qui porte le même nom qu'une fonction ou un module (`items`, `lesson`, `queue`). Ça ne pose problème que si la fonction voulait utiliser la version globale, ce qui n'est pas le cas aujourd'hui. |
| `arguments-differ` | 4 | Nul | `BaseCommand.handle(*args, **options)` est redéfinie avec des arguments nommés, comme l'admet Django. `tts/server.py` est une fausse alerte. |
| `attribute-defined-outside-init` | 4 | Nul | Commandes Django et formulaires : les attributs sont posés dans `handle()` ou `clean_*()`, le point d'entrée réel. |
| `abstract-method` | 3 | Nul | `SansAccent` (lookup Django) n'a pas besoin des opérateurs `&`, `\|`, `^` inversés. |
| `unnecessary-lambda` | 1 | Nul | Le `lambda` dans `packs.py` retarde l'accès au modèle jusqu'à l'exécution. Il est nécessaire. |

### Refactorisations suggérées (318) et conventions (38)

| Remarque | Nombre | Risque | Analyse |
|---|---|---|---|
| `consider-using-assignment-expr` (`:=`) | 137 | Nul | Suggestion de style de l'extension facultative. La transformation n'apporte rien au fonctionnement. |
| `use-set-for-membership` | 30 | Nul | `x in (a, b)` sur des tuples de deux ou trois éléments : la différence de vitesse est négligeable. |
| `duplicate-code` | 27 | Faible | Champs communs aux modèles de génération (cours, podcasts, QCM : `status`, `error`, `requested_by`…) et commandes `generate_*` parallèles. Factoriser les modèles demanderait des migrations pour un gain faible. Le risque est d'oublier de corriger l'un des doublons. |
| `too-many-locals`, `too-many-arguments`, `too-many-branches`, `too-many-statements`, `too-many-return-statements` | ~80 | Faible | Fonctions longues, surtout les vues et les générateurs IA. Le risque porte sur la maintenabilité. À découper quand on modifie la fonction concernée. |
| `cyclic-import` | 11 | Faible | Cycles entre modules de génération (`generation` ↔ `glossary` ↔ `quizgen` ↔ `prereview` ↔ `coursegen`), cassés à l'exécution par les imports tardifs. Le risque est une erreur d'import si un import tardif est remonté en tête de fichier. Les tests le détecteraient au démarrage. |
| `inconsistent-return-statements` | 3 | Nul | Dans `narration._synthesize_piece`, la dernière tentative lève toujours une exception : la fin implicite de la fonction est inatteignable. Dans `tts/server.py`, les retours servent à sortir tôt des gestionnaires HTTP. |
| `invalid-name` | 13 | Nul | Réglages Django en majuscules, `setUp`/`tearDown` de unittest, `do_GET`/`do_POST` de `http.server` : noms imposés par les bibliothèques. |
| `line-too-long` | 16 | Nul | Consignes envoyées aux modèles d'IA (`coursegen.py`, `podcastgen.py`, `assistant.py`…), une URL et une expression régulière. Les couper changerait le texte reçu par le modèle ou la valeur testée. |
| Autres (`consider-using-with` dans un test, `too-many-lines`, `ungrouped-imports`…) | ~15 | Nul | Style. |

## Pistes, par ordre d'intérêt

1. Journaliser les `except Exception` silencieux (`assistant.py`, `importers/pdf.py`). Gain : diagnostic des
   incidents.
2. Rendre publics les helpers de `generation.py` utilisés par `courses/stats.py` et `podcasts/stats.py`, ainsi que
   `_xp_summary`.
3. Ajouter `pylint` (ou au moins `pylint --errors-only`) à la CI, avec cette configuration, pour éviter les
   régressions.
