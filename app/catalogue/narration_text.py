"""
Texte à lire à voix haute, à partir d'un PDF de guide.

  - Le texte est extrait avec pdfminer, qui reconstitue les espaces d'après la
    position des caractères et sépare les blocs (titres, paragraphes, encadrés).
  - Le PDF est découpé en sections d'après son sommaire (signets, lu avec pypdf) :
    les chapitres, et leurs sous-chapitres quand un chapitre est trop long.
    Sans sommaire, on découpe par tranches de pages.
  - On retire ce qui ne se lit pas : en-têtes et pieds de page répétés, numéros de
    page, sommaire à points de suite, adresses web, renvois bibliographiques, puces,
    blocs de code ou de chiffres. Quelques sigles sont épelés pour la synthèse vocale.

Aucune dépendance à la synthèse vocale ici : ce module est testable seul.
"""
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextContainer
from pypdf import PdfReader

VERSION = 2  # à incrémenter quand le nettoyage change : les versions audio seront régénérées
PAGES_PER_CHUNK = 8          # sections sans sommaire
MAX_SECTION_CHARS = 12000    # au-delà (≈ 13 min), on descend aux sous-chapitres
MIN_SECTION_CHARS = 40
SHORT_INTRO_CHARS = 600

SKIPPED_TITLES = re.compile(
    r"^\W*(\d+(\.\d+)*\s*)?(table des mati[eè]res|sommaire|liste des"
    r" (recommandations|figures|tableaux)|"
    r"bibliographie|r[ée]f[ée]rences( bibliographiques)?|index|glossaire|acronymes|sigles|"
    r"annexe.*(glossaire|bibliographie))\b",
    re.I,
)

# Sigles mal prononcés par la synthèse : on les épelle (ou on les développe).
SPOKEN = {
    "SI": "S I",
    "SSI": "S S I",
    "RSSI": "R S S I",
    "DSI": "D S I",
    "PSSI": "P S S I",
    "SIE": "S I E",
    "PDF": "P D F",
    "DNS": "D N S",
    "VPN": "V P N",
    "TLS": "T L S",
    "SSH": "S S H",
    "SSL": "S S L",
    "HTTP": "H T T P",
    "HTTPS": "H T T P S",
    "IP": "I P",
    "API": "A P I",
    "USB": "U S B",
    "EDR": "E D R",
    "XDR": "X D R",
    "IDS": "I D S",
    "IPS": "I P S",
    "PKI": "P K I",
    "IGC": "I G C",
    "MCS": "M C S",
    "MCO": "M C O",
    "PCA": "P C A",
    "PRA": "P R A",
    "DR": "Diffusion Restreinte",
    "AD": "Active Directory",
    "OIV": "O I V",
    "OSE": "O S E",
    "DMZ": "D M Z",
    "LAN": "L A N",
    "WAN": "W A N",
    "MFA": "M F A",
    "UE": "U E",
    "RGPD": "R G P D",
    "RGS": "R G S",
    "EBIOS": "Ébios",
    "SIEM": "Sim",
    "IAM": "I A M",
    "OT": "O T",
    "IoT": "I o T",
    "VLAN": "V LAN",
    "TPM": "T P M",
    "UEFI": "U E F I",
    "OS": "O S",
    "HSM": "H S M",
    "IPsec": "I P sec",
    "RFC": "R F C",
    "NAT": "Natte",
    "FTP": "F T P",
    "RSH": "R S H",
    "RCP": "R C P",
    "TCP": "T C P",
    "UDP": "U D P",
    "ANSSI": "Anssi",
    "CNIL": "Cnil",
}
SPOKEN_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, SPOKEN), key=len, reverse=True)) + r")\b"
)

URL_RE = re.compile(r"(https?://|www\.)[^\s)\]>»,;]+", re.I)
EMPTY_PARENS_RE = re.compile(r"\(\s*((voir|cf\.?|sur)\s*)?\)", re.I)
REFERENCE_RE = re.compile(r"\[[^\]\n]{1,25}\]")
DOT_LEADER_RE = re.compile(r"(\.\s?){4,}|…{2,}|(\s\.){3,}")
PAGE_NUMBER_RE = re.compile(r"^(page\s+)?\d{1,4}(\s*(/|sur)\s*\d{1,4})?$", re.I)
BULLET_RE = re.compile(r"^(?:[•▪■●◦‣∙\-–—»]|n(?=\s+[a-zàâäéèêëîïôöùûüç]))\s+")
HEADING_NUMBER_RE = re.compile(r"^\d+(\.\d+)*\.?\s+(?=\S)")
SLIDE_RE = re.compile(r"^diapositive\s+\d+\s*", re.I)
CAPTION_RE = re.compile(r"^(figure|tableau|illustration|sch[ée]ma)\s+\d+\s*[–:.-]", re.I)
RECO_SPLIT_RE = re.compile(r"\bR(\d) (\d)\b")  # « R1 1 » : artefact d'extraction de « R11 »
RECO_RE = re.compile(r"\bR(\d{1,3})(?:\s?[+\-]+)?(?=\W|$)")
SENTENCE_END = (".", "!", "?", ":", ";", "»", ")")


@dataclass
class Section:
    title: str
    text: str


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "")  # ligatures « ﬁ », espaces insécables…
    return text.replace("­", "").replace("’", "'").replace("\x0c", "")


def signature(text):
    """Forme d'un bloc sans ses chiffres ni espaces : repère les en-têtes et pieds de page
    répétés."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.strip().lower()))


def _readable(text):
    """Faux pour les blocs de code, de chiffres ou de symboles (lignes de commande, tableaux)."""
    letters = sum(c.isalpha() or c.isspace() for c in text)
    return letters >= 0.75 * len(text)


def clean_block(block, repeated=frozenset()):
    """Paragraphe lisible à partir d'un bloc de texte extrait (None s'il n'y a rien à lire)."""
    if signature(block) in repeated:
        return None
    lines = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line or PAGE_NUMBER_RE.match(line) or DOT_LEADER_RE.search(line):
            continue
        line = BULLET_RE.sub("", URL_RE.sub("", REFERENCE_RE.sub("", line))).strip()
        if line:
            lines.append(line)
    text = ""
    for line in lines:  # recolle les lignes (césures en fin de ligne)
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}" if text else line
    text = re.sub(r"\s{2,}", " ", EMPTY_PARENS_RE.sub("", text))
    text = HEADING_NUMBER_RE.sub("", text).strip()
    if len(text) <= 2 or not _readable(text) or CAPTION_RE.match(text):
        return None
    return text


def paragraphs_to_text(paragraphs):
    """
    Assemble les blocs : un bloc sans ponctuation finale suivi d'une minuscule est un
    paragraphe coupé (fin de page, encadré) et se recolle au suivant ; sinon c'est un
    titre, suivi d'un point pour marquer une pause à la lecture.
    """
    out = []
    for paragraph in paragraphs:
        if out and not out[-1].endswith(SENTENCE_END) and paragraph[:1].islower():
            out[-1] = f"{out[-1]} {paragraph}"
        else:
            if out and not out[-1].endswith(SENTENCE_END):
                out[-1] += "."
            out.append(paragraph)
    if out and not out[-1].endswith(SENTENCE_END):
        out[-1] += "."
    return "\n".join(out)


def speakable(text):
    """Adaptations pour la synthèse vocale : recommandations, sigles, espaces."""
    text = RECO_SPLIT_RE.sub(r"R\1\2", text)
    text = RECO_RE.sub(r"recommandation \1", text)
    text = SPOKEN_RE.sub(lambda m: SPOKEN[m.group(1)], text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip()


def page_blocks(data):
    """Blocs de texte de chaque page (liste de listes)."""
    pages = []
    for layout in extract_pages(io.BytesIO(data), laparams=LAParams()):
        blocks = [
            normalize(element.get_text())
            for element in layout
            if isinstance(element, LTTextContainer)
        ]
        pages.append([b for b in blocks if b.strip()])
    return pages


def repeated_blocks(pages, min_ratio=0.3):
    counts = Counter()
    for blocks in pages:
        counts.update({signature(block) for block in blocks})
    threshold = max(3, int(len(pages) * min_ratio))
    return {sig for sig, n in counts.items() if n >= threshold}


def _is_toc_page(blocks):
    return (
        sum(1 for block in blocks for line in block.splitlines() if DOT_LEADER_RE.search(line)) >= 4
    )


# --- Sommaire -------------------------------------------------------------------------------
@dataclass
class Entry:
    title: str
    page: int
    children: list


def outline_tree(reader):
    """Arbre du sommaire (signets) : titres et pages de début."""
    def build(items):
        entries = []
        for item in items:
            if isinstance(item, list):
                if entries:
                    entries[-1].children = build(item)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:  # signet sans page
                continue
            if page is None:  # destination introuvable (lien externe, page retirée)
                continue
            title = SLIDE_RE.sub("", normalize(str(item.title))).strip()
            entries.append(Entry(title, page, []))
        return entries

    try:
        tree = build(reader.outline)
    except Exception:
        return []
    # Un seul chapitre racine (titre du document) : on part de ses enfants.
    while len(tree) == 1 and tree[0].children:
        tree = tree[0].children
    return tree


def _flatten(entries, page_count, sizes, prefix=""):
    """(titre, début, fin) des sections ; un chapitre trop long est remplacé par ses
    sous-chapitres."""
    ranges = []
    ordered = sorted(entries, key=lambda e: e.page)
    for i, entry in enumerate(ordered):
        end = ordered[i + 1].page if i + 1 < len(ordered) else page_count
        end = max(end, entry.page + 1)
        title = f"{prefix}{entry.title}" if entry.title else f"{prefix}Partie {i + 1}"
        too_long = sum(sizes[entry.page:end]) > MAX_SECTION_CHARS
        if too_long and len(entry.children) >= 2:
            first_child = min(c.page for c in entry.children)
            # Introduction du chapitre, avant son premier sous-chapitre (ignorée si trop courte).
            ranges.append((title, entry.page, max(first_child, entry.page + 1)))
            children = [Entry(c.title, c.page, c.children) for c in entry.children]
            children[-1:] = [Entry(children[-1].title, children[-1].page, children[-1].children)]
            sub = _flatten(children, end, sizes, prefix=f"{title} — ")
            ranges.extend(sub)
        else:
            ranges.append((title, entry.page, end))
    return ranges


def _start_index(paragraphs, title):
    """Position du titre de section parmi les blocs de sa page de début (0 s'il est introuvable)."""
    wanted = signature(HEADING_NUMBER_RE.sub("", title).rstrip("."))
    for index, paragraph in enumerate(paragraphs):
        found = signature(paragraph.rstrip("."))
        if found == wanted or (len(wanted) >= 12 and found.startswith(wanted)):
            return index
    return 0


def extract_sections(pdf_file, spoken=True):
    """
    Sections lisibles d'un PDF (objet fichier binaire ou contenu en octets).
    `spoken` : adaptations pour la synthèse vocale (sigles épelés…) ; False pour garder l'écrit
    (QCM).
    """
    data = pdf_file if isinstance(pdf_file, bytes) else pdf_file.read()
    pages = page_blocks(data)
    if not pages:
        return []
    repeated = repeated_blocks(pages)
    cleaned = [
        [] if _is_toc_page(blocks) else [p for p in (clean_block(b, repeated) for b in blocks) if p]
        for blocks in pages
    ]
    sizes = [sum(len(p) for p in paragraphs) for paragraphs in cleaned]

    tree = outline_tree(PdfReader(io.BytesIO(data)))
    if len(tree) >= 2:
        ranges = _flatten(tree, len(pages), sizes)
    else:  # pas de sommaire exploitable : tranches de pages, sans la couverture
        first = 1 if len(pages) > 2 else 0
        ranges = [(f"Pages {start + 1} à {min(start + PAGES_PER_CHUNK, len(pages))}", start,
                   min(start + PAGES_PER_CHUNK, len(pages)))
                  for start in range(first, len(pages), PAGES_PER_CHUNK)]

    # Les sections commencent à leur titre (un chapitre peut débuter en milieu de page) et
    # s'arrêtent au titre de la suivante.
    flat = [
        (page, paragraph) for page, paragraphs in enumerate(cleaned) for paragraph in paragraphs
    ]
    offsets = [0]
    for paragraphs in cleaned:
        offsets.append(offsets[-1] + len(paragraphs))
    starts = []
    for title, start, _end in ranges:
        last_part = title.split(" — ")[-1]
        starts.append(
            offsets[start] + _start_index(cleaned[start], last_part)
            if start < len(cleaned)
            else len(flat)
        )

    sections, carry = [], ""
    for i, (title, _start, _end) in enumerate(ranges):
        bare_title = HEADING_NUMBER_RE.sub("", title.split(" — ")[-1]).strip()
        if SKIPPED_TITLES.match(bare_title) or SKIPPED_TITLES.match(title):
            continue
        stop = starts[i + 1] if i + 1 < len(starts) else len(flat)
        paragraphs = [paragraph for _page, paragraph in flat[starts[i]:max(stop, starts[i])]]
        # Le titre figure en tête de section : on évite de le lire deux fois.
        if paragraphs and signature(paragraphs[0].rstrip(".")) == signature(bare_title):
            paragraphs = paragraphs[1:]
        text = paragraphs_to_text(paragraphs)
        text = speakable(text) if spoken else re.sub(r"[ \t]+", " ", text).strip()
        if carry:
            text, carry = carry + "\n" + text, ""
        next_title = ranges[i + 1][0] if i + 1 < len(ranges) else ""
        if len(text) < SHORT_INTRO_CHARS and next_title.startswith(f"{title} — "):
            carry = text  # courte introduction de chapitre : lue au début du premier sous-chapitre
            continue
        if len(text) >= MIN_SECTION_CHARS:
            # Titre affiché : les deux derniers niveaux du sommaire suffisent (« Chapitre — Partie
            # »).
            short_title = " — ".join(
                HEADING_NUMBER_RE.sub("", part) for part in title.split(" — ")[-2:]
            )
            sections.append(Section(title=short_title[:255], text=text))
    return sections


def chunks(text, limit=1200):
    """Morceaux de texte d'au plus `limit` caractères, coupés entre les phrases."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?:;])\s+", text):
        while len(sentence) > limit:  # phrase démesurée : coupe sur un espace
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                pieces.append(current.strip())
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current.strip())
            current = ""
        current = f"{current} {sentence}" if current else sentence
    if current.strip():
        pieces.append(current.strip())
    return [p for p in pieces if p]
