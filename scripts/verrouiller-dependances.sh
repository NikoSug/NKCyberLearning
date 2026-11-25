#!/usr/bin/env bash
# Régénère requirements.txt et requirements-dev.txt (versions exactes + empreintes) depuis les fichiers .in, dans Docker.
# À lancer après toute modification de requirements.in ou requirements-dev.in ; ajouter --upgrade pour monter les versions.
set -eu
cd "$(dirname "$0")/.."
src=$(cygpath -m "$PWD" 2>/dev/null || pwd)
MSYS_NO_PATHCONV=1 docker run --rm -v "$src:/w" -w /w python:3.13.18-slim-bookworm sh -c '
  pip install -q --root-user-action=ignore uv==0.9.5
  for f in requirements requirements-dev; do
    uv pip compile -q --python-version 3.13 --generate-hashes "$@" \
      --custom-compile-command "bash scripts/verrouiller-dependances.sh" -o "$f.txt" "$f.in"
  done' sh "$@"
