"""
Schémas d'exemple attachés aux questions de QCM (facultatifs : la plupart des questions n'en ont
pas).

Le schéma est écrit dans un petit sous-ensemble de la syntaxe Mermaid « flowchart », connue des
rédacteurs
et des modèles d'IA, puis dessiné en SVG côté serveur par ce module : aucune bibliothèque JavaScript
ni CDN,
et aucun HTML ou SVG fourni par l'IA n'arrive dans la page (seuls des libellés échappés et des
nombres).

Syntaxe acceptée :
    flowchart LR                      (ou TD : de haut en bas ; première ligne facultative)
    PC[Poste de l'employé] -->|HTTPS| FW{Pare-feu}
    FW --> SRV[(Base clients)]
    ATT[Attaquant]:::danger -.->|hameçonnage| PC
    A -- texte --> B --> C            (enchaînements et libellés « -- texte --> » acceptés)

Formes : [rectangle], (arrondi), {contrôle}, [(base de données)]. Liens : --> flèche, --- trait,
-.-> pointillés (attaque, flux non autorisé), ==> trait épais. Classes : :::danger (attaquant,
élément
compromis), :::ok (élément de confiance). Les lignes « %% » sont des commentaires ; classDef, style,
class
et linkStyle sont ignorés ; subgraph et click sont refusés.
"""

import hashlib
import re
import textwrap
from dataclasses import dataclass, field

from django.utils.html import escape

MAX_CHARS = 2000
MAX_NODES = 12
MAX_EDGES = 20
MAX_LABEL = 60
DIRECTIONS = {"LR": "LR", "RL": "LR", "TD": "TD", "TB": "TD", "BT": "TD"}
CLASSES = {"danger", "ok"}

HEADER_RE = re.compile(r"^(flowchart|graph)(\s+(?P<dir>[A-Za-z]{2}))?\s*;?$", re.I)
IGNORED_RE = re.compile(r"^(classDef|style|class|linkStyle)\b", re.I)
REFUSED_RE = re.compile(r"^(subgraph|end|click)\b", re.I)
NODE_RE = re.compile(
    r"(?P<id>[^\W\d]\w*)"
    r"(?P<shape>\[\(.*?\)\]|\[\".*?\"\]|\[[^\]]*\]|\(\".*?\"\)|\([^)]*\)|\{\".*?\"\}|\{[^}]*\})?"
    r"(?::::(?P<cls>[\w-]+))?")
EDGE_RE = re.compile(
    r"\s*(?:--\s*(?P<inline>[^-|>\s][^>]*?)\s*(?P<op1>-->|---)"
    r"|(?P<op2>-\.->|-\.-|==>|-->|---)(?:\s*\|(?P<label>[^|]*)\|)?)\s*")
SHAPES = (("[(", ")]", "db"), ("[", "]", "rect"), ("(", ")", "round"), ("{", "}", "check"))


class DiagramError(ValueError):
    pass


@dataclass
class Node:
    id: str
    label: str
    shape: str = "rect"
    cls: str = ""


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    style: str = "arrow"   # arrow, line, dashed, thick


@dataclass
class Diagram:
    direction: str = "LR"
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)


def _label(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    raw = re.sub(r"<br\s*/?>", " ", raw, flags=re.I)
    return re.sub(r"\s+", " ", raw).strip()


def _node(diagram, match, number):
    node_id, shape, cls = (
        match.group("id"),
        match.group("shape"),
        (match.group("cls") or "").lower(),
    )
    node = diagram.nodes.get(node_id)
    if node is None:
        if len(diagram.nodes) >= MAX_NODES:
            raise DiagramError(f"trop d'éléments : {MAX_NODES} au maximum")
        node = diagram.nodes[node_id] = Node(node_id, node_id)
    if shape:
        for start, end, kind in SHAPES:
            if shape.startswith(start) and shape.endswith(end):
                label = _label(shape[len(start):-len(end)])
                if not label:
                    raise DiagramError(f"ligne {number} : libellé vide pour « {node_id} »")
                if len(label) > MAX_LABEL:
                    raise DiagramError(
                        f"ligne {number} : libellé trop long ({MAX_LABEL} caractères au maximum)"
                    )
                node.label, node.shape = label, kind
                break
    if cls in CLASSES:
        node.cls = cls
    return node_id


def parse(text):
    """Diagramme décrit par `text` ; DiagramError (message en français) si la syntaxe n'est pas
    reconnue."""
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        raise DiagramError(f"schéma trop long ({MAX_CHARS} caractères au maximum)")
    diagram = Diagram()
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.split("%%")[0].strip().rstrip(";").strip()
        if not line or IGNORED_RE.match(line):
            continue
        header = HEADER_RE.match(line)
        if header:
            if number > 1 and diagram.nodes:
                raise DiagramError(
                    f"ligne {number} : « {header.group(1)} » doit être la première ligne"
                )
            if header.group("dir"):
                if header.group("dir").upper() not in DIRECTIONS:
                    raise DiagramError(f"ligne {number} : sens inconnu (LR ou TD)")
                diagram.direction = DIRECTIONS[header.group("dir").upper()]
            continue
        if REFUSED_RE.match(line):
            raise DiagramError(f"ligne {number} : « {line.split()[0]} » n'est pas pris en charge")
        match = NODE_RE.match(line)
        if not match:
            raise DiagramError(f"ligne {number} illisible : « {line[:40]} »")
        source, pos = _node(diagram, match, number), match.end()
        while pos < len(line):
            edge = EDGE_RE.match(line, pos)
            target = edge and NODE_RE.match(line, edge.end())
            if not target:
                raise DiagramError(
                    f"ligne {number} illisible près de « {line[pos:pos + 30].strip()} »"
                )
            op = edge.group("op1") or edge.group("op2")
            label = _label(edge.group("inline") or edge.group("label") or "")
            if len(label) > MAX_LABEL:
                raise DiagramError(
                    f"ligne {number} : libellé de lien trop long ({MAX_LABEL} caractères au"
                    " maximum)"
                )
            target_id = _node(diagram, target, number)
            if len(diagram.edges) >= MAX_EDGES:
                raise DiagramError(f"trop de liens : {MAX_EDGES} au maximum")
            style = {"---": "line", "-.-": "dashed", "-.->": "dashed", "==>": "thick"}.get(
                op, "arrow"
            )
            diagram.edges.append(Edge(source, target_id, label, style))
            source, pos = target_id, target.end()
    if not diagram.edges:
        raise DiagramError(
            "aucun lien entre des éléments (par exemple « A[Poste] --> B[Serveur] »)"
        )
    return diagram


def problem(text):
    """Message d'erreur si le schéma (non vide) est invalide, sinon None."""
    if not (text or "").strip():
        return None
    try:
        parse(text)
    except DiagramError as exc:
        return str(exc)
    return None


# --- Disposition ---------------------------------------------------------------------------------
CHAR_W, LINE_H, PAD_X, PAD_Y = 7.4, 17, 14, 10
NODE_GAP, MARGIN, WRAP = 22, 12, 20


def _lines(label, width=WRAP):
    return textwrap.wrap(label, width, break_long_words=True) or [label]


def _ranks(diagram):
    """Rang de chaque élément (colonne en LR, ligne en TD) : plus long chemin depuis les points de
    départ."""
    order = list(diagram.nodes)
    successors = {n: [] for n in order}
    for edge in diagram.edges:
        successors[edge.source].append(edge.target)
    back, state = set(), {}

    def visit(node):  # liens qui referment une boucle : ignorés pour le classement
        state[node] = "open"
        for target in successors[node]:
            if state.get(target) == "open":
                back.add((node, target))
            elif target not in state:
                visit(target)
        state[node] = "done"

    for node in order:
        if node not in state:
            visit(node)
    rank = {n: 0 for n in order}
    for _ in order:
        changed = False
        for edge in diagram.edges:
            if (edge.source, edge.target) not in back and edge.source != edge.target \
                    and rank[edge.target] < rank[edge.source] + 1:
                rank[edge.target], changed = rank[edge.source] + 1, True
        if not changed:
            break
    targets = {n: [t for t in successors[n] if (n, t) not in back and t != n] for n in order}
    for (
        node
    ) in order:  # point de départ (attaquant, navigateur…) : placé juste avant sa première cible
        if targets[node] and not any(node in targets[other] for other in order):
            rank[node] = max(rank[node], min(rank[t] for t in targets[node]) - 1)
    return rank


@dataclass
class Box:
    node: Node
    lines: list
    w: float
    h: float
    x: float = 0   # centre
    y: float = 0


def layout(diagram):
    """Positions des éléments : (boîtes, largeur, hauteur)."""
    rank = _ranks(diagram)
    boxes = {}
    for node in diagram.nodes.values():
        lines = _lines(node.label)
        boxes[node.id] = Box(node, lines, max(84, max(len(l) for l in lines) * CHAR_W + 2 * PAD_X),
                             len(lines) * LINE_H + 2 * PAD_Y)
    layers = [[] for _ in range(max(rank.values()) + 1)]
    for node_id in diagram.nodes:
        layers[rank[node_id]].append(node_id)
    order = {n: i for layer in layers for i, n in enumerate(layer)}
    predecessors = {
        n: [e.source for e in diagram.edges if e.target == n and rank[e.source] < rank[n]]
        for n in diagram.nodes
    }
    for layer in layers[1:]:  # un passage « barycentre » : moins de liens qui se croisent
        layer.sort(key=lambda n: (sum(order[p] for p in predecessors[n]) / len(predecessors[n])
                                  if predecessors[n] else order[n]))
        order.update({n: i for i, n in enumerate(layer)})

    horizontal = diagram.direction == "LR"
    labels = {}
    for edge in diagram.edges:  # place réservée aux libellés des liens entre deux rangs
        if edge.label:
            gap = min(rank[edge.source], rank[edge.target])
            lines = _lines(edge.label, 18)
            need = (
                (max(len(l) for l in lines) * CHAR_W + 48) if horizontal else (len(lines) * 15 + 44)
            )
            labels[gap] = max(labels.get(gap, 0), need)
    span = lambda b: b.w if horizontal else b.h        # noqa: E731 (taille dans le sens du flux)
    across = lambda b: b.h if horizontal else b.w      # noqa: E731
    thickness = [max(span(boxes[n]) for n in layer) if layer else 0 for layer in layers]
    lengths = [
        sum(across(boxes[n]) for n in layer) + NODE_GAP * (len(layer) - 1) for layer in layers
    ]
    total_across = max(lengths)
    position = MARGIN
    for index, layer in enumerate(layers):
        offset = MARGIN + (total_across - lengths[index]) / 2
        for node_id in layer:
            box = boxes[node_id]
            main, cross = position + thickness[index] / 2, offset + across(box) / 2
            box.x, box.y = (main, cross) if horizontal else (cross, main)
            offset += across(box) + NODE_GAP
        position += thickness[index] + max(56 if horizontal else 44, labels.get(index, 0))
    flow = position - max(56 if horizontal else 44, labels.get(len(layers) - 1, 0)) + MARGIN
    size = (flow, total_across + 2 * MARGIN) if horizontal else (total_across + 2 * MARGIN, flow)
    return boxes, size[0], size[1]


def _border(box, dx, dy):
    """Point du bord de la boîte dans la direction (dx, dy) depuis son centre."""
    if dx == dy == 0:
        return box.x, box.y
    t = min(
        (box.w / 2 + 3) / abs(dx) if dx else float("inf"),
        (box.h / 2 + 3) / abs(dy) if dy else float("inf"),
    )
    return box.x + dx * t, box.y + dy * t


def _shape(box):
    x, y, w, h = box.x - box.w / 2, box.y - box.h / 2, box.w, box.h
    kind = box.node.shape
    if kind == "db":
        return (
            f'<path d="M{x:.1f},{y + 6:.1f} a{w / 2:.1f},6 0 0,0 {w:.1f},0 a{w / 2:.1f},6 0 0,0'
            f' {-w:.1f},0 v{h - 12:.1f} a{w / 2:.1f},6 0 0,0 {w:.1f},0 v{-(h - 12):.1f}"/>'
        )
    if kind == "check":
        d = min(14, h / 2)
        points = [
            (x + d, y),
            (x + w - d, y),
            (x + w, y + h / 2),
            (x + w - d, y + h),
            (x + d, y + h),
            (x, y + h / 2),
        ]
        return '<polygon points="' + " ".join(f"{px:.1f},{py:.1f}" for px, py in points) + '"/>'
    radius = h / 2 if kind == "round" else 6
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius:.1f}"/>'


def _text(lines, x, y, line_h=LINE_H):
    top = y - (len(lines) - 1) * line_h / 2
    spans = "".join(f'<tspan x="{x:.1f}" y="{top + i * line_h:.1f}">{escape(line)}</tspan>'
                    for i, line in enumerate(lines))
    return f'<text text-anchor="middle" dominant-baseline="central">{spans}</text>'


def describe(diagram):
    """Version texte du schéma (lecteurs d'écran, info-bulle)."""
    names = {n.id: n.label for n in diagram.nodes.values()}
    parts = [
        f"{names[e.source]} → {names[e.target]}" + (f" ({e.label})" if e.label else "")
        for e in diagram.edges
    ]
    linked = {e.source for e in diagram.edges} | {e.target for e in diagram.edges}
    parts += [n.label for n in diagram.nodes.values() if n.id not in linked]
    return " ; ".join(parts)


def render(text):
    """SVG du schéma (chaîne sûre : libellés échappés) ; chaîne vide si le schéma est vide ou
    invalide."""
    try:
        diagram = parse(text)
    except DiagramError:
        return ""
    boxes, width, height = layout(diagram)
    uid = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:8]
    pairs = {(e.source, e.target) for e in diagram.edges}
    edges, labels = [], []
    for edge in diagram.edges:
        a, b = boxes[edge.source], boxes[edge.target]
        dx, dy = b.x - a.x, b.y - a.y
        norm = (dx * dx + dy * dy) ** 0.5 or 1
        shift = 5 if (edge.target, edge.source) in pairs and edge.source != edge.target else 0
        ox, oy = (
            -dy / norm * shift,
            dx / norm * shift,
        )  # deux liens opposés : décalés l'un de l'autre
        x1, y1 = _border(a, dx, dy)
        x2, y2 = _border(b, -dx, -dy)
        marker = "" if edge.style == "line" else f' marker-end="url(#fleche-{uid})"'
        edges.append(
            f'<line class="diagram-edge {edge.style}" x1="{x1 + ox:.1f}" y1="{y1 + oy:.1f}" '
            f'x2="{x2 + ox:.1f}" y2="{y2 + oy:.1f}"{marker}/>'
        )
        if edge.label:
            lines = _lines(edge.label, 18)
            mx, my = (x1 + x2) / 2 + ox * 2, (y1 + y2) / 2 + oy * 2
            w, h = max(len(l) for l in lines) * 6.6 + 10, len(lines) * 15 + 6
            labels.append(
                f'<g class="diagram-edge-label"><rect x="{mx - w / 2:.1f}" y="{my - h / 2:.1f}" '
                f'width="{w:.1f}" height="{h:.1f}" rx="4"/>{_text(lines, mx, my, 15)}</g>'
            )
    nodes = [f'<g class="{" ".join(filter(None, ["diagram-node", box.node.shape, box.node.cls]))}">'
             f'{_shape(box)}{_text(box.lines, box.x, box.y)}</g>' for box in boxes.values()]
    summary = escape("Schéma : " + describe(diagram))
    return (
        '<figure class="quiz-diagram"><svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{summary}" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}">'
        f'<title>{summary}</title>'
        f'<defs><marker id="fleche-{uid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"'
        ' class="diagram-arrow"/>'
        f'</marker></defs>{"".join(edges)}{"".join(nodes)}{"".join(labels)}</svg></figure>'
    )
