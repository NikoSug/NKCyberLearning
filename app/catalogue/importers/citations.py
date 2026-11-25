"""
Documents cités par un référentiel ANSSI (annexe « Références documentaires »)
et rapprochement avec les guides importés.

Les entrées ont la forme « [HYGIENE] Guide d'hygiène informatique, ANSSI,
version en vigueur. Disponible sur … » ou « 12. Guide - … ». Les titres cités
ne sont pas toujours identiques aux titres actuels des guides : le
rapprochement est approximatif et peut être corrigé dans l'administration.
"""
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

BRACKET_ENTRY = re.compile(r"^\s*\[([A-Za-z0-9][A-Za-z0-9_\-. ]{0,40})\]\s*(.*)$")
NUMBERED_ENTRY = re.compile(r"^\s*(\d{1,3})\.\s+(\S.*)$")
STOP = re.compile(r"^\s*(renvoi\s+document|[IVX]+\.\s|annexe\s|[a-z]\)\s)", re.I)
BIBLIOGRAPHIC = re.compile(
    r"disponibles? sur|version en vigueur|ssi\.gouv|cyber\.gouv|\banssi\b", re.I
)
TITLE_END = re.compile(
    r",\s*(guide anssi|guide n|note|anssi\b|version|référence|n°|du \d|publi|disponible|\d{4})"
    r"|\.\s+(disponible|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)"
    r"|\s+[–-]\s+version|\.\s*$|\.\s+[A-Z]|\s*:\s*(www|https?:)|\s+\[",
    re.I,
)
NUMBERED_REFERENCE = re.compile(r"disponibles? sur|version en vigueur", re.I)
MAX_LINES = 6  # une référence tient en quelques lignes : au-delà, c'est du texte courant
STOP_WORDS = re.compile(
    r"\b(l|d|de|des|du|la|le|les|un|une|et|a|au|aux|en|pour|sur|relatives?|guide|anssi)\b")


@dataclass
class Citation:
    code: str
    text: str

    @property
    def title(self):
        text = re.sub(r"^guide\s*[-–]\s*", "", self.text, flags=re.I)
        return TITLE_END.split(text, maxsplit=1)[0].strip(" .,;«»")

    @property
    def is_anssi(self):
        lowered = self.text.lower()
        return "anssi" in lowered or "ssi.gouv" in lowered or "cyber.gouv" in lowered


def parse_citations(lines):
    """
    Références bibliographiques du document : entrées « [CODE] … » ou « 12. … ».
    Seules les entrées qui ressemblent à une référence (« Disponible sur »,
    « version en vigueur », ANSSI…) sont conservées.
    """
    citations, current = [], None

    def flush():
        if current:
            text = re.sub(r"\s+", " ", " ".join(current["parts"])).strip()
            # Une liste numérotée du texte courant peut citer l'ANSSI : on exige alors une vraie
            # référence.
            marker = BIBLIOGRAPHIC if current["code"] else NUMBERED_REFERENCE
            if marker.search(text):
                citations.append(Citation(current["code"], text))

    for line in lines:
        bracket, numbered = BRACKET_ENTRY.match(line), NUMBERED_ENTRY.match(line)
        if bracket or numbered:
            flush()
            current = ({"code": bracket.group(1).strip(), "parts": [bracket.group(2)]} if bracket
                       else {"code": "", "parts": [numbered.group(2)]})
            continue
        if current is None:
            continue
        if STOP.match(line) or len(current["parts"]) >= MAX_LINES:
            flush()
            current = None
            continue
        current["parts"].append(line)
        if re.search(r"disponibles? sur", line, re.I):
            flush()
            current = None
    flush()

    # Une même référence peut apparaître plusieurs fois : l'annexe (en fin de document) fait foi.
    unique = {}
    for citation in citations:
        unique[citation.code or normalize(citation.title)] = citation
    return [c for c in unique.values() if c.title]


def normalize(text):
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("œ", "oe")).encode(
        "ascii", "ignore"
    )
    text = re.sub(r"[^a-z0-9 ]+", " ", text.decode().lower())
    return re.sub(r"\s+", " ", STOP_WORDS.sub(" ", text)).strip()


def similarity(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    shorter, longer = sorted((a, b), key=len)
    if len(shorter.split()) >= 2 and shorter in longer and len(shorter) >= 0.5 * len(longer):
        return 0.95
    small, large = sorted((set(a.split()), set(b.split())), key=len)
    if len(small) >= 3 and small <= large and len(small) >= 0.6 * len(large):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


# Guides renommés depuis leur citation : (mots de la citation, mots du titre actuel), normalisés.
ALIASES = [
    ("homologation securite", "homologation securite systemes information"),
    ("ebios", "methode ebios risk manager"),
    ("restrictions logicielles", "politique restrictions logicielles sous windows"),
    ("sites web", "securiser site web"),
    ("defense profondeur", "defense profondeur"),
    ("cartographie systeme information", "cartographie systeme information"),
    ("802 1x", "deploiement protocole 802 1x"),
    ("ipsec", "recommandations securite ipsec"),
    ("architectures systemes information sensibles", "architectures si sensibles ou dr"),
    ("externalisation", "externalisation securite systemes information"),
    ("rancongiciels", "attaques par rancongiciels"),
    ("virtualisation systemes information", "securite systemes virtualisation"),
    ("cybersecurite systemes industriels", "cybersecurite systemes industriels mesures detaillees"),
    ("vie privee", "restreindre collecte donnees sous windows 10"),
    ("reseaux wi fi", "securisation reseaux wi fi"),
]
MATCH_THRESHOLD = 0.82


def best_guide(title, guides):
    """Guide dont le titre ressemble le plus au titre cité (ou None)."""
    cited = normalize(title)
    for cited_words, guide_words in ALIASES:
        if cited_words in cited:
            for guide in sorted(guides, key=lambda g: g.language != "fr"):
                if guide_words in normalize(guide.title):
                    return guide
    best, best_score = None, MATCH_THRESHOLD
    for guide in guides:
        score = similarity(title, guide.title)
        if score > best_score or (score == best_score and best and guide.language == "fr"):
            best, best_score = guide, score
    return best
