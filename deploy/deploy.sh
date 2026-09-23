#!/usr/bin/env bash
# Redeploy df-docs.streamflows.org after a push to main.
# Mirrors the "Redeploy after content changes" steps in CLAUDE.md.
#
# Run this yourself (it needs your sudo password interactively):
#   ./deploy/deploy.sh
#
# Requires: sudo access on this host. Each step runs as the `fewsdocs`
# site user, per this box's multi-tenant file-ownership rule.

set -euo pipefail

REPO=/home/fewsdocs/repo
WHEEL=/home/geoskimoto/projects/streamflows-auth/dist/streamflows_auth-0.1.0-py3-none-any.whl
SERVICE=fewsdocs-chat

echo "==> Pulling latest main into $REPO"
sudo -u fewsdocs git -C "$REPO" pull

echo "==> Installing JS deps and building the site"
sudo -u fewsdocs bash -c "cd '$REPO' && npm ci && npm run build"

echo "==> Syncing the chat service's Python deps"
sudo -u fewsdocs bash -c "cd '$REPO/chat' && venv/bin/pip install -r requirements.txt"

# streamflows_auth is not on PyPI and is not in requirements.txt, so a
# rebuilt venv silently lacks it. Reinstalling here is a no-op when it's
# already present, and cheap insurance when the venv was just recreated.
echo "==> Ensuring streamflows_auth is installed in the chat venv"
sudo -u fewsdocs "$REPO/chat/venv/bin/pip" install "$WHEEL"

echo "==> Restarting $SERVICE (drops the prompt cache; first question after this is a full cache write)"
sudo systemctl restart "$SERVICE"

echo "==> Done. Service status:"
sudo systemctl status "$SERVICE" --no-pager -l | head -n 10
