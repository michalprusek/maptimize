#!/bin/bash
#
# Maptimize production backup.
#
# Backs up the THREE independent stores that together make a restorable system.
# A pg_dump alone is not a backup of this app:
#
#   1. Postgres        — metadata + pgvector embeddings
#   2. data/           — the actual pixels. DB rows reference these files BY PATH,
#                        so a dump restored without data/ is a database full of
#                        dead links (this already happened once in miniature when
#                        data/rag_documents/ was left unmounted — see CLAUDE.md,
#                        "Perzistence obrázků v chatu").
#   3. weights/        — best.pt is NOT in the repo and the backend will not start
#                        without it.
#
# Plus .env, which is gitignored and therefore exists nowhere else. The rest of
# the config (compose files, source) is on GitHub and needs no second copy here.
#
# Consistency model: online, no downtime. pg_dump takes its own transaction
# snapshot so the dump is internally consistent; data/ is rsynced alongside it.
# The seam is a file uploaded between the two steps, which can land in one and
# not the other. That costs one re-upload, not a corrupt restore — a deliberate
# trade against stopping the backend nightly.

set -eo pipefail
# pipefail matters here: a bare `cmd | cmd` masks failure of the left side.
# The classic form of this bug is `pg_dump | gzip` — gzip exits 0 on empty input
# and produces a valid 20-byte header, so a size check still passes and you
# silently install a useless backup. We avoid pipes for the dump entirely and
# verify the archive with pg_restore --list instead of trusting its size.

BACKUP_ROOT="${BACKUP_ROOT:-/backup/maptimize}"
SOURCE_DIR="${SOURCE_DIR:-/home/cvat/maptimize}"
DB_CONTAINER="${DB_CONTAINER:-maptimize-db}"
DB_USER="${DB_USER:-maptimize}"
DB_NAME="${DB_NAME:-maptimize}"
# Must stay the same major version as the server — a dump is only guaranteed
# readable by a pg_restore at least as new as the pg_dump that wrote it.
PG_IMAGE="${PG_IMAGE:-pgvector/pgvector:pg16}"
# Sanity floor for the TOC check. Production carries ~320 objects; anything
# under 50 means we captured a truncated or near-empty archive, not a schema
# that happens to have shrunk.
MIN_OBJECTS="${MIN_OBJECTS:-50}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MIN_FREE_GB="${MIN_FREE_GB:-40}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$BACKUP_ROOT/logs/backup.log"
STATUS_FILE="$BACKUP_ROOT/LAST_RESULT"
LOCK_FILE="$BACKUP_ROOT/.lock"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$LOG_FILE"; }

# Any exit path writes the status file, so a stale timestamp in LAST_RESULT is
# itself a signal that the run died hard (OOM-killed, disk yanked mid-write).
finish() {
    local rc=$?
    if [ $rc -eq 0 ]; then
        printf 'OK %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" > "$STATUS_FILE"
    else
        printf 'FAIL %s (exit %d) — see %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$rc" "$LOG_FILE" > "$STATUS_FILE"
        log "BACKUP FAILED (exit $rc)"
    fi
    exit $rc
}
mkdir -p "$BACKUP_ROOT"/{db,files,logs}

# Never let two runs overlap: a slow run plus a daily timer would otherwise have
# two rsyncs writing the same snapshot directory.
#
# Taken BEFORE the trap is installed on purpose. Losing the race is not a backup
# failure, and if it wrote LAST_RESULT it would stamp FAIL over a run that is at
# that moment succeeding — turning the health signal into a false alarm.
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "another backup is already running, aborting" >&2; exit 1; }

trap finish EXIT

log "=== backup start ($TIMESTAMP) ==="

# --- retention first ---------------------------------------------------------
# Expired backups are pruned BEFORE the new one so a nearly-full disk can still
# make progress. Only things past RETENTION_DAYS are touched, so this can never
# trade a known-good backup for one that has not been written yet.
find "$BACKUP_ROOT/db" -maxdepth 1 -name '*.dump' -mtime "+$RETENTION_DAYS" -print -delete \
    | sed 's/^/pruned: /' | tee -a "$LOG_FILE" || true
while IFS= read -r old; do
    log "pruned snapshot: $old"
    rm -rf "$old"
done < <(find "$BACKUP_ROOT/files" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS")

FREE_GB=$(df -BG --output=avail "$BACKUP_ROOT" | tail -1 | tr -dc '0-9')
if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    log "ERROR: only ${FREE_GB}GB free on $BACKUP_ROOT, need ${MIN_FREE_GB}GB"
    exit 1
fi
log "free space: ${FREE_GB}GB"

# --- 1. postgres -------------------------------------------------------------
DUMP="$BACKUP_ROOT/db/maptimize_${TIMESTAMP}.dump"
log "dumping database -> $(basename "$DUMP")"
# No -t/-it: allocating a TTY would corrupt the binary custom-format stream.
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -Fc --no-password "$DB_NAME" > "$DUMP"

# Prove the archive is READABLE, not merely non-empty. A truncated dump has a
# plausible size and fails only on the day you actually need it.
#
# The host has no postgres client, so verification runs in a throwaway container
# built from the image the database itself uses — same major version by
# construction, and nothing new to install or keep in sync. Reading the dump
# through `docker exec` instead does not work: pg_restore needs to seek, and
# both `-` and /dev/stdin fail with "did not find magic string in file header".
OBJECTS=$(docker run --rm -v "$BACKUP_ROOT/db:/b:ro" "$PG_IMAGE" \
    pg_restore --list "/b/$(basename "$DUMP")" 2>/dev/null | grep -c '^[0-9]' ) || true

if [ "${OBJECTS:-0}" -lt "$MIN_OBJECTS" ]; then
    log "ERROR: dump failed integrity check (pg_restore --list read ${OBJECTS:-0} objects, expected >=$MIN_OBJECTS)"
    exit 1
fi
log "database ok ($(du -h "$DUMP" | cut -f1), $OBJECTS objects)"

# --- 2. files ----------------------------------------------------------------
# --link-dest hardlinks unchanged files to yesterday's snapshot, so 14 daily
# snapshots of a 4.7GB tree cost ~4.7GB plus whatever actually changed. Each
# snapshot is still a complete, independently browsable directory.
SNAPSHOT="$BACKUP_ROOT/files/$TIMESTAMP"
PREV="$BACKUP_ROOT/files/latest"

# --link-dest must name the directory that corresponds to the DESTINATION, not
# the snapshot root: rsync compares entry-for-entry against it. Pointing it one
# level too high silently matches nothing and every run becomes a full copy —
# which still produces correct backups, just at 14x the disk, so it would go
# unnoticed until the disk filled.
link_dest_for() {
    # Emits into the caller's LINK array; keeps the option quoted so a path with
    # spaces can never split into two arguments.
    #
    # The explicit `return 0` is load-bearing under `set -e`: written as a
    # trailing `[ -d ... ] && LINK=(...)`, the function's exit status is the
    # failed test on the very first run (no previous snapshot yet), which aborts
    # the whole script right after a perfectly good database dump.
    LINK=()
    if [ -d "$PREV/$1" ]; then
        LINK=(--link-dest="$PREV/$1")
    fi
    return 0
}

log "syncing files -> files/$TIMESTAMP"
mkdir -p "$SNAPSHOT"

# uploads/temp/ is excluded on purpose: cleanup_old_temp_files() wipes it on every
# backend start, so nothing durable is allowed to live there by design.
link_dest_for data
rsync -a --delete "${LINK[@]}" \
    --exclude 'uploads/temp/' \
    "$SOURCE_DIR/data/" "$SNAPSHOT/data/"

link_dest_for weights
rsync -a --delete "${LINK[@]}" "$SOURCE_DIR/weights/" "$SNAPSHOT/weights/"

# .env is gitignored, so this backup is its only copy. It holds DB and API
# secrets — keep it unreadable to anyone but the owner.
install -m 600 "$SOURCE_DIR/.env" "$SNAPSHOT/env"

ln -sfn "$TIMESTAMP" "$BACKUP_ROOT/files/latest"
log "files ok ($(du -sh --apparent-size "$SNAPSHOT" | cut -f1) logical, $(du -sh "$SNAPSHOT" | cut -f1) new on disk)"

log "=== backup done: $(ls -1 "$BACKUP_ROOT"/db/*.dump | wc -l) dumps, $(find "$BACKUP_ROOT/files" -mindepth 1 -maxdepth 1 -type d | wc -l) snapshots, $(df -h "$BACKUP_ROOT" | tail -1 | awk '{print $4}') free ==="
