"""
Génération des QCM par une IA locale (Ollama), puis relecture humaine.

Pour un guide :
  1. le texte est extrait par chapitres (même module que la version audio) puis
     découpé en 6 parties dans l'ordre du guide ;
  2. chaque partie donne une série de 5 questions, de la série 1 (notions essentielles)
     à la série 6 (mises en situation) : les petits modèles travaillent mieux sur un
     texte court et une consigne précise ;
  3. chaque question est contrôlée (propositions distinctes, pas de « toutes les
     réponses », pas de doublon…) ; les séries incomplètes sont relancées ;
  4. vérification croisée : le modèle répond lui-même aux questions, propositions
     mélangées ; une erreur signale une question à relire attentivement ;
  5. le brouillon passe « à relire » : un gestionnaire de contenu accepte, corrige ou
     rejette chaque question, peut régénérer les rejetées, puis publie.

L'IA ne lit que le texte du guide fourni dans la consigne ; elle ne contacte rien d'autre.
"""
import difflib
import json
import random
import re
import urllib.request
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import diagrams, generation, narration_text, prereview
from .management.commands import import_quizzes
from .models import Guide, QuizDraft, QuizDraftQuestion, Referential, subject_filter

Status = QuizDraft.Status
Decision = QuizDraftQuestion.Decision
SERIES = range(1, 7)
PER_SERIES = 5              # minimum publiable par série
MAX_PER_SERIES = (
    7  # long document (référentiel, gros guide) ; à 9, le modèle se répète (essai sur PASSI)
)
QUESTION_CHARS = 2000
BATCH = 5  # questions demandées par appel : une réponse plus longue risque le délai dépassé
MAX_ATTEMPTS = 4
DIAGRAM_FROM = 3  # à partir de la série 3 (mises en situation), au moins une question sur un schéma
MAX_CONTEXT_CHARS = (
    14000  # ≈ 4 000 jetons : tient dans le contexte d'un modèle 8B sur un GPU de 8 Go
)
TIMEOUT = 600

LEVELS = {
    1: (
        "très facile : une notion ou un terme clé du texte, posé à partir d'une situation simple de"
        " tous les jours"
    ),
    2: (
        "facile : le rôle d'une mesure du texte ou le risque qu'elle évite, illustré par un cas"
        " concret"
    ),
    3: (
        "intermédiaire : quelle recommandation précise du texte appliquer dans une situation"
        " donnée, et pourquoi"
    ),
    4: (
        "intermédiaire à difficile : paramètres, valeurs, conditions et exceptions du texte,"
        " appliqués à un cas précis, ou ce qui arrive si on les néglige"
    ),
    5: (
        "difficile : choisir, dans un cas concret, la mesure recommandée par le texte parmi des"
        " options proches"
    ),
    6: (
        "très difficile : mises en situation détaillées (incident, architecture, décision) où il"
        " faut appliquer les recommandations du texte"
    ),
}

SYSTEM_PROMPT = """Tu es concepteur pédagogique en cybersécurité. Tu rédiges en français des questions à choix \
multiples pour vérifier qu'un apprenant sait appliquer un guide de l'ANSSI.
Règles impératives :
- Chaque question porte uniquement sur le texte fourni : n'utilise aucune connaissance extérieure. La bonne \
réponse doit pouvoir être vérifiée dans le texte (une recommandation, une définition, une valeur).
- Ancre la question dans une situation concrète, en une ou deux phrases : qui (une PME, une mairie, un \
administrateur, un employé en déplacement…), ce qui se passe, puis la question. Exemple : « Dans une PME, un \
employé reçoit un courriel lui demandant de confirmer son mot de passe sur un lien. Que doit-il faire en \
priorité ? »
- L'apprenant n'a pas le texte sous les yeux : n'écris jamais « selon le texte », « d'après le guide », \
« ce document » ; ne pose pas de question sur le document lui-même (son objectif, son plan, sa date).
- Une seule bonne réponse, indiscutable : aucune mauvaise réponse ne doit être vraie ou défendable, même \
en partie. Évite « principal », « le plus important » ou « meilleur » si le texte ne fait pas lui-même ce \
classement.
- Trois mauvaises réponses plausibles : des erreurs que ferait vraiment un débutant (idées reçues, \
pratiques courantes mais déconseillées par le texte), de même forme et de longueur comparable à la bonne \
réponse. Pas de réponses absurdes ou hors sujet.
- La bonne réponse ne reprend pas les mots de l'énoncé.
- Les propositions sont courtes et neutres : aucune justification, aucune parenthèse explicative, aucun \
commentaire comme « non mentionné », « déconseillé » ou « réservé à… » qui trahirait la réponse.
- Interdit : « toutes les réponses », « aucune des réponses », les doubles négations, les questions piège \
sur un mot, les questions sur la mise en page ou la numérotation du document.
- L'explication (trois ou quatre phrases) enseigne : ce que recommande le guide (« Le guide recommande… »), \
le risque que cette mesure évite (« Sinon, un attaquant peut… »), en quoi l'erreur la plus tentante est \
fausse, puis une dernière phrase « À retenir : … » qui résume la règle.
- La source est courte : le numéro de recommandation (par exemple « R12 ») ou le titre de la partie, \
en moins de 60 caractères.
- Schéma (champ « schema ») : quand un dessin aide à comprendre la situation (architecture réseau, \
chemin d'une attaque, chaîne de confiance, organisation d'une équipe) ou quand on te le demande, au plus une \
question sur trois ; sinon laisse le champ vide. Syntaxe Mermaid simple, une ligne par lien, au plus 8 éléments, libellés courts \
en français : « flowchart LR », puis par exemple « PC[Poste de l'employé] -->|VPN| FW{Pare-feu} », \
« FW --> SRV[(Serveur de fichiers)] », « ATT[Attaquant]:::danger -.->|courriel piégé| PC ». Pas de \
subgraph, pas de style. L'énoncé peut alors parler du « schéma ».
- Les questions d'une même série portent sur des points différents.
Exemple complet d'une bonne question (forme à imiter ; n'en reprends ni le sujet ni les mots) :
{"question": "Dans une mairie, un agent en télétravail se connecte au serveur de fichiers comme sur le schéma. \
Quel élément protège les données échangées sur Internet ?", "bonne_reponse": "Le tunnel VPN chiffré", \
"mauvaises_reponses": ["Le mot de passe de la session Windows", "L'antivirus du poste", "Le Wi-Fi du domicile \
protégé par une clé"], "explication": "Le guide recommande de passer par un VPN pour tout accès distant au \
système d'information. Sinon, quelqu'un sur le même réseau peut lire ou modifier les échanges. L'antivirus \
protège le poste, pas le trajet des données. À retenir : un accès distant passe toujours par un tunnel chiffré.", \
"source": "R7", "schema": "flowchart LR\\nPC[Poste de l'agent] -->|VPN| FW{Pare-feu}\\nFW --> SRV[(Serveur de \
fichiers)]"}"""

# Référentiel ANSSI (exigences imposées à un prestataire) : l'apprenant prouve qu'il sait ce que le
# référentiel attend.
REFERENTIAL_RULES = """
Ce texte n'est pas un guide de bonnes pratiques mais un référentiel d'exigences ANSSI : il dit ce qu'un \
prestataire (ou un produit) doit faire pour être qualifié. Les questions vérifient que l'apprenant sait ce que le \
référentiel exige, pas qu'il en a retenu le vocabulaire :
- Mets en scène un prestataire, un auditeur ou un commanditaire face à une situation réelle : quelle exigence \
s'applique, ce que le prestataire doit faire ou fournir, quelle preuve un évaluateur attend, ce qui rend la \
situation non conforme.
- Distingue ce qui est exigé (« doit ») de ce qui est seulement recommandé (« il est recommandé »), quand le \
texte fait cette différence.
- Les mauvaises réponses sont des pratiques qu'un prestataire non préparé jugerait suffisantes.
- L'explication dit ce que le référentiel exige (« Le référentiel exige… »), pourquoi (le risque pour le \
commanditaire), puis « À retenir : … ».
- La source est le numéro de l'exigence quand le texte en donne un (par exemple « III.2.a »)."""

DIAGRAM_REQUEST = """
Au moins une de ces questions s'appuie sur un schéma (champ « schema ») : dessine une architecture, un flux ou \
une organisation tirée d'une situation du texte, puis demande si elle respecte le texte, quelle règle elle \
enfreint ou quel élément il faut ajouter ou corriger. L'énoncé parle alors « du schéma »."""

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "bonne_reponse": {"type": "string"},
            "mauvaises_reponses": {"type": "array", "items": {"type": "string"}},
            "explication": {"type": "string"},
            "source": {"type": "string"},
            "schema": {"type": "string"},
        },
        "required": ["question", "bonne_reponse", "mauvaises_reponses", "explication", "source"],
    }}},
    "required": ["questions"],
}
ANSWERS_SCHEMA = {
    "type": "object",
    "properties": {"reponses": {"type": "array", "items": {"type": "string"}}},
    "required": ["reponses"],
}
# Commentaires qui trahissent une mauvaise réponse : parenthèse explicative finale (3 mots ou plus,
# ou
# parlant du texte), ou incise finale du type « , réservé à… », « , qui… ».
GIVEAWAY_RE = re.compile(
    r"\s*(\((?:[^()\s]+\s+){2,}[^()]*\)"
    r"|\([^)]*\b(texte|mentionn|guide|document|recommand|d[ée]conseill|obsol|vuln[ée]rab)[^)]*\)"
    r"|,\s*(qui|ce qui|réservée?s?|considérée?s?|non"
    r" mentionnée?s?|mentionnée?s?|déconseillée?s?)\b.*)$",
    re.I,
)
FORBIDDEN_CHOICES = re.compile(
    r"\b(toutes? les (r[ée]ponses|propositions)|aucune des (r[ée]ponses|propositions))\b", re.I
)
# L'apprenant n'a pas le guide sous les yeux : « selon le texte » est retiré de l'énoncé, et une
# question sur le
# document lui-même (« Quel est l'objectif de ce document ? ») est écartée.
META_PHRASE_RE = re.compile(
    r"\s*,?\s*\b(selon|d['’]apr[èe]s|dans)\s+(le|ce|cet)\s+(texte|guide|extrait)\b\s*,?", re.I
)
META_QUESTION_RE = re.compile(
    r"\b(ce|cet)\s+(texte|document|extrait|guide)\b|\b(du|le|au)\s+texte\b", re.I
)
RECO_RE = re.compile(r"R\d{1,3}")  # source qui cite une recommandation (voir short_source)
DIAGRAM_MENTION_RE = re.compile(
    r"\b(le|ce|du|au)\s+sch[ée]ma\b(?!\s+(de|d['’]|des)\b)", re.I
)  # « le schéma » dessiné


class GenerationError(Exception):
    pass


class UnreadableAnswer(GenerationError):
    """Réponse du modèle inexploitable (JSON invalide ou coupée) : réessayer sur un texte plus court
    peut suffire."""


class SlowAnswer(UnreadableAnswer):
    """Réponse trop longue à venir (délai dépassé) : le plus souvent un modèle qui se répète en
    boucle."""


# --- Modèle de langage (Ollama) ----------------------------------------------------------
def model_name():
    return settings.QUIZ_LLM_MODEL


def chat(messages, schema, temperature=0.4, max_tokens=None, model=None):
    """
    Un échange avec le modèle (`model`, sinon celui des générations) ; la réponse est un objet JSON
    conforme à `schema`.
    `max_tokens` borne la longueur de la réponse : un modèle qui se met à boucler s'arrête vite
    (réponse coupée)
    au lieu d'occuper la carte graphique jusqu'au délai maximal.
    """
    options = {"temperature": temperature, "num_ctx": settings.QUIZ_LLM_CONTEXT}
    if max_tokens:
        options["num_predict"] = max_tokens
    body = {
        "model": model or model_name(),
        "messages": messages,
        "stream": False,
        "format": schema,
        "think": False,
        "options": options,
    }
    request = urllib.request.Request(settings.QUIZ_LLM_URL.rstrip("/") + "/api/chat",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT
        ) as response:  # nosec B310 (URL de configuration)
            data = json.load(response)
            content = data["message"]["content"]
    except Exception as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            raise SlowAnswer(
                f"réponse du modèle trop longue (délai de {TIMEOUT} s dépassé)"
            ) from exc
        raise GenerationError(f"modèle d'IA injoignable ou en erreur : {exc}") from exc
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        if (
            data.get("done_reason") == "length"
        ):  # contexte du modèle plein : réponse coupée en plein JSON
            raise UnreadableAnswer(
                "réponse du modèle coupée (extrait trop long pour son contexte)"
            ) from exc
        raise UnreadableAnswer("réponse du modèle illisible (JSON invalide)") from exc


# --- Découpage du guide ---------------------------------------------------------------------
@dataclass
class Part:
    title: str
    text: str


def split_parts(sections, count=len(SERIES)):
    """Texte du guide en `count` parties de taille voisine, dans l'ordre, coupées entre les
    paragraphes."""
    paragraphs = []
    for section in sections:
        paragraphs.append((section.title, f"## {section.title}"))
        paragraphs.extend((section.title, p) for p in section.text.split("\n") if p.strip())
    total = sum(len(p) for _, p in paragraphs) or 1
    parts, current, size, titles = [], [], 0, []
    for title, paragraph in paragraphs:
        current.append(paragraph)
        size += len(paragraph)
        if title not in titles:
            titles.append(title)
        if size >= total / count * (len(parts) + 1) and len(parts) < count - 1:
            parts.append(Part(" / ".join(titles)[:255], "\n".join(current)))
            current, titles = [], []
    if current or len(parts) < count:
        parts.append(Part(" / ".join(titles)[:255], "\n".join(current)))
    available = len(parts)
    while (
        len(parts) < count
    ):  # guide très court : les parties sont réutilisées (niveaux différents)
        parts.append(parts[len(parts) % available])
    return [Part(p.title, _fit(p.text)) for p in parts[:count]]


def _fit(text, limit=MAX_CONTEXT_CHARS):
    """Réduit un texte trop long en gardant des paragraphes répartis sur toute la partie."""
    if len(text) <= limit:
        return text
    paragraphs = text.split("\n")
    step = len(text) / limit
    kept, budget = [], limit
    for index, paragraph in enumerate(paragraphs):
        if index % max(1, round(step)) == 0 and len(paragraph) <= budget:
            kept.append(paragraph)
            budget -= len(paragraph)
    return "\n".join(kept)


# --- Contrôles ------------------------------------------------------------------------------
def _norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def clean_choice(text):
    """Proposition sans commentaire révélateur ni point final."""
    text = GIVEAWAY_RE.sub("", (text or "").strip()).strip()
    return text[:-1].rstrip() if text.endswith(".") and not text.endswith("..") else text


def short_source(source):
    """« R12 » si la source cite une recommandation, sinon un titre court."""
    source = re.sub(
        r"^(section|partie|chapitre)\s*[\d\s:–-]*", "", (source or "").strip(), flags=re.I
    )
    reco = re.search(r"\bR\s?\d{1,3}\b", source)
    if reco:
        return reco.group(0).replace(" ", "")
    return source.split(",")[0].strip(" '\"«»")[:120]


def clean_question(text):
    """Énoncé sans « selon le texte » ni espace superflu."""
    text = re.sub(r"\s+", " ", META_PHRASE_RE.sub(" ", (text or "").strip())).strip(" ,")
    text = re.sub(r"\s*([?:!])$", r" \1", text)
    return text[:1].upper() + text[1:]


def check_question(item, existing):
    """
    Question au format du brouillon, ou None si elle ne respecte pas les règles.
    Un schéma illisible est abandonné ; la question aussi si son énoncé parle du schéma.
    """
    question = clean_question(item.get("question"))
    diagram = (item.get("schema") or "").strip()
    if diagram and diagrams.problem(diagram):
        diagram = ""
    if META_QUESTION_RE.search(question) or (DIAGRAM_MENTION_RE.search(question) and not diagram):
        return None
    correct = clean_choice(item.get("bonne_reponse"))
    wrong = [clean_choice(w) for w in item.get("mauvaises_reponses") or [] if w and w.strip()]
    wrong = [w for w in wrong if w]
    choices = [correct] + wrong[:4]
    if len(question) < 15 or not correct or len(choices) < 4:
        return None
    if len({_norm(c) for c in choices}) != len(choices) or any(
        FORBIDDEN_CHOICES.search(c) for c in choices
    ):
        return None
    if any(len(c) > 300 for c in choices) or len(question) > 500:
        return None
    if any(
        difflib.SequenceMatcher(None, _norm(question), _norm(other)).ratio() > 0.85
        for other in existing
    ):
        return None  # doublon d'une question déjà retenue
    return {
        "text": question,
        "choices": choices,
        "explanation": (item.get("explication") or "").strip()[:1000],
        "source": short_source(item.get("source")),
        "diagram": diagram,
    }


def system_prompt(draft):
    return SYSTEM_PROMPT + REFERENTIAL_RULES if draft.referential_id else SYSTEM_PROMPT


def per_series(part):
    """Questions par série : 5, et davantage quand la partie du texte est longue (une question pour
    ~2 000 caractères)."""
    return max(PER_SERIES, min(MAX_PER_SERIES, len(part.text) // QUESTION_CHARS))


def generate_series(part, number, needed, existing, sources=(), system=SYSTEM_PROMPT):
    """
    Jusqu'à `needed` questions valides pour une série (plusieurs tentatives si besoin). Une
    recommandation
    (« R12 ») déjà interrogée dans la série (`sources` : celles des questions gardées) ne l'est de
    nouveau
    que faute d'autre choix : une partie courte peut ne contenir qu'une ou deux recommandations.
    """
    found, spare, used = [], [], {s for s in sources if RECO_RE.fullmatch(s)}
    # Au-delà du premier lot, le modèle repropose surtout ses questions : un essai de plus par lot
    # demandé
    for _attempt in range(MAX_ATTEMPTS + needed // BATCH):
        missing = needed - len(found)
        if missing <= 0:
            break
        avoid = (
            "\n".join(f"- {q}" for q in (existing + [f["text"] for f in found])[-30:]) or "(aucune)"
        )
        prompt = (
            f"Texte extrait du guide (partie {number} sur 6)"
            f" :\n\"\"\"\n{part.text}\n\"\"\"\n\nRédige {min(missing, BATCH) + 1} questions à choix"
            f" multiples de niveau {number} sur 6 : {LEVELS[number]}.\nQuestions déjà posées, à ne"
            f" pas répéter :\n{avoid}"
        )
        if number >= DIAGRAM_FROM and not any(f["diagram"] for f in found):
            prompt += DIAGRAM_REQUEST
        if used:
            prompt += (
                "\nRecommandations déjà interrogées, à ne pas reprendre :"
                f" {', '.join(sorted(used))}"
            )
        result = chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                      QUESTIONS_SCHEMA)
        for item in result.get("questions") or []:
            checked = check_question(item, existing + [f["text"] for f in found])
            if not checked or len(found) >= needed:
                continue
            if checked["source"] in used:
                spare.append(item)
                continue
            found.append(checked)
            if RECO_RE.fullmatch(checked["source"]):
                used.add(checked["source"])
    for item in spare:
        checked = check_question(item, existing + [f["text"] for f in found])
        if checked and len(found) < needed:
            found.append(checked)
    return found


def cross_check(part, questions):
    """Le modèle répond aux questions (propositions mélangées) : vrai si sa réponse est la bonne."""
    if not questions:
        return []
    letters = "ABCDE"
    shuffled, lines = [], []
    for index, q in enumerate(questions, start=1):
        order = list(range(len(q["choices"])))
        random.Random(q["text"]).shuffle(order)  # nosec B311 (aucun usage cryptographique)
        shuffled.append(order.index(0))
        options = " ".join(f"{letters[i]}) {q['choices'][o]}" for i, o in enumerate(order))
        figure = f" [Schéma : {q['diagram']}]" if q.get("diagram") else ""
        lines.append(f"{index}. {q['text']}{figure} {options}")
    prompt = (
        f"Texte :\n\"\"\"\n{part.text}\n\"\"\"\n\nRéponds à chaque question d'après le texte"
        " uniquement. Donne seulement la lettre de la bonne réponse, dans l'ordre des"
        " questions.\n\n"
        + "\n".join(lines)
    )
    try:
        answers = (
            chat([{"role": "user", "content": prompt}], ANSWERS_SCHEMA, temperature=0).get(
                "reponses"
            )
            or []
        )
    except GenerationError:
        return [None] * len(questions)
    results = []
    for index, correct in enumerate(shuffled):
        given = (answers[index] if index < len(answers) else "").strip().upper()[:1]
        results.append(given == letters[correct] if given else None)
    return results


# --- File d'attente -----------------------------------------------------------------------------
def guides_without_quiz():
    """Guides publiés en français, avec un PDF (le texte à interroger), sans QCM ni génération en
    cours."""
    # sans les brouillons de référentiels : un NULL dans « NOT IN » vide la sélection
    active = (
        QuizDraft.objects.filter(status__in=QuizDraft.ACTIVE).exclude(guide=None).values("guide")
    )
    return (
        generation.eligible_guides()
        .filter(quiz_questions__isnull=True)
        .exclude(pk__in=active)
        .distinct()
    )


def has_pdf(guide):
    if isinstance(guide, Referential):
        return bool(guide.document)
    return guide.documents.exclude(file="").exists()


def referentials_with_pdf():
    """Référentiels ANSSI dont le PDF peut être interrogé (les programmes d'examen et normes n'ont
    pas de texte)."""
    return Referential.objects.filter(kind=Referential.Kind.ANSSI, is_published=True).exclude(
        document=""
    )


def request(guide, user=None):
    """
    Brouillon actif d'un guide ou d'un référentiel ANSSI (créé s'il n'y en a pas). Sans PDF, pas de
    QCM :
    il n'y a pas de texte dont tirer des questions.
    """
    if isinstance(guide, Referential) and guide.kind != Referential.Kind.ANSSI:
        raise GenerationError("seuls les référentiels ANSSI ont un texte dont tirer des questions")
    if not has_pdf(guide):
        raise GenerationError(
            "ce guide n'a pas de document PDF : il n'y a pas de texte dont tirer des questions"
            if isinstance(guide, Guide)
            else "ce référentiel n'a pas de document PDF"
        )
    draft = QuizDraft.objects.filter(**subject_filter(guide), status__in=QuizDraft.ACTIVE).first()
    return draft or QuizDraft.objects.create(**subject_filter(guide), requested_by=user)


def take_next():
    with transaction.atomic():
        draft = (QuizDraft.objects.select_for_update(skip_locked=True)
                 .filter(status=Status.PENDING).order_by(*QuizDraft.QUEUE_ORDER).first())
        if draft is None:
            return None
        draft.status, draft.started_at, draft.error = Status.GENERATING, timezone.now(), ""
        draft.llm_model = model_name()
        draft.save(update_fields=["status", "started_at", "error", "llm_model"])
        return draft


def recover_stale(hours=3, immediate=False):
    """Remet en file les générations interrompues ; `immediate` : au démarrage du service « quizgen
    »."""
    stale = QuizDraft.objects.filter(status=Status.GENERATING)
    if not immediate:
        stale = stale.filter(started_at__lt=timezone.now() - timedelta(hours=hours))
    return stale.update(status=Status.PENDING)


def generate(draft, progress=None, checkpoint=None):
    """
    Complète le brouillon : pour chaque série, autant de questions que nécessaire pour en avoir
    per_series() non rejetées (une première génération, ou le remplacement des questions rejetées).
    `checkpoint` est appelé avant chaque série (pause) : les séries déjà faites sont gardées.
    """
    if draft.referential_id:
        file = draft.referential.document or None
    else:
        document = draft.guide.documents.exclude(file="").order_by("position", "pk").first()
        file = document and document.file
    if not file:
        raise GenerationError("ce document n'a pas de PDF")
    if not file.storage.exists(file.name):
        raise GenerationError(f"PDF introuvable par le service ({file.name}) : "
                              "le volume des médias est-il monté dans le conteneur ?")
    with file.open("rb") as pdf:
        sections = narration_text.extract_sections(pdf, spoken=False)
    if not sections:
        raise GenerationError("aucun texte lisible dans le PDF")
    parts = split_parts(sections)

    for number in SERIES:
        kept = list(draft.questions.filter(series=number).exclude(decision=Decision.REJECTED))
        part = parts[number - 1]
        needed = per_series(part) - len(kept)
        if needed <= 0:
            continue
        if checkpoint:
            checkpoint()
        draft.progress = f"Série {number} sur 6"
        draft.save(update_fields=["progress"])
        if progress:
            progress(number)
        existing = list(
            draft.questions.exclude(decision=Decision.REJECTED).values_list("text", flat=True)
        )
        found = generate_series(
            part, number, needed, existing, [q.source for q in kept], system_prompt(draft)
        )
        if (
            len(kept) + len(found) < PER_SERIES
        ):  # au-delà du minimum, per_series() n'est qu'un objectif
            raise GenerationError(
                f"série {number} : seulement {len(kept) + len(found)} question(s) valide(s) "
                f"sur {PER_SERIES} au minimum"
            )
        checks = cross_check(part, found)
        last = max([q.position for q in draft.questions.filter(series=number)] or [0])
        for offset, (item, check) in enumerate(zip(found, checks), start=1):
            QuizDraftQuestion.objects.create(draft=draft, series=number, position=last + offset,
                                             context_title=part.title, cross_check=check, **item)

    prereview.review_quiz(draft, parts, checkpoint)  # contrôle par l'IA avant la relecture humaine
    # Complété à la demande d'un rédacteur : le brouillon revient dans son atelier ; sinon, en
    # relecture.
    draft.status = Status.WRITING if draft.author_id else Status.REVIEW
    draft.generated_at, draft.progress = timezone.now(), ""
    draft.save(update_fields=["status", "generated_at", "progress"])
    return draft


def suspend(draft):
    """Pause demandée : retour dans la file, séries déjà générées conservées."""
    draft.status, draft.progress = Status.PENDING, "Interrompu par la pause"
    draft.save(update_fields=["status", "progress"])


def fail(draft, error):
    draft.status, draft.error, draft.progress = Status.FAILED, str(error)[:2000], ""
    draft.save(update_fields=["status", "error", "progress"])


# --- Relecture et publication --------------------------------------------------------------
def accepted_by_series(draft):
    counts = {number: 0 for number in SERIES}
    for series in draft.questions.filter(decision=Decision.ACCEPTED).values_list(
        "series", flat=True
    ):
        counts[series] = counts.get(series, 0) + 1
    return counts


def can_publish(draft):
    return draft.status == Status.REVIEW and all(
        n >= PER_SERIES for n in accepted_by_series(draft).values()
    )


@transaction.atomic
def publish(draft, user):
    """Publie les questions acceptées (visibles des apprenants) et, si possible, les exporte en
    JSON."""
    if not can_publish(draft):
        raise GenerationError(
            f"il faut au moins {PER_SERIES} questions acceptées dans chacune des 6 séries"
        )
    questions = [
        {
            "series": q.series,
            "question": q.text,
            "choices": q.choices,
            "explanation": q.explanation,
            "source": q.source,
            **({"diagram": q.diagram} if q.diagram else {}),
        }
        for q in draft.questions.filter(decision=Decision.ACCEPTED).order_by("series", "position")
    ]
    import_quizzes.validate({"questions": questions}, str(draft.subject))
    import_quizzes.store(draft.subject, questions)
    draft.status, draft.published_at, draft.published_by = Status.PUBLISHED, timezone.now(), user
    draft.save(update_fields=["status", "published_at", "published_by"])
    QuizDraft.objects.filter(**subject_filter(draft.subject), status__in=QuizDraft.ACTIVE).exclude(
        pk=draft.pk).update(status=Status.DISCARDED)
    return export(draft.subject, questions)


def export(guide, questions):
    """
    Écrit le QCM d'un guide dans catalogue/data/quiz/ (versionné avec le code) quand le dossier est
    accessible
    en écriture. shortcut: ceux des référentiels ne restent qu'en base, à exporter si on veut les
    réimporter.
    """
    if not getattr(guide, "anssi_id", None):
        return None
    path = import_quizzes.DATA_DIR / f"{guide.anssi_id}.json"
    try:
        path.write_text(
            json.dumps(
                {"guide": guide.anssi_id, "questions": questions}, ensure_ascii=False, indent=1
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:  # image de production en lecture seule : la base suffit
        return None
    return path
