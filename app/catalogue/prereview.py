"""
Pré-relecteur IA : une passe de contrôle par l'IA locale à la fin de chaque génération (QCM, cours),
avant la relecture humaine. Il ne publie jamais rien et ne décide de rien : il laisse des notes au
relecteur et régénère d'office les questions clairement ratées.

  - Le modèle relit les questions d'une série de QCM (ou d'une leçon de cours) à côté du texte du
  guide
    dont elles sont tirées et rend un avis par question : bonne, douteuse (un humain doit regarder
    de
    près) ou ratée (réponse non justifiée ou contredite par le texte, plusieurs bonnes réponses,
    erreur
    factuelle). Pour une leçon, il relève aussi les affirmations que le texte ne soutient pas.
  - Les doublons sont repérés sans IA : questions trop proches dans tout le brouillon.
  - Une question ratée est régénérée (MAX_REGENERATIONS par question, REGENERATION_BUDGET par
  contenu),
    puis relue à son tour ; si elle reste ratée, elle est seulement signalée.
  - Il tourne au sein de la génération (même service, même tour de passage : une seule IA sur la
  carte
    graphique) ; `checkpoint` permet la pause entre deux séries ou leçons, la reprise repart de ce
    qui
    n'a pas encore été relu.
  - Désactivable (réglage PREREVIEW_ENABLED=0, ou bouton du tableau de bord des générations) ; ne
    tourne jamais sans IA (AI_ENABLED=0). Les contenus écrits par un rédacteur ne sont pas
    pré-relus.
  - Une panne de l'IA pendant la pré-relecture n'empêche pas la relecture humaine : une note le
  signale.
"""

import difflib

from django.conf import settings
from django.utils import timezone

from . import generation, quizgen
from .models import GenerationControl, QuizDraftQuestion

Kind = GenerationControl.Kind
Decision = QuizDraftQuestion.Decision
Verdict = QuizDraftQuestion.Prereview
MAX_REGENERATIONS = 2       # par question
REGENERATION_BUDGET = 10    # par QCM ou par cours : borne le temps passé sur la carte graphique
DUPLICATE_RATIO = 0.75      # au-delà, deux questions sont signalées comme doublons
SOURCE_CHARS = 9000  # texte du guide fourni pour relire une leçon (avec la leçon elle-même)

SYSTEM_PROMPT = """Tu es relecteur pédagogique en cybersécurité. Tu contrôles des questions à choix multiples \
rédigées par une autre IA à partir d'un extrait d'un guide de l'ANSSI, avant qu'un humain ne les relise.
Pour chaque question, compare-la au texte et rends un avis :
- « bonne » : la bonne réponse indiquée est justifiée par le texte, les autres propositions sont fausses \
d'après le texte, la question est claire ;
- « douteuse » : formulation floue, mauvaise réponse peut-être défendable, justification seulement \
implicite dans le texte : un humain doit regarder ;
- « ratee » : la bonne réponse indiquée est contredite par le texte ou absente du texte, plusieurs \
propositions sont correctes, ou la question contient une erreur factuelle.
Pour une question douteuse ou ratée, écris une note courte en français pour le relecteur humain, \
qui dit ce que le guide dit vraiment (par exemple « le guide recommande 12 caractères, pas 8 »).
Sois exigeant mais juste : ne signale pas une question correcte."""

PROBLEMS = {
    "aucun": "",
    "reponse_non_justifiee": "réponse non justifiée par le texte",
    "plusieurs_bonnes_reponses": "plusieurs bonnes réponses possibles",
    "formulation_floue": "formulation floue",
    "erreur_factuelle": "erreur factuelle",
    "doublon": "doublon d'une autre question",
}
VERDICT_ITEM = {
    "type": "object",
    "properties": {
        "numero": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["bonne", "douteuse", "ratee"]},
        "probleme": {"type": "string", "enum": list(PROBLEMS)},
        "note": {"type": "string"},
    },
    "required": ["numero", "verdict", "probleme", "note"],
}
VERDICTS_SCHEMA = {
    "type": "object",
    "properties": {"avis": {"type": "array", "items": VERDICT_ITEM}},
    "required": ["avis"],
}
LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "problemes_lecon": {"type": "array", "items": {"type": "string"}},
        "avis": {"type": "array", "items": VERDICT_ITEM},
    },
    "required": ["problemes_lecon", "avis"],
}


# --- Réglage ------------------------------------------------------------------------------------
def enabled():
    """Pré-relecture active : IA présente, pas coupée par le réglage ni depuis le tableau de
    bord."""
    return bool(settings.AI_ENABLED and settings.PREREVIEW_ENABLED
                and not GenerationControl.objects.filter(kind=Kind.PREREVIEW, paused=True).exists())


def set_enabled(on, user=None):
    """Active ou coupe la pré-relecture (tableau de bord des générations)."""
    return generation.set_paused(Kind.PREREVIEW, not on, user)


# --- Avis du modèle -----------------------------------------------------------------------------
def _listing(questions):
    """Questions numérotées pour la consigne ; `questions` : dictionnaires text / choices /
    explanation."""
    lines = []
    for number, q in enumerate(questions, start=1):
        others = " | ".join(q["choices"][1:])
        lines.append(
            f"{number}. {q['text']}\n   Bonne réponse indiquée : {q['choices'][0]}\n   Autres"
            f" propositions : {others}\n   Explication : {q.get('explanation') or '(aucune)'}"
        )
    return "\n".join(lines)


def _verdicts(items, count):
    """{indice : (verdict, note)} à partir de la réponse du modèle (avis manquants ou incohérents
    ignorés)."""
    found = {}
    for item in items or []:
        index = item.get("numero")
        if not isinstance(index, int) or not 1 <= index <= count or index - 1 in found:
            continue
        verdict = (
            item.get("verdict") if item.get("verdict") in ("bonne", "douteuse", "ratee") else None
        )
        if verdict is None:
            continue
        problem = PROBLEMS.get(item.get("probleme"), "")
        note = (item.get("note") or "").strip()
        text = " : ".join(p for p in (problem, note) if p) if verdict != "bonne" else ""
        found[index - 1] = (verdict, text[:600])
    return found


def ask_verdicts(source, questions):
    """Avis du modèle sur des questions tirées de `source` (lève quizgen.GenerationError si l'IA ne
    répond pas)."""
    prompt = (
        f"Texte extrait du guide :\n\"\"\"\n{source}\n\"\"\"\n\nQuestions à contrôler"
        f" :\n{_listing(questions)}\n\nDonne un avis pour chacune des {len(questions)} questions,"
        " dans l'ordre."
    )
    result = quizgen.chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        VERDICTS_SCHEMA,
        temperature=0,
        model=settings.PREREVIEW_LLM_MODEL,
    )
    return _verdicts(result.get("avis"), len(questions))


def duplicate_of(text, others):
    """Indice de la première question trop proche de `text` dans `others`, ou None."""
    for index, other in enumerate(others):
        if (
            difflib.SequenceMatcher(None, quizgen._norm(text), quizgen._norm(other)).ratio()
            > DUPLICATE_RATIO
        ):
            return index
    return None


# --- QCM ----------------------------------------------------------------------------------------
def _as_dict(question):
    return {"text": question.text, "choices": question.choices, "explanation": question.explanation}


def review_quiz(draft, parts, checkpoint=None):
    """
    Pré-relit les questions du brouillon pas encore relues (ni décidées ni corrigées par un humain)
    et
    régénère les ratées. `parts` : texte du guide découpé comme pour la génération (une partie par
    série).
    L'état du brouillon n'est pas changé ; seuls les avis, les notes et les questions régénérées le
    sont.
    """
    if not enabled() or draft.author_id:
        return draft
    budget = REGENERATION_BUDGET - sum(draft.questions.values_list("prereview_attempts", flat=True))
    for number in quizgen.SERIES:
        todo = list(
            draft.questions.filter(
                series=number, prereview=Verdict.NONE, decision=Decision.PENDING, edited=False
            )
        )
        if not todo:
            continue
        if checkpoint:
            checkpoint()
        draft.progress = f"Pré-relecture : série {number} sur 6"
        draft.save(update_fields=["progress"])
        try:
            budget = _review_series(draft, parts[number - 1], number, todo, budget)
        except quizgen.GenerationError as exc:
            draft.prereview_notes = draft.prereview_notes + [
                f"Série {number} : pré-relecture interrompue ({exc}). Questions non pré-relues : à"
                " relire comme d'habitude."
            ]
            draft.save(update_fields=["prereview_notes"])
            break
    draft.prereviewed_at = timezone.now()
    draft.save(update_fields=["prereviewed_at"])
    return draft


def _review_series(draft, part, number, todo, budget):
    """Relit une série (puis les questions régénérées, jusqu'à épuisement des essais) ; renvoie le
    budget restant."""
    while todo:
        others = list(
            draft.questions.exclude(decision=Decision.REJECTED)
            .exclude(pk__in=[q.pk for q in todo])
            .values_list("text", flat=True)
        )
        verdicts = ask_verdicts(part.text, [_as_dict(q) for q in todo])
        again = []
        for index, question in enumerate(todo):
            verdict, note = verdicts.get(index, (None, ""))
            twin = duplicate_of(question.text, others + [q.text for q in todo[:index]])
            if twin is not None and verdict != "ratee":
                verdict, note = "douteuse", "; ".join(
                    n for n in (note, "très proche d'une autre question du QCM") if n
                )
            history = question.prereview_note if question.prereview_attempts else ""
            if (
                verdict == "ratee"
                and budget > 0
                and question.prereview_attempts < MAX_REGENERATIONS
            ):
                if _regenerate(
                    draft,
                    part,
                    number,
                    question,
                    note,
                    others + [q.text for q in todo if q != question],
                ):
                    budget -= 1
                    again.append(question)
                    continue
                note += " (régénération sans résultat)"
            if verdict is None:
                continue  # pas d'avis du modèle : la question reste « non pré-relue »
            if verdict == "bonne":
                question.prereview = (
                    Verdict.REGENERATED if question.prereview_attempts else Verdict.OK
                )
                question.prereview_note = history
            else:
                label = (
                    "Question ratée selon le pré-relecteur"
                    if verdict == "ratee"
                    else "Question douteuse"
                )
                still = " (après régénération)" if question.prereview_attempts else ""
                question.prereview = Verdict.DOUBT
                question.prereview_note = "\n".join(
                    n for n in (history, f"{label}{still} : {note}") if n
                )
            question.save(update_fields=["prereview", "prereview_note"])
        todo = again
    return budget


def _regenerate(draft, part, number, question, reason, others):
    """Remplace une question ratée par une nouvelle de la même série ; False si le modèle n'en donne
    pas de valide."""
    found = quizgen.generate_series(part, number, 1, others, system=quizgen.system_prompt(draft))
    if not found:
        return False
    item = found[0]
    check = quizgen.cross_check(part, [item])[0]
    old = question.text if len(question.text) <= 160 else question.text[:157] + "…"
    question.prereview_note = "\n".join(
        n
        for n in (
            question.prereview_note,
            (
                f"Régénérée par le pré-relecteur : l'ancienne question (« {old} ») était ratée —"
                f" {reason or 'sans motif'}."
            ),
        )
        if n
    )
    question.text, question.choices = item["text"], item["choices"]
    question.explanation, question.source, question.diagram = (
        item["explanation"],
        item["source"],
        item["diagram"],
    )
    question.cross_check, question.prereview = check, Verdict.NONE
    question.prereview_attempts += 1
    question.save()
    return True


# --- Cours --------------------------------------------------------------------------------------
LESSON_PROMPT = """En plus des questions, relis la leçon elle-même : liste dans « problemes_lecon » les \
affirmations de la leçon que les extraits contredisent ou ne justifient pas (une phrase courte chacune, \
avec ce que dit le guide). Laisse la liste vide si la leçon est fidèle aux extraits."""


def course_notes(course, step=None):
    """Notes du pré-relecteur pour un cours (toutes, ou celles d'une étape : « 2 », « examen »)."""
    notes = (course.prereview or {}).get("notes", [])
    return [n["text"] for n in notes if step is None or n.get("step") == step]


def review_course(course, theme_sections=None, checkpoint=None):
    """
    Pré-relit chaque leçon (texte et quiz, comparés aux extraits des guides qui ont servi à
    l'écrire) puis
    l'examen final (comparé aux leçons) ; régénère les questions ratées. L'état du cours n'est pas
    changé.
    """
    from courses import coursegen  # import local : courses importe catalogue
    if not enabled() or course.author_id:
        return course
    state = {"done": [], "notes": [], "regenerated": 0, **(course.prereview or {})}
    steps = [(str(lesson.position), lesson) for lesson in course.lessons.all()] + [("examen", None)]
    for step, lesson in steps:
        if step in state["done"]:
            continue
        if checkpoint:
            checkpoint()
        course.progress = "Pré-relecture : " + (
            f"leçon {lesson.position}" if lesson else "examen final"
        )
        course.save(update_fields=["progress"])
        try:
            if lesson:
                item = (
                    course.plan[lesson.position - 1]
                    if lesson.position <= len(course.plan)
                    else None
                )
                context = (
                    coursegen.lesson_context(course, item, theme_sections)[0]
                    if item
                    else lesson.body
                )
                _review_lesson(
                    course, state, step, lesson, quizgen._fit(context, SOURCE_CHARS), coursegen
                )
            else:
                context = "\n\n".join(
                    f"## Leçon {l.position} : {l.title}\n{l.body}" for l in course.lessons.all()
                )
                _review_lesson(
                    course,
                    state,
                    step,
                    None,
                    quizgen._fit(context, quizgen.MAX_CONTEXT_CHARS),
                    coursegen,
                )
        except (quizgen.GenerationError, coursegen.GenerationError) as exc:
            state["notes"].append(
                {
                    "step": "",
                    "text": (
                        f"Pré-relecture interrompue ({exc}) : le reste du cours "
                        "n'a pas été pré-relu."
                    ),
                }
            )
            break
        state["done"].append(step)
        course.prereview = state
        course.save(update_fields=["prereview"])
    course.prereview, course.prereviewed_at = state, timezone.now()
    course.save(update_fields=["prereview", "prereviewed_at"])
    return course


def _review_lesson(course, state, step, lesson, source, coursegen):
    """Une leçon (lesson=None : l'examen) : avis du modèle, notes, régénération des questions
    ratées."""
    questions = list(course.questions.filter(lesson=lesson).order_by("position"))
    where = f"Leçon {lesson.position}" if lesson else "Examen"
    if lesson:
        prompt = (
            "Extraits des guides de l'ANSSI qui ont servi à écrire la leçon"
            f" :\n\"\"\"\n{source}\n\"\"\"\n\nLeçon « {lesson.title} » :\n\"\"\"\n{lesson.body}\nÀ"
            f" retenir : {' ; '.join(lesson.key_points)}\n\"\"\"\n\nQuestions du quiz de la leçon"
            f" :\n{_listing([_as_dict(q) for q in questions])}\n\n{LESSON_PROMPT}"
        )
        result = quizgen.chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            LESSON_SCHEMA,
            temperature=0,
            model=settings.PREREVIEW_LLM_MODEL,
        )
        for problem in (result.get("problemes_lecon") or [])[:5]:
            if isinstance(problem, str) and problem.strip():
                state["notes"].append({"step": step, "text": f"{where} : {problem.strip()[:400]}"})
        verdicts = _verdicts(result.get("avis"), len(questions))
    else:
        verdicts = ask_verdicts(source, [_as_dict(q) for q in questions]) if questions else {}
    texts = [q.text for q in course.questions.all()]
    for index, question in enumerate(questions):
        verdict, note = verdicts.get(index, (None, ""))
        label = f"{where}, question {question.position}"
        if verdict == "ratee" and state["regenerated"] < REGENERATION_BUDGET:
            others = [t for t in texts if t != question.text]
            found = coursegen.more_questions(lesson.body if lesson else source, 1, others,
                                             "de la leçon" if lesson else "du cours")
            if found:
                old = question.text if len(question.text) <= 160 else question.text[:157] + "…"
                question.text, question.choices = found[0]["text"], found[0]["choices"]
                question.explanation, question.answer = found[0]["explanation"], 0
                question.save(update_fields=["text", "choices", "explanation", "answer"])
                state["regenerated"] += 1
                state["notes"].append(
                    {
                        "step": step,
                        "text": (
                            f"{label} régénérée : l'ancienne (« {old} ») était "
                            f"ratée — {note or 'sans motif'}. Vérifiez la nouvelle."
                        ),
                    }
                )
                continue
            note += " (régénération sans résultat)"
        if verdict in ("douteuse", "ratee"):
            state["notes"].append({"step": step, "text": f"{label} douteuse : {note}"})
