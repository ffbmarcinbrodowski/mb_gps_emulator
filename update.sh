#!/bin/sh
# Update the emulator container to the latest version from GitHub.
# Made for QNAP Container Station, works on any Linux with Docker.
#
#   cd /share/Container/mb_gps_emulator     # the folder with docker-compose.yml
#   sh update.sh                            # pull, back up the database, rebuild, restart
#   sh update.sh --force                    # rebuild even if there is no new version
#
# - git is not needed on the NAS: if it is missing, git runs in a small container (alpine/git).
# - The repository is private: put a GitHub token (read access to the repo) into the file
#   .github_token next to this script. It is only sent as a header, never written to the git config.
# - The database (data/emulator.db) is backed up to data/backup/ before every update (last 10 kept).
#   Database migrations run automatically when the new container starts.

set -eu

KEEP_BACKUPS=10
HEALTH_TIMEOUT_S=120
CONTAINER=gps-emulator
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

cd "$(dirname "$0")"
DIR=$(pwd)
[ -f docker-compose.yml ] || { echo "ERROR: docker-compose.yml not found in $DIR"; exit 1; }

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# --- Docker (QNAP: Container Station is often not in PATH) -------------------------------

DOCKER=$(command -v docker || true)
if [ -z "$DOCKER" ]; then
    CS=$(getcfg container-station Install_Path -f /etc/config/qpkg.conf 2>/dev/null || true)
    for d in "$CS/bin" /share/CACHEDEV1_DATA/.qpkg/container-station/bin; do
        [ -x "$d/docker" ] && DOCKER="$d/docker" && PATH="$d:$PATH" && break
    done
fi
[ -n "$DOCKER" ] || { echo "ERROR: docker not found - is Container Station running?"; exit 1; }

if "$DOCKER" compose version >/dev/null 2>&1; then
    compose() { "$DOCKER" compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    compose() { docker-compose "$@"; }
else
    echo "ERROR: neither 'docker compose' nor 'docker-compose' found"; exit 1
fi

# --- git (local or in a container) ---------------------------------------------------------

# Never ask for a GitHub password (GitHub accepts only tokens) - fail with a hint instead.
export GIT_TERMINAL_PROMPT=0
AUTH=""
if [ -f .github_token ]; then
    TOKEN=$(tr -d ' \r\n' < .github_token)
    B64=$(printf 'x-access-token:%s' "$TOKEN" | { base64 2>/dev/null || openssl base64; } | tr -d '\n')
    AUTH="Authorization: Basic $B64"
fi

git_() {
    if command -v git >/dev/null 2>&1; then
        if [ -n "$AUTH" ]; then git -c "http.extraHeader=$AUTH" "$@"; else git "$@"; fi
    else
        # safe.directory: the folder belongs to another user than root inside the container
        if [ -n "$AUTH" ]; then
            "$DOCKER" run --rm -e GIT_TERMINAL_PROMPT=0 -v "$DIR":/repo -w /repo alpine/git \
                -c safe.directory=/repo -c "http.extraHeader=$AUTH" "$@"
        else
            "$DOCKER" run --rm -e GIT_TERMINAL_PROMPT=0 -v "$DIR":/repo -w /repo alpine/git \
                -c safe.directory=/repo "$@"
        fi
    fi
}

[ -d .git ] || { echo "ERROR: $DIR is not a git clone (git clone the repository first)"; exit 1; }

# --- 1. New version? -------------------------------------------------------------------------

log "Checking GitHub for a new version..."
OLD=$(git_ rev-parse --short HEAD)
if ! git_ fetch --quiet origin; then
    echo
    if [ -z "$AUTH" ]; then
        echo "ERROR: GitHub needs a token for this private repository, but $DIR/.github_token does not exist."
    else
        echo "ERROR: GitHub refused the token in .github_token (expired, or no access to the repository?)."
    fi
    echo "Create a fine-grained token on GitHub (Settings > Developer settings > Personal access tokens):"
    echo "only this repository, permission 'Contents: Read-only'. Then save it on the NAS:"
    echo "    cat > .github_token      (paste the token, Enter, Ctrl+D)"
    echo "    chmod 600 .github_token"
    exit 1
fi
BRANCH=$(git_ rev-parse --abbrev-ref HEAD)
NEW=$(git_ rev-parse --short "origin/$BRANCH")

if [ "$OLD" = "$NEW" ] && [ "$FORCE" -eq 0 ]; then
    log "Already up to date ($OLD). Use --force to rebuild anyway."
    exit 0
fi
if [ "$OLD" != "$NEW" ]; then
    log "New version: $OLD -> $NEW"
    git_ log --oneline "HEAD..origin/$BRANCH" | sed 's/^/    /'
fi

# Local edits of tracked files would block the update (.env and data/ are not tracked).
if [ -n "$(git_ status --porcelain --untracked-files=no)" ]; then
    echo "ERROR: files of the project were changed on the NAS:"
    git_ status --short --untracked-files=no
    echo "Undo them (git checkout -- <file>) or move them away, then run the update again."
    exit 1
fi

# --- 2. Back up the database -------------------------------------------------------------------

if [ -f data/emulator.db ]; then
    mkdir -p data/backup
    STAMP=$(date '+%Y-%m-%d_%H-%M-%S')
    BACKUP="data/backup/emulator_${STAMP}_${OLD}.db"
    # SQLite backup() inside the running container gives a consistent copy while it writes;
    # if the container is not running, the file can simply be copied.
    if "$DOCKER" exec "$CONTAINER" python -c \
        "import sqlite3; s = sqlite3.connect('/data/emulator.db'); d = sqlite3.connect('/data/backup/$(basename "$BACKUP")'); s.backup(d); d.close()" \
        2>/dev/null; then
        :
    else
        cp data/emulator.db "$BACKUP"
    fi
    log "Database backed up: $BACKUP"
    # keep only the newest backups
    ls -1t data/backup/emulator_*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r f; do rm -f "$f"; done
else
    log "No data/emulator.db yet - nothing to back up"
fi

# --- 3. Update the code, rebuild and restart -------------------------------------------------

if [ "$OLD" != "$NEW" ]; then
    git_ merge --ff-only --quiet "origin/$BRANCH"
    log "Code updated to $(git_ log -1 --format='%h %s')"
fi

log "Building and restarting the container (takes a minute)..."
compose up -d --build --remove-orphans

# --- 4. Wait until it is healthy -------------------------------------------------------------

log "Waiting for the health check..."
waited=0
status=starting
while [ "$waited" -lt "$HEALTH_TIMEOUT_S" ]; do
    status=$("$DOCKER" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$CONTAINER" 2>/dev/null || echo missing)
    [ "$status" = healthy ] && break
    [ "$status" = unhealthy ] && break
    sleep 5
    waited=$((waited + 5))
done

if [ "$status" != healthy ]; then
    echo
    echo "ERROR: the container is '$status' after the update. Last log lines:"
    compose logs --tail 30 emulator || true
    echo
    echo "To go back to the previous version:"
    if command -v git >/dev/null 2>&1; then
        echo "  git reset --hard $OLD"
    else
        echo "  docker run --rm -v \"$DIR\":/repo -w /repo alpine/git -c safe.directory=/repo reset --hard $OLD"
    fi
    [ -n "${BACKUP:-}" ] && echo "  cp $BACKUP data/emulator.db   (only if the database was changed)"
    echo "  docker compose up -d --build"
    exit 1
fi

# Old image layers of previous builds take disk space on the NAS.
"$DOCKER" image prune -f >/dev/null 2>&1 || true

log "Done: emulator $(git_ rev-parse --short HEAD) is running (port 5057)."
