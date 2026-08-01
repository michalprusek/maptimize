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
#   3. weights/        — best.pt is custom-trained: not in the repo, and
#                        download_weights.py cannot fetch it (it only handles
#                        mobile_sam.pt, a public re-downloadable asset). This
#                        backup is its only copy. It is lazily loaded, so the
#                        backend still STARTS without it — cell detection fails
#                        at first use instead.
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
# Sanity floor for the TOC check. Production carried 335 objects on 2026-08-01;
# anything under 50 means we captured a near-empty archive, not a schema that
# happens to have shrunk.
MIN_OBJECTS="${MIN_OBJECTS:-50}"
# Row floor, because MIN_OBJECTS counts SCHEMA and a schema-only dump is a
# perfectly valid archive that passes every structural check. `docker compose
# down -v` (the operation CLAUDE.md warns about in bold) plus the backend's own
# create_all/ensure_schema_updates() rebuild an empty database within minutes of
# startup — structurally identical, zero rows. Without this floor every night
# after that reports OK while capturing nothing.
MIN_ROWS="${MIN_ROWS:-100}"
ROW_CHECK_TABLE="${ROW_CHECK_TABLE:-cell_crops}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MIN_FREE_GB="${MIN_FREE_GB:-40}"
# Retention floor. Age alone is not a safe rule: if every run fails for longer
# than RETENTION_DAYS (a renamed DB container, a wedged timer), an age-only prune
# deletes the last good backup and then the run fails too, leaving nothing. The
# floor makes "we have no backups at all" unreachable by pruning.
KEEP_MIN="${KEEP_MIN:-3}"
# Sanity floor for the file snapshot, mirroring MIN_OBJECTS for the database.
# Guards against pointing `latest` at a snapshot of an empty or wrong SOURCE_DIR.
MIN_FILES="${MIN_FILES:-1000}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$BACKUP_ROOT/logs/backup.log"
STATUS_FILE="$BACKUP_ROOT/LAST_RESULT"
LOCK_FILE="$BACKUP_ROOT/.lock"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$LOG_FILE"; }

# Written via temp+rename so the status is never observed half-written. A plain
# `> "$STATUS_FILE"` truncates BEFORE printf runs, so a full or read-only disk
# leaves a 0-byte LAST_RESULT — neither OK nor FAIL, and unparseable by anything
# monitoring it later.
set_status() {
    printf '%s\n' "$*" > "$STATUS_FILE.tmp" 2>/dev/null && mv "$STATUS_FILE.tmp" "$STATUS_FILE" \
        || echo "WARNING: cannot write $STATUS_FILE" >&2
}

LOCK_LOST=0

# Any exit path writes the status file, so a stale timestamp in LAST_RESULT is
# itself a signal that the run died hard (OOM-killed, disk yanked mid-write).
finish() {
    local rc=$?
    # Losing the flock race is not a backup failure and must not touch the
    # status file — it would stamp FAIL over a run that is at that moment
    # succeeding, turning the health signal into a false alarm.
    [ "$LOCK_LOST" -eq 1 ] && exit "$rc"

    # Never leave a half-written dump behind. It would be the newest file in
    # db/, which is what the restore runbook reaches for first.
    if [ -n "${PARTIAL:-}" ] && [ -e "${PARTIAL:-}" ]; then
        rm -f "$PARTIAL"
        log "removed unverified partial dump"
    fi
    if [ "$rc" -eq 0 ]; then
        set_status "OK $(date '+%Y-%m-%d %H:%M:%S')"
    else
        set_status "FAIL $(date '+%Y-%m-%d %H:%M:%S') (exit $rc) — see $LOG_FILE"
        log "BACKUP FAILED (exit $rc)"
    fi
    exit "$rc"
}
trap finish EXIT
# Bash runs the EXIT trap on an untrapped fatal signal too, and at that moment
# $? holds the status of the last COMPLETED command — normally 0. So without
# these, a SIGTERM'd run takes the success branch and writes OK over a backup
# that was killed halfway. That is not exotic: systemd SIGTERMs the cgroup on
# `systemctl stop` and on reboot, and unattended-upgrades reboots land in the
# 03:00 window. Verified: SIGTERM used to produce `OK`, now `FAIL (exit 143)`.
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

mkdir -p "$BACKUP_ROOT"/{db,files,logs}

# RequiresMountsFor= only guarantees something is mounted, and it does not apply
# at all when the script is run by hand. Without this check a missing mount lets
# the backup write into the empty /backup mountpoint ON THE ROOT DISK — the one
# it exists to protect against — and report success.
if [ "${REQUIRE_MOUNT:-1}" = "1" ] && ! mountpoint -q "$(dirname "$BACKUP_ROOT")"; then
    log "ERROR: $(dirname "$BACKUP_ROOT") is not a mount point — refusing to back up onto the root disk"
    exit 1
fi

# A read-only remount is the kernel's standard response to ext4 errors, and
# `mkdir -p` returns 0 for directories that already exist even then — so the
# script would sail past the line above and die later with the trap's status
# file itself unwritable. Prove writability explicitly.
if ! touch "$BACKUP_ROOT/.writable" 2>/dev/null; then
    log "ERROR: $BACKUP_ROOT is not writable (read-only remount?)"
    exit 1
fi
rm -f "$BACKUP_ROOT/.writable"

# Never let two runs overlap: a slow run plus a daily timer would otherwise have
# two rsyncs writing the same snapshot directory.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    LOCK_LOST=1
    echo "another backup is already running, aborting" >&2
    exit 1
fi

# A stale RUNNING marker is unambiguous, where a stale OK is indistinguishable
# from success. SIGKILL (OOM killer, TimeoutStopSec) cannot be trapped at all,
# so this is the only way that case leaves a usable trace.
set_status "RUNNING $(date '+%Y-%m-%d %H:%M:%S') pid=$$"

log "=== backup start ($TIMESTAMP) ==="

# --- retention first ---------------------------------------------------------
# Expired backups are pruned BEFORE the new one so a nearly-full disk can still
# make progress.
#
# Two conditions must BOTH hold before anything is deleted: older than
# RETENTION_DAYS *and* outside the newest KEEP_MIN. Age alone is not enough.
# Pruning runs before the new backup exists, so with an age-only rule a run of
# failures longer than the retention window deletes the last good backup and
# then fails as well — ending with zero backups and no alert. The free-space
# guard below made that worse, since it aborts *after* the prune: a full disk
# would empty the archive one night at a time while never writing anything.
prune() {  # $1 = label, $2 = directory, rest = find predicates
    local label="$1" dir="$2"; shift 2
    local kept=0 old
    while IFS= read -r old; do
        kept=$((kept + 1))
        if [ "$kept" -le "$KEEP_MIN" ]; then
            continue
        fi
        # Age is tested per entry rather than inside the find, so the newest
        # KEEP_MIN are skipped before age is ever consulted.
        if [ -z "$(find "$old" -maxdepth 0 -mtime "+$RETENTION_DAYS")" ]; then
            continue
        fi
        log "pruned $label: $(basename "$old")"
        rm -rf "$old"
    done < <(find "$dir" -mindepth 1 -maxdepth 1 "$@" -printf '%T@\t%p\n' 2>/dev/null \
             | sort -rn | cut -f2-)
}
# -type d skips the `latest` symlink, so the pointer is never pruned.
prune dump     "$BACKUP_ROOT/db"    -type f -name '*.dump'
prune snapshot "$BACKUP_ROOT/files" -type d

FREE_GB=$(df -BG --output=avail "$BACKUP_ROOT" | tail -1 | tr -dc '0-9')
# `[ "" -lt 40 ]` returns 2 and prints "integer expression expected", and set -e
# does NOT apply inside an `if` condition — so an unparseable value would make
# the guard silently pass rather than fail.
if ! [[ "$FREE_GB" =~ ^[0-9]+$ ]]; then
    log "ERROR: could not parse free space on $BACKUP_ROOT"
    exit 1
fi
if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    log "ERROR: only ${FREE_GB}GB free on $BACKUP_ROOT, need ${MIN_FREE_GB}GB"
    exit 1
fi
log "free space: ${FREE_GB}GB"

# --- 1. postgres -------------------------------------------------------------
DUMP="$BACKUP_ROOT/db/maptimize_${TIMESTAMP}.dump"
# Written under .partial and renamed only after verification, so the existence
# of a *.dump file MEANS it passed the checks below. Without this, a failed run
# leaves a plausible-looking corpse that is also the NEWEST file in db/ — and
# the restore runbook selects by mtime. The production restore path drops the
# database before restoring, so handing it a partial dump destroys the live data
# and replaces it with garbage, at exactly the moment someone is already in
# trouble. The EXIT trap removes the .partial on every failure path.
PARTIAL="$DUMP.partial"

# Content floor on the SOURCE, before spending 80s dumping it. A structurally
# perfect empty database passes every check further down — see MIN_ROWS above.
ROWS=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM $ROW_CHECK_TABLE")
if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || [ "$ROWS" -lt "$MIN_ROWS" ]; then
    log "ERROR: $ROW_CHECK_TABLE holds '${ROWS:-?}' rows, expected >=$MIN_ROWS — refusing to record this as a good backup"
    exit 1
fi

log "dumping database -> $(basename "$DUMP") ($ROW_CHECK_TABLE: $ROWS rows)"
# No -t/-it: allocating a TTY would corrupt the binary custom-format stream.
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -Fc --no-password "$DB_NAME" > "$PARTIAL"

# Verification runs in a throwaway container built from the image the database
# itself uses — same major version by construction, nothing to install on the
# host or keep in sync. Reading the dump through `docker exec` instead does not
# work: `-` is not special-cased at all (pg_restore tries to open a file literally
# named "-"), and /dev/stdin gives "did not find magic string in file header"
# because a pipe is not seekable.
verify() { docker run --rm -v "$BACKUP_ROOT/db:/b:ro" "$PG_IMAGE" "$@"; }
PART_NAME="/b/$(basename "$PARTIAL")"

# (a) FULL READ. This is the check that matters, and `pg_restore --list` is NOT
# a substitute for it: in the custom format the TOC lives at the HEAD of the
# archive, so --list never touches the data blocks. Measured on the real 1.2GB
# production dump truncated to 40MB — 3% of its content — --list still reported
# all 335 objects and exited 0. Restoring that "healthy" archive yields 31
# tables and zero rows: a backup that looks perfect until you open the app.
# -f /dev/null decompresses every block and costs ~12s on an ~80s run.
if ! verify pg_restore -f /dev/null "$PART_NAME" >>"$LOG_FILE" 2>&1; then
    log "ERROR: dump is not fully readable — archive is truncated or corrupt"
    exit 1
fi

# (b) Object count, kept as a SECOND and different assertion: a dump can be
# perfectly readable end-to-end and still have lost its schema.
#
# stderr goes to the log rather than /dev/null so that "the verifier could not
# run" (image missing, docker hiccup, mount denied) is distinguishable from
# "the archive is bad". Both are fail-closed, but they demand opposite fixes.
OBJECTS=$(verify pg_restore --list "$PART_NAME" 2>>"$LOG_FILE" | grep -c '^[0-9]') || true
if [ "${OBJECTS:-0}" -lt "$MIN_OBJECTS" ]; then
    log "ERROR: dump has only ${OBJECTS:-0} objects, expected >=$MIN_OBJECTS (see log for verifier stderr)"
    exit 1
fi

mv "$PARTIAL" "$DUMP"
log "database ok ($(du -h "$DUMP" | cut -f1), $OBJECTS objects, full read verified)"

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

# rsync exit 24 is "source files vanished during transfer". That is BENIGN and
# expected against a live tree — deleting an experiment cascades to its images
# and crops under data/uploads/, and the MCP connector accepts writes at any
# hour. Treating it as failure would mark good backups FAIL and, worse, skip the
# `latest` update at the end, so the next run would fall back to a full copy.
sync_tree() {
    local rc=0
    rsync -a --delete "$@" || rc=$?
    [ "$rc" -eq 0 ] || [ "$rc" -eq 24 ] || return "$rc"
    if [ "$rc" -eq 24 ]; then
        log "note: some source files vanished mid-sync (rsync 24) — expected on a live tree"
    fi
    return 0
}

# uploads/temp/ is excluded on purpose: cleanup_old_temp_files(max_age_hours=24)
# reaps it, and by design nothing referenced by a persisted message may live
# there (see code_execution_service.py) — so it is transient by construction.
link_dest_for data
sync_tree "${LINK[@]}" --exclude 'uploads/temp/' "$SOURCE_DIR/data/" "$SNAPSHOT/data/"

link_dest_for weights
sync_tree "${LINK[@]}" "$SOURCE_DIR/weights/" "$SNAPSHOT/weights/"

# .env is gitignored, so this backup is its only copy. It holds DB and API
# secrets — keep it unreadable to anyone but the owner.
install -m 600 "$SOURCE_DIR/.env" "$SNAPSHOT/env"

# Same idea as MIN_OBJECTS: refuse to advance `latest` to a snapshot that is
# obviously degraded. rsync exits 0 for an empty source, so without this a wrong
# SOURCE_DIR or a half-restored tree would silently become both the documented
# restore source and the --link-dest base for every night after.
FILES=$(find "$SNAPSHOT" -type f | wc -l)
if [ "$FILES" -lt "$MIN_FILES" ]; then
    log "ERROR: snapshot holds only $FILES files, expected >=$MIN_FILES — not advancing 'latest'"
    exit 1
fi
# best.pt is singled out at the top of this file as the one file without which
# the backend does not start; assert it rather than trusting the count above.
if [ ! -s "$SNAPSHOT/weights/best.pt" ]; then
    log "ERROR: weights/best.pt missing or empty in snapshot"
    exit 1
fi

# True incremental cost. `du -sh "$SNAPSHOT"` alone cannot measure this: within a
# single invocation du counts each hardlinked file the first time it sees it, so
# a fully deduplicated snapshot still reports its full logical size. That number
# reads identically whether --link-dest works perfectly or not at all — i.e. it
# is blind to exactly the regression the comment above warns about. Passing both
# directories lets du credit the shared inodes to the first one.
if [ -d "$PREV" ]; then
    NEW_BLOCKS=$(du -sh "$(readlink -f "$PREV")" "$SNAPSHOT" | tail -1 | cut -f1)
else
    NEW_BLOCKS=$(du -sh "$SNAPSHOT" | cut -f1)
fi

ln -sfn "$TIMESTAMP" "$BACKUP_ROOT/files/latest"
log "files ok ($FILES files, $(du -sh --apparent-size "$SNAPSHOT" | cut -f1) logical, $NEW_BLOCKS new on disk)"

log "=== backup done: $(ls -1 "$BACKUP_ROOT"/db/*.dump | wc -l) dumps, $(find "$BACKUP_ROOT/files" -mindepth 1 -maxdepth 1 -type d | wc -l) snapshots, $(df -h "$BACKUP_ROOT" | tail -1 | awk '{print $4}') free ==="
