#!/bin/bash
set -euo pipefail

# The retired app-builder once scanned a persistent `outputs/` directory and
# rewrote its legacy `jobs` rows here. The video product owns durable recovery
# in worker/main.py and the remote-execution ledger; startup must not mutate an
# unrelated table or depend on a directory that no longer exists.

exec gunicorn --workers 3 --worker-class gthread --threads 8 --timeout 600 --chdir backend "app:create_app()"
