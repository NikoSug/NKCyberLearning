# syntax=docker/dockerfile:1
# Service de synthèse vocale Kyutai TTS 1.6B (anglais/français), sur carte graphique NVIDIA.
# Modèle : licence CC BY 4.0 (Kyutai, mention affichée dans le lecteur) ; voix « developpeuse-3 » et « fabieng-enhanced-v2 » :
# enregistrements de Kyutai, CC0 ; bibliothèque moshi : MIT.
# Les poids (environ 4 Go) sont téléchargés au premier lancement dans le volume tts_models,
# à des révisions figées (server.py) ; l'application ne lui parle qu'en HTTP.

FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/models TORCHDYNAMO_DISABLE=1 NO_TORCH_COMPILE=1 HF_HUB_DISABLE_TELEMETRY=1

RUN pip install "moshi==0.2.13" "sphn==0.2.1"

RUN groupadd --system --gid 10003 tts \
 && useradd --system --uid 10003 --gid tts --home-dir /models --shell /usr/sbin/nologin tts \
 && mkdir -p /models && chown tts:tts /models
COPY --chown=tts:tts server.py /app/server.py
USER tts
WORKDIR /app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/info', timeout=4).status == 200 else 1)"

CMD ["python", "server.py"]
