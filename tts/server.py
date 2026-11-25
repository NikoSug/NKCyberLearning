"""
Service de synthèse vocale Kyutai TTS (1,6 milliard de paramètres, anglais et français), GPU.

Interface HTTP utilisée par le « narrator » :
  GET  /info        → {"voice": {"name": …}, "loaded": bool}
  POST /synthesize  → {"text": "…"} ; réponse : WAV 16 bits mono
                      ou {"turns": [{"speaker": 0|1, "text": "…"}, …]} : dialogue à deux voix
                      (podcasts)
  POST /unload      → libère la mémoire de la carte graphique (le modèle sera rechargé au besoin)

Une erreur CUDA « inconnue » (pilote réinitialisé, carte saturée…) rend la carte inutilisable pour
ce
processus : le service répond alors 503 puis s'arrête, et Docker le redémarre (restart:
unless-stopped).

Le modèle n'est chargé qu'à la première demande (environ 4 Go de mémoire graphique) et peut être
déchargé entre deux guides, pour laisser la place à l'IA locale (Ollama) sur une carte de 8 Go.
"""

import io
import json
import logging
import os
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import hf_hub_download
from moshi.models.loaders import CheckpointInfo
from moshi.models.tts import TTSModel

MODEL_REPO = os.environ.get("TTS_MODEL_REPO", "kyutai/tts-1.6b-en_fr")
MODEL_REVISION = os.environ.get("TTS_MODEL_REVISION", "f65439609986c392cb12df63938abcc550c3fb15")
VOICE_REPO = os.environ.get("TTS_VOICE_REPO", "kyutai/tts-voices")
VOICE_REVISION = os.environ.get("TTS_VOICE_REVISION", "323332d33f997de8394f24a193e1a76df720e01a")
VOICE = os.environ.get("TTS_VOICE", "unmute-prod-website/developpeuse-3.wav")
VOICE_B = os.environ.get(
    "TTS_VOICE_B", "unmute-prod-website/fabieng-enhanced-v2.wav"
)  # seconde voix (CC0)
VOICE_NAME = os.environ.get("TTS_VOICE_NAME", "Kyutai TTS — voix « développeuse »")
MAX_TEXT = 1500
MAX_TURNS = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts-kyutai")
lock = threading.Lock()  # une synthèse à la fois sur la carte graphique
state = {"model": None, "condition": None, "dialogue": None, "broken": False}
RESTART_DELAY = 1.0  # secondes : le temps d'envoyer la réponse avant de quitter
STUCK_AFTER = 240    # secondes : un passage court se lit en ~15 s ; au-delà, la carte est bloquée
busy = {"since": None}  # début de la synthèse en cours (surveillée par le chien de garde)


def load():
    if state["model"] is None:
        log.info("chargement du modèle %s", MODEL_REPO)
        info = CheckpointInfo.from_hf_repo(MODEL_REPO, revision=MODEL_REVISION)
        model = TTSModel.from_checkpoint_info(info, n_q=32, temp=0.6, device=torch.device("cuda"))
        voice, other = (
            Path(hf_hub_download(VOICE_REPO, name + model.voice_suffix, revision=VOICE_REVISION))
            for name in (VOICE, VOICE_B)
        )
        state["condition"] = model.make_condition_attributes([voice], cfg_coef=2.0)
        state["dialogue"] = model.make_condition_attributes([voice, other], cfg_coef=2.0)
        state["model"] = model
    return state["model"]


def unload():
    if state["model"] is not None:
        state["model"] = state["condition"] = state["dialogue"] = None
        torch.cuda.empty_cache()
        log.info("modèle déchargé : mémoire graphique libérée")


def broken(exc):
    """Erreur CUDA hors mémoire : le contexte de la carte est perdu pour ce processus."""
    return isinstance(exc, RuntimeError) and "CUDA error" in str(exc)


def restart_soon(exc):
    state["broken"] = True
    log.error(
        "carte graphique inutilisable (%s) : redémarrage du service", str(exc).splitlines()[0]
    )
    threading.Timer(RESTART_DELAY, os._exit, args=[1]).start()


def watchdog():
    """Synthèse bloquée (pilote de la carte figé) : le service redémarre au lieu de rester muet."""
    while True:
        time.sleep(10)
        since = busy["since"]
        if since is not None and time.monotonic() - since > STUCK_AFTER and not state["broken"]:
            restart_soon(RuntimeError(f"synthèse bloquée depuis plus de {STUCK_AFTER} s"))


def synthesize(text=None, turns=None):
    """Un texte (première voix) ou des répliques alternées entre les deux voix (0 : première, 1 :
    seconde)."""
    model = load()
    pcms = []

    def on_frame(frame):
        if (frame != -1).all():
            pcm = model.mimi.decode(frame[:, 1:, :]).cpu().numpy()
            pcms.append(np.clip(pcm[0, 0], -1, 1))

    if turns:
        # Le modèle alterne les voix à chaque élément du script, en commençant par la première :
        # un tour vide fait commencer la seconde ; deux répliques de suite d'une même voix sont
        # réunies.
        script, speaker = [], 0
        for turn in turns:
            if turn["speaker"] == speaker and script:
                script[-1] += " " + turn["text"]
                continue
            if not script and turn["speaker"] == 1:
                script.append("")
            script.append(turn["text"])
            speaker = turn["speaker"]
        entries, condition = model.prepare_script(script, padding_between=1), state["dialogue"]
    else:
        entries, condition = model.prepare_script([text], padding_between=1), state["condition"]
    with model.mimi.streaming(1):
        model.generate([entries], [condition], on_frame=on_frame)
    audio = np.concatenate(pcms) if pcms else np.zeros(0, dtype=np.float32)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(model.mimi.sample_rate)
        wav.writeframes((audio * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data).encode("utf-8"))

    def do_GET(self):  # noqa: N802 (API de http.server)
        if self.path == "/info":
            if state["broken"]:
                return self._json(
                    503, {"error": "carte graphique en erreur : redémarrage du service"}
                )
            self._json(200, {"voice": {"name": VOICE_NAME}, "loaded": state["model"] is not None})
        else:
            self._json(404, {"error": "inconnu"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if state["broken"]:
            return self._json(503, {"error": "carte graphique en erreur : redémarrage du service"})
        if self.path == "/unload":
            with lock:
                try:
                    unload()
                except RuntimeError as exc:
                    if not broken(exc):
                        raise
                    restart_soon(exc)
            return self._json(200, {"loaded": False})
        if self.path != "/synthesize":
            return self._json(404, {"error": "inconnu"})
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            text = (data.get("text") or "").strip()
            turns = [
                {"speaker": int(t["speaker"]), "text": str(t["text"]).strip()}
                for t in data.get("turns") or []
            ]
        except (ValueError, TypeError, KeyError, AttributeError):
            return self._json(400, {"error": "JSON invalide"})
        if turns:
            if (
                len(turns) > MAX_TURNS
                or any(t["speaker"] not in (0, 1) or not t["text"] for t in turns)
                or sum(len(t["text"]) for t in turns) > MAX_TEXT
            ):
                return self._json(
                    400,
                    {"error": f"au plus {MAX_TURNS} répliques non vides, {MAX_TEXT} caractères"},
                )
        elif not text or len(text) > MAX_TEXT:
            return self._json(400, {"error": f"texte vide ou de plus de {MAX_TEXT} caractères"})
        try:
            with lock:
                busy["since"] = time.monotonic()
                try:
                    body = synthesize(text, turns)
                finally:
                    busy["since"] = None
        except torch.cuda.OutOfMemoryError:
            try:
                torch.cuda.empty_cache()
            except RuntimeError as exc:
                restart_soon(exc)
            return self._json(503, {"error": "mémoire graphique insuffisante"})
        except Exception as exc:  # erreur renvoyée au narrator plutôt qu'une connexion coupée
            if broken(exc):
                restart_soon(exc)
                return self._json(
                    503, {"error": "carte graphique en erreur : redémarrage du service"}
                )
            log.exception("synthèse impossible")
            return self._json(500, {"error": f"synthèse impossible : {exc}"})
        self._send(200, body, "audio/wav")

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    threading.Thread(target=watchdog, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
