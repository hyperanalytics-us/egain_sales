#!/usr/bin/env bash
#
# Deploy ESP to a cPanel host over SSH (GoDaddy, Exact Hosting, any cPanel box).
#
#   ./deploy/deploy.sh <ssh-user>@<ssh-host> [app-root] [url]
#
# Example:
#   ./deploy/deploy.sh myuser@hyperanalyticslabs.com esp https://hyperanalyticslabs.com/esp/
#
# Authentication is by SSH key only - this script never asks for or handles a
# password. Upload ~/.ssh/id_rsa.pub in cPanel > SSH Access > Manage SSH Keys
# and authorize it first.
#
# What it does: syncs the code (never data/, .env or .venv), installs Python
# dependencies into the cPanel virtualenv, hardens the app root if it sits inside
# a web-served directory, restarts Passenger, and smoke-tests the URL.
set -euo pipefail

TARGET="${1:-}"
APP_ROOT="${2:-esp}"
APP_URL="${3:-}"

if [ -z "$TARGET" ]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }

# ----------------------------------------------------------------- connect --
say "Checking SSH access to $TARGET"
if ! ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET" 'echo connected' >/dev/null 2>&1; then
  cat <<EOF
Could not connect with key authentication.

  1. cPanel > SSH Access > Manage SSH Keys - import and *authorize* your public key:
$(sed 's/^/       /' ~/.ssh/id_rsa.pub 2>/dev/null || echo '       (no ~/.ssh/id_rsa.pub found - run ssh-keygen)')
  2. Confirm the SSH host and cPanel username, then re-run.

EOF
  exit 1
fi

REMOTE_HOME=$(ssh "$TARGET" 'echo $HOME')
DEST="$REMOTE_HOME/$APP_ROOT"
say "Deploying to $TARGET:$DEST"

# --------------------------------------------------------- warn on docroot --
case "$DEST" in
  */public_html/*)
    warn "The application root is inside public_html."
    warn "Passenger only needs a small .htaccess there - the code itself is safer"
    warn "outside the web root. Continuing, and locking the directory down below."
    IN_DOCROOT=1 ;;
  *) IN_DOCROOT=0 ;;
esac

# -------------------------------------------------------------------- sync --
say "Syncing code"
ssh "$TARGET" "mkdir -p '$DEST' '$DEST/tmp' '$DEST/data'"

EXCLUDES=(
  --exclude '.git' --exclude '.venv' --exclude 'venv'
  --exclude '__pycache__' --exclude '*.pyc'
  --exclude '.env'          # secrets live in cPanel environment variables
  --exclude 'data'          # uploads and analysed databases stay on the server
  --exclude '.DS_Store' --exclude '.claude'
)
if ssh "$TARGET" 'command -v rsync >/dev/null 2>&1'; then
  rsync -az --delete-after "${EXCLUDES[@]}" "$SRC/" "$TARGET:$DEST/"
else
  warn "rsync not available on the server; falling back to tar over ssh"
  tar czf - -C "$SRC" \
      --exclude='.git' --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
      --exclude='*.pyc' --exclude='.env' --exclude='data' --exclude='.DS_Store' \
      --exclude='.claude' . | ssh "$TARGET" "tar xzf - -C '$DEST'"
fi

# ------------------------------------------------------------- virtualenv --
say "Locating the cPanel virtualenv"
VENV=$(ssh "$TARGET" "ls -d $REMOTE_HOME/virtualenv/$APP_ROOT/*/bin/activate 2>/dev/null | sort -V | tail -1" || true)
if [ -z "$VENV" ]; then
  warn "No virtualenv found at $REMOTE_HOME/virtualenv/$APP_ROOT/"
  warn "Create the app first: cPanel > Setup Python App, application root '$APP_ROOT',"
  warn "startup file 'passenger_wsgi.py', entry point 'application'. Then re-run."
  exit 1
fi
echo "  $VENV"

say "Installing dependencies (this can take a few minutes)"
ssh "$TARGET" "source '$VENV' && cd '$DEST' && pip install --quiet --upgrade pip && \
               pip install --quiet -r deploy/requirements-passenger.txt && \
               python -c 'import fastapi, anthropic, a2wsgi; print(\"  fastapi\", fastapi.__version__, \"| anthropic\", anthropic.__version__)'"

# --------------------------------------------------------------- harden ----
say "Securing runtime data"
ssh "$TARGET" "chmod 700 '$DEST/data' 2>/dev/null || true"
if [ "$IN_DOCROOT" = "1" ]; then
  ssh "$TARGET" "cp '$DEST/deploy/htaccess-app-root.conf' '$DEST/.htaccess.esp-protect' && \
                 cat '$DEST/.htaccess.esp-protect' >> '$DEST/.htaccess' && \
                 rm -f '$DEST/.htaccess.esp-protect'"
  echo "  appended deny rules for data/, esp/, scripts/, deploy/ and .env to $DEST/.htaccess"
fi

# ------------------------------------------- passenger .htaccess guard -----
# cPanel writes a Passenger .htaccess into the document root. If a parent
# directory carries a catch-all rewrite (a WordPress .htaccess one level up is
# the usual culprit), every ESP path that is not an existing file or directory
# gets rewritten before Passenger sees it - the app answers on /esp/ and returns
# 500 for every route beneath it. Giving this directory its own ruleset stops
# the inherited rules applying here.
say "Checking the Passenger .htaccess"
HTACCESS=$(ssh "$TARGET" "grep -rl 'PassengerAppRoot \"$DEST\"' \$HOME/public_html 2>/dev/null | head -1" || true)
if [ -n "$HTACCESS" ]; then
  echo "  $HTACCESS"
  if ssh "$TARGET" "grep -q 'ESP: neutralise inherited rewrites' '$HTACCESS'"; then
    echo "  rewrite guard already present"
  else
    ssh "$TARGET" "cp '$HTACCESS' '$HTACCESS.bak-esp' && cat >> '$HTACCESS' <<'EOF'

# ESP: neutralise inherited rewrites so Passenger sees every /esp/* request.
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteRule ^ - [L]
</IfModule>
EOF"
    echo "  added rewrite guard (original backed up alongside it)"
  fi
else
  warn "Could not find the Passenger .htaccess - create the app in cPanel first."
fi

# -------------------------------------------------------------- restart ----
say "Restarting Passenger"
ssh "$TARGET" "mkdir -p '$DEST/tmp' && touch '$DEST/tmp/restart.txt'"
echo "  touched $DEST/tmp/restart.txt"

# ----------------------------------------------------------------- verify --
if [ -n "$APP_URL" ]; then
  say "Smoke test"
  sleep 6
  CODE=$(curl -s -o /tmp/esp_deploy_check -m 60 -w '%{http_code}' "${APP_URL%/}/healthz" || echo 000)
  echo "  GET ${APP_URL%/}/healthz -> HTTP $CODE"
  if [ "$CODE" = "200" ]; then
    cat /tmp/esp_deploy_check; echo
    echo "  Deployment healthy."
  else
    warn "Not healthy yet. Check the Passenger log named in cPanel > Setup Python App,"
    warn "and confirm ESP_PASSWORD / ANTHROPIC_API_KEY are set as environment variables."
    head -c 400 /tmp/esp_deploy_check 2>/dev/null || true; echo
  fi
  rm -f /tmp/esp_deploy_check
fi

say "Done"
