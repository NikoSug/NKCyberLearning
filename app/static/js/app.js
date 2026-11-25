// Fichier séparé : la politique CSP interdit les scripts en ligne.
// Les listes déroulantes marquées data-autosubmit envoient leur formulaire dès qu'on les change
// (le bouton « Enregistrer » reste disponible sans JavaScript).
document.addEventListener("change", (event) => {
  const field = event.target;
  if (field.matches("select[data-autosubmit]") && field.form) {
    field.form.requestSubmit();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-autosubmit-hide]").forEach((button) => { button.hidden = true; });
});

// Questionnaires : compteur des questions répondues.
document.addEventListener("change", (event) => {
  const form = event.target.closest("form[data-quiz]");
  if (!form) return;
  const answered = new Set([...form.querySelectorAll("input[type=radio]:checked")].map((input) => input.name)).size;
  form.querySelector("[data-quiz-progress]").value = answered;
  form.querySelector("[data-quiz-count]").textContent = answered;
});

// Liens de retour : revenir à la page précédente du site quand on en vient (liste filtrée, recherche,
// relecture…), sinon suivre le lien prévu. Une page rechargée après un enregistrement garde le lien prévu.
document.querySelectorAll(".back-link a").forEach((link) => {
  let previous = null;
  try { previous = document.referrer ? new URL(document.referrer) : null; } catch (e) { previous = null; }
  if (!previous || previous.origin !== location.origin || previous.pathname === location.pathname
      || previous.pathname.startsWith("/compte/") || history.length < 2) return;
  link.textContent = "← Retour";
  link.title = "Revenir à la page précédente";
  link.addEventListener("click", (event) => { event.preventDefault(); history.back(); });
});

// Formulaires (ou boutons d'envoi) marqués data-confirm : confirmation avant une action irréversible (achat…).
document.addEventListener("submit", (event) => {
  const message = (event.submitter && event.submitter.dataset.confirm) || (event.target.dataset && event.target.dataset.confirm);
  if (message && !window.confirm(message)) event.preventDefault();
});

// Menus déroulants (<details data-menu>) : fermeture au clic à l'extérieur et avec Échap.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details[data-menu][open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll("details[data-menu][open]").forEach((menu) => {
    menu.open = false;
    menu.querySelector("summary").focus();
  });
});

// Pages en attente (version audio en préparation) : rechargement automatique.
document.addEventListener("DOMContentLoaded", () => {
  const waiting = document.querySelector("[data-auto-refresh]");
  if (!waiting) return;
  const delay = Number(waiting.dataset.autoRefresh || 20) * 1000;
  // Pas de rechargement pendant qu'on se sert de la page (menu ouvert, champ en cours de saisie).
  const busy = () => document.querySelector("details[data-menu][open]")
    || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement && document.activeElement.tagName);
  const tick = () => (busy() ? setTimeout(tick, 5000) : window.location.reload());
  setTimeout(tick, delay);
});

// Lecteur audio des guides : chapitres enchaînés, reprise, vitesse, sauvegarde de la progression.
document.addEventListener("DOMContentLoaded", () => {
  const player = document.querySelector("[data-player]");
  if (!player) return;
  const audio = player.querySelector("[data-audio]");
  const chapters = [...document.querySelectorAll("[data-chapter]")];
  const playButton = player.querySelector("[data-play]");
  const seek = player.querySelector("[data-seek]");
  const timeLabel = player.querySelector("[data-time]");
  const durationLabel = player.querySelector("[data-duration]");
  const nowTitle = player.querySelector("[data-now-title]");
  const status = player.querySelector("[data-status]");
  const speed = player.querySelector("[data-speed]");
  const csrf = player.querySelector("input[name=csrfmiddlewaretoken]").value;
  let index = Math.max(0, chapters.findIndex((c) => c.dataset.position === player.dataset.startSection));
  let lastSaved = 0;

  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  function save(ended = false) {
    const body = new URLSearchParams({ section: chapters[index].dataset.position,
      seconds: ended ? 0 : audio.currentTime.toFixed(1), ended: ended ? "1" : "0" });
    return fetch(player.dataset.progressUrl, { method: "POST", body, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf } }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }

  function load(i, startAt = 0, autoplay = false) {
    index = i;
    const chapter = chapters[i];
    chapters.forEach((c) => c.classList.toggle("current", c === chapter));
    nowTitle.textContent = chapter.dataset.title;
    audio.src = chapter.dataset.src;
    audio.playbackRate = Number(speed.value);
    audio.addEventListener("loadedmetadata", () => {
      durationLabel.textContent = clock(audio.duration);
      timeLabel.textContent = clock(startAt && startAt < audio.duration ? startAt : 0);
      if (startAt && startAt < audio.duration) audio.currentTime = startAt;
      if (autoplay) audio.play().catch(() => {});
    }, { once: true });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({ title: chapter.dataset.title, artist: player.dataset.title,
        album: "CyberLearning" });
    }
  }

  try { const saved = localStorage.getItem("audio-speed"); if (saved) speed.value = saved; } catch (e) { /* stockage indisponible */ }
  speed.addEventListener("change", () => {
    audio.playbackRate = Number(speed.value);
    try { localStorage.setItem("audio-speed", speed.value); } catch (e) { /* stockage indisponible */ }
  });

  playButton.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => { playButton.textContent = "⏸"; playButton.setAttribute("aria-label", "Pause"); });
  audio.addEventListener("pause", () => {
    playButton.textContent = "▶"; playButton.setAttribute("aria-label", "Lecture");
    save();
  });
  audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    seek.value = (100 * audio.currentTime) / audio.duration;
    timeLabel.textContent = clock(audio.currentTime);
    durationLabel.textContent = clock(audio.duration);
    if (Date.now() - lastSaved > 15000) { lastSaved = Date.now(); save(); }
  });
  seek.addEventListener("input", () => { if (audio.duration) audio.currentTime = (seek.value / 100) * audio.duration; });
  player.querySelector("[data-back]").addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
  player.querySelector("[data-forward]").addEventListener("click", () => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 15); });
  player.querySelector("[data-prev]").addEventListener("click", () => { if (index > 0) load(index - 1, 0, true); });
  player.querySelector("[data-next]").addEventListener("click", () => { if (index < chapters.length - 1) load(index + 1, 0, true); });
  chapters.forEach((chapter, i) => chapter.querySelector("[data-chapter-play]").addEventListener("click", () => load(i, 0, true)));

  audio.addEventListener("ended", () => {
    const chapter = chapters[index];
    chapter.classList.add("listened");
    chapter.querySelector(".chapter-mark").textContent = "✓";
    save(true).then((result) => {
      if (result && result.message) {  // podcasts : message préparé par le serveur
        status.textContent = result.message;
        status.classList.toggle("done", Boolean(result.completed));
      } else if (result && result.completed) {
        status.textContent = `Écoute terminée : le guide est marqué comme lu (+${result.xp} XP). Son test de connaissances est ouvert.`;
        status.classList.add("done");
      } else if (result) {
        status.textContent = `${result.done} chapitre(s) écouté(s) sur ${result.total}.`;
      }
    });
    if (index < chapters.length - 1) load(index + 1, 0, true);
  });

  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", () => audio.play());
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("previoustrack", () => { if (index > 0) load(index - 1, 0, true); });
    navigator.mediaSession.setActionHandler("nexttrack", () => { if (index < chapters.length - 1) load(index + 1, 0, true); });
  }
  window.addEventListener("pagehide", () => { if (!audio.paused) save(); });

  load(index, Number(player.dataset.startSeconds) || 0, false);
});

// Assistant de lecture : questions sur le guide lu ou écouté, réponse de l'IA locale affichée au fil de l'eau.
document.addEventListener("DOMContentLoaded", () => {
  const panel = document.querySelector("[data-assistant]");
  if (!panel) return;
  const toggle = document.querySelector("[data-assistant-open]");
  const log = panel.querySelector("[data-assistant-log]");
  const form = panel.querySelector("[data-assistant-form]");
  const question = form.elements.question;
  const passage = form.elements.passage;
  const passageBox = panel.querySelector("[data-assistant-passage]");
  const send = form.querySelector("button[type=submit]");
  const suggestions = panel.querySelector("[data-assistant-suggestions]");
  const csrf = panel.querySelector("input[name=csrfmiddlewaretoken]").value;
  // Conversation gardée pendant la session de l'onglet (une par guide, une pour les questions générales) :
  // elle survit au changement de page.
  const storageKey = `assistant:${panel.dataset.url}`;
  const store = {
    get(key, fallback) { try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; } },
    set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* stockage indisponible */ } },
  };
  const history = store.get(storageKey, []).slice(-20);
  let busy = false;
  // Défilement automatique seulement si l'on est déjà en bas : on peut remonter lire pendant la réponse.
  const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const follow = (wasAtBottom) => { if (wasAtBottom) log.scrollTop = log.scrollHeight; };

  function open(show, focus = true) {
    store.set("assistant:open", show);
    panel.hidden = !show;
    toggle.hidden = show;
    toggle.setAttribute("aria-expanded", String(show));
    if (!show) { toggle.focus(); return; }
    // Texte sélectionné dans la page (transcription d'un chapitre…) : repris comme passage concerné.
    const selected = String(window.getSelection() || "").trim();
    if (selected && !panel.contains(window.getSelection().anchorNode)) {
      passage.value = selected.slice(0, 1500);
      passageBox.open = true;
    }
    log.scrollTop = log.scrollHeight;
    if (focus) question.focus();
  }
  toggle.addEventListener("mousedown", (event) => event.preventDefault());  // garde la sélection de texte
  toggle.addEventListener("click", () => open(true));
  panel.querySelector("[data-assistant-close]").addEventListener("click", () => open(false));
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") open(false); });

  // Mise en forme sûre (textContent) : paragraphes, listes à tirets et **gras**.
  function render(target, text) {
    target.replaceChildren();
    let list = null;
    text.split("\n").forEach((line) => {
      if (!line.trim()) { list = null; return; }
      const bullet = line.match(/^(\s*)[-*•]\s+(.*)$/);
      const element = document.createElement(bullet ? "li" : "p");
      if (bullet) {
        if (!list) { list = document.createElement("ul"); target.append(list); }
        if (bullet[1].length >= 2) element.className = "sub";  // sous-liste indentée
        list.append(element);
      } else {
        list = null;
        target.append(element);
      }
      (bullet ? bullet[2] : line.replace(/^#+\s*/, "")).split(/\*\*(.+?)\*\*/).forEach((part, i) => {
        if (i % 2) { const strong = document.createElement("strong"); strong.textContent = part; element.append(strong); }
        else element.append(part);
      });
    });
  }

  function bubble(role, text) {
    const element = document.createElement("div");
    element.className = `assistant-msg ${role}`;
    element.textContent = text;
    log.append(element);
    log.scrollTop = log.scrollHeight;
    return element;
  }

  async function ask(text) {
    if (busy || text.trim().length < 3) return;
    busy = true;
    send.disabled = true;
    suggestions.hidden = true;
    const quoted = passage.value.trim();
    const chapter = document.querySelector("[data-chapter].current");
    bubble("user", quoted ? `« ${quoted.length > 160 ? quoted.slice(0, 160) + "…" : quoted} »\n${text}` : text);
    const answer = bubble("bot pending", "Lecture du guide et réflexion…");
    question.value = "";
    let full = "";
    try {
      const response = await fetch(panel.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ question: text, passage: quoted, chapter: chapter ? chapter.dataset.title : "",
          history: history.slice(-4) }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "L'assistant n'a pas pu répondre. Réessayez.");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        full += decoder.decode(value, { stream: true });
        const wasAtBottom = atBottom();
        answer.classList.remove("pending");
        render(answer, full);
        follow(wasAtBottom);
      }
      if (!full.trim()) throw new Error("Réponse vide : reformulez votre question.");
      history.push({ role: "user", content: text }, { role: "assistant", content: full });
      store.set(storageKey, history.slice(-20));
      passage.value = "";
      passageBox.open = false;
    } catch (error) {
      answer.classList.remove("pending");
      answer.classList.add("error");
      answer.textContent = error.message;
    } finally {
      busy = false;
      send.disabled = false;
      question.focus();
    }
  }

  history.forEach((message) => {
    if (message.role === "user") bubble("user", message.content);
    else render(bubble("bot", ""), message.content);
  });
  if (history.length) suggestions.hidden = true;
  if (store.get("assistant:open", false)) open(true, false);  // resté ouvert sur la page précédente

  form.addEventListener("submit", (event) => { event.preventDefault(); ask(question.value); });
  question.addEventListener("keydown", (event) => {  // Entrée envoie, Maj + Entrée va à la ligne
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(question.value); }
  });
  panel.querySelectorAll("[data-assistant-suggest]").forEach((button) => {
    button.addEventListener("click", () => ask(button.textContent));
  });
});

// Questionnaires : une question à la fois. Le formulaire reste le même (envoi de toutes les
// réponses à la fin) ; sans JavaScript, toutes les questions s'affichent.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-quiz]").forEach((form) => {
    const steps = [...form.querySelectorAll("[data-quiz-step]")];
    if (steps.length < 2) return;
    const nav = form.querySelector("[data-quiz-nav]");
    const prev = nav.querySelector("[data-quiz-prev]");
    const next = nav.querySelector("[data-quiz-next]");
    const submit = form.querySelector("[data-quiz-submit]");
    const position = nav.querySelector("[data-quiz-position]");
    const answered = (step) => Boolean(step.querySelector("input:checked"));
    let index = Math.max(0, steps.findIndex((step) => !answered(step)));  // retour arrière du navigateur

    nav.hidden = false;
    nav.append(submit);  // le bouton d'envoi rejoint la navigation, à la dernière question
    form.querySelector(".quiz-submit").hidden = true;
    form.classList.add("stepper");

    function refresh() {
      const last = index === steps.length - 1;
      prev.disabled = index === 0;
      next.hidden = last;
      next.disabled = !answered(steps[index]);
      submit.hidden = !last;
      submit.disabled = !steps.every(answered);
      position.textContent = `Question ${index + 1} sur ${steps.length}`;
    }
    function show(i, focus = true) {
      index = i;
      steps.forEach((step, n) => { step.hidden = n !== i; });
      refresh();
      if (focus) (steps[i].querySelector("input:checked") || steps[i].querySelector("input")).focus();
    }
    form.addEventListener("change", refresh);
    prev.addEventListener("click", () => { if (index > 0) show(index - 1); });
    next.addEventListener("click", () => { if (answered(steps[index]) && index < steps.length - 1) show(index + 1); });
    form.addEventListener("keydown", (event) => {  // Entrée : question suivante, puis envoi
      if (event.key !== "Enter" || event.target.type !== "radio") return;
      event.preventDefault();
      if (!next.hidden && !next.disabled) next.click();
      else if (!submit.hidden && !submit.disabled) form.requestSubmit(submit);
    });
    show(index, false);
  });
});

// Relecture des QCM : raccourcis clavier (A accepter, R rejeter, flèches pour naviguer) hors des champs.
document.addEventListener("keydown", (event) => {
  if (!document.querySelector("[data-review-question]") || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest("input, textarea, select, [contenteditable]")) return;
  const target = document.querySelector(`[data-shortcut="${event.key.toLowerCase()}"]`);
  if (!target || target.disabled) return;
  event.preventDefault();
  target.click();
});

// Quiz d'une leçon : une question à la fois, correction immédiate (la première réponse est retenue
// côté serveur), puis envoi pour le résultat. Sans JavaScript : toutes les questions, envoi groupé.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("form[data-lesson-quiz]");
  if (!form) return;
  const steps = [...form.querySelectorAll("[data-lesson-step]")];
  const nav = form.querySelector("[data-lesson-nav]");
  const next = nav.querySelector("[data-lesson-next]");
  const submitBox = form.querySelector("[data-lesson-submit]");
  const progress = form.querySelector("[data-quiz-progress]");
  const position = form.querySelector("[data-lesson-position]");
  const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  let index = 0;
  let answered = 0;

  nav.hidden = false;
  submitBox.hidden = true;
  nav.append(submitBox.querySelector("button"));
  const submit = nav.querySelector("button[type=submit]");
  submit.hidden = true;

  function show(i) {
    index = i;
    steps.forEach((step, n) => { step.hidden = n !== i; });
    position.textContent = `Question ${i + 1} sur ${steps.length}`;
    next.hidden = i === steps.length - 1;
    next.disabled = true;
    const first = steps[i].querySelector("input");
    if (first && i > 0) first.focus();
  }

  steps.forEach((step, i) => {
    step.addEventListener("change", async (event) => {
      const input = event.target;
      if (step.dataset.done) return;
      step.dataset.done = "1";
      step.querySelectorAll("input").forEach((other) => { if (other !== input) other.disabled = true; });
      const feedback = step.querySelector("[data-feedback]");
      try {
        const response = await fetch(form.dataset.answerUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
          body: JSON.stringify({ question: step.dataset.question, choice: Number(input.value) }),
        });
        if (!response.ok) throw new Error();
        const result = await response.json();
        const labels = [...step.querySelectorAll(".choice")];
        labels[result.answer].classList.add("is-answer");
        if (!result.correct) labels[result.given].classList.add("is-given");
        step.classList.add(result.correct ? "correct" : "wrong");
        feedback.textContent = (result.correct ? "✓ Bonne réponse. " : "✕ Pas tout à fait. ") + (result.explanation || "");
        feedback.className = `feedback ${result.correct ? "ok" : "ko"}`;
      } catch (error) {
        feedback.textContent = "Réponse enregistrée (correction indisponible pour le moment).";
        feedback.className = "feedback";
      }
      feedback.hidden = false;
      answered += 1;
      progress.value = answered;
      if (i === steps.length - 1) { submit.hidden = false; submit.focus(); } else { next.disabled = false; next.focus(); }
    });
  });
  next.addEventListener("click", () => { if (index < steps.length - 1) show(index + 1); });
  show(0);
});

// Lexique flottant : ouverture (ferme l'assistant), recherche, et clic sur un terme souligné d'une leçon.
document.addEventListener("DOMContentLoaded", () => {
  const panel = document.querySelector("[data-glossary]");
  if (!panel) return;
  const toggle = document.querySelector("[data-glossary-open]");
  const search = panel.querySelector("[data-glossary-search]");
  const items = [...panel.querySelectorAll("[data-glossary-item]")];
  const empty = panel.querySelector("[data-glossary-empty]");
  const plain = (text) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();

  function filter() {
    const query = plain(search.value);
    let shown = 0;
    items.forEach((item) => {
      const match = !query || plain(item.dataset.search).includes(query) || plain(item.textContent).includes(query);
      item.hidden = !match;
      shown += match;
    });
    empty.hidden = shown > 0;
  }
  function open(show, term = "") {
    panel.hidden = !show;
    toggle.hidden = show;
    toggle.setAttribute("aria-expanded", String(show));
    if (!show) { toggle.focus(); return; }
    const assistant = document.querySelector("[data-assistant]");
    if (assistant && !assistant.hidden) assistant.querySelector("[data-assistant-close]").click();
    search.value = term;
    filter();
    const target = term && items.find((item) => item.dataset.key === term);
    if (target) { target.scrollIntoView({ block: "nearest" }); target.classList.add("flash-term"); }
    else search.focus();
  }
  toggle.addEventListener("click", () => open(true));
  panel.querySelector("[data-glossary-close]").addEventListener("click", () => open(false));
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") open(false); });
  search.addEventListener("input", filter);
  document.addEventListener("click", (event) => {
    const term = event.target.closest("abbr[data-term]");
    if (!term) return;
    event.preventDefault();
    open(true, term.dataset.term);
  });
  // L'assistant s'ouvre : on referme le lexique (un seul panneau à la fois).
  const assistantToggle = document.querySelector("[data-assistant-open]");
  if (assistantToggle) assistantToggle.addEventListener("click", () => { if (!panel.hidden) open(false); });
});

// Atelier de rédaction : aide de l'IA sous un champ de texte (brouillon, amélioration, vérification).
// La réponse arrive au fil de l'eau dans un aperçu ; rien ne change dans le texte avant « Remplacer » ou « Ajouter ».
document.querySelectorAll("[data-ai-tools]").forEach((tools) => {
  const form = tools.closest("form");
  const field = form.elements[tools.dataset.target];
  const result = tools.querySelector("[data-ai-result]");
  const output = tools.querySelector("[data-ai-output]");
  const title = tools.querySelector("[data-ai-title]");
  const buttons = [...tools.querySelectorAll("[data-ai-action]")];
  const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  let range = null;  // sélection traitée (début, fin), ou null pour tout le texte
  let action = "";

  const value = (name) => (form.elements[name] ? form.elements[name].value : "");

  async function run(button) {
    action = button.dataset.aiAction;
    const start = field.selectionStart;
    const end = field.selectionEnd;
    range = end > start ? [start, end] : null;
    const text = range ? field.value.slice(start, end) : field.value;
    buttons.forEach((b) => { b.disabled = true; });
    result.hidden = false;
    result.classList.remove("error");
    title.textContent = `${button.textContent.trim()}${range ? " (sélection)" : ""} : l'IA écrit…`;
    output.textContent = "";
    let full = "";
    try {
      const response = await fetch(tools.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ action, kind: tools.dataset.kind, text, title: value("title"),
          objective: value("objective") || value("summary"), brief: tools.dataset.brief, category: tools.dataset.category,
          source: tools.dataset.source || "" }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "L'IA n'a pas pu répondre. Réessayez.");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value: chunk } = await reader.read();
        if (done) break;
        full += decoder.decode(chunk, { stream: true });
        output.textContent = full;
      }
      if (!full.trim()) throw new Error("Réponse vide : réessayez.");
      title.textContent = action === "verifier" ? "Vérification par l'IA (à lire, rien n'est modifié)" : "Proposition de l'IA";
      result.querySelectorAll("[data-ai-apply=replace], [data-ai-apply=append]")
        .forEach((b) => { b.hidden = action === "verifier"; });
    } catch (error) {
      result.classList.add("error");
      title.textContent = "Aide indisponible";
      output.textContent = error.message;
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  buttons.forEach((button) => button.addEventListener("click", () => run(button)));
  result.querySelectorAll("[data-ai-apply]").forEach((button) => button.addEventListener("click", () => {
    const text = output.textContent.trim();
    if (button.dataset.aiApply === "replace") {
      field.value = range ? field.value.slice(0, range[0]) + text + field.value.slice(range[1]) : text;
    } else if (button.dataset.aiApply === "append") {
      field.value = `${field.value.trimEnd()}${field.value.trim() ? "\n\n" : ""}${text}`;
    }
    result.hidden = true;
    if (button.dataset.aiApply !== "close") field.focus();
  }));
});

// Boutons longs (IA côté serveur) : message d'attente et pas de double envoi.
document.addEventListener("submit", (event) => {
  const button = event.submitter;
  if (!button || !button.dataset.busy || event.defaultPrevented) return;
  setTimeout(() => { button.disabled = true; button.textContent = button.dataset.busy; }, 0);
});
