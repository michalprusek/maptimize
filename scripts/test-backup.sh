#!/bin/bash
#
# Tests for scripts/backup.sh.
#
# Runs the REAL, unmodified script against a temp BACKUP_ROOT, a fake source
# tree and a throwaway Postgres container. Touches no production data.
#
#   ./scripts/test-backup.sh
#
# Scope is deliberate: this pins the decisions the script MAKES, not the tools
# it calls. Whether rsync copies files or systemd honours Persistent=true is
# somebody else's test suite. What belongs here is every invariant the script
# argues for in prose — because prose does not fail when it stops being true,
# and three of these were wrong when first written:
#
#   * the integrity check certified a dump missing 97% of its data
#   * retention could delete every backup before writing the new one
#   * a failed run left a corpse that the restore runbook selects by mtime

set -uo pipefail
cd "$(dirname "$0")/.."
SCRIPT="$PWD/scripts/backup.sh"

TMP=$(mktemp -d)
DB="backup-test-db-$$"
PASS=0; FAIL=0

cleanup() { docker rm -f "$DB" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }

# --- fixture -----------------------------------------------------------------
mkdir -p "$TMP/src/data/uploads/temp" "$TMP/src/weights" "$TMP/root"
printf 'SECRET=x\n' > "$TMP/src/.env"
# MIN_FILES defaults to 1000, so the fixture must look like a real tree.
for i in $(seq 1 1200); do printf 'payload %s\n' "$i" > "$TMP/src/data/uploads/f$i.bin"; done
printf 'transient\n' > "$TMP/src/data/uploads/temp/junk.txt"
printf 'weights\n'   > "$TMP/src/weights/best.pt"

docker run -d --name "$DB" -e POSTGRES_USER=maptimize -e POSTGRES_PASSWORD=x \
    -e POSTGRES_DB=maptimize pgvector/pgvector:pg16 >/dev/null
for _ in $(seq 1 60); do docker exec "$DB" pg_isready -U maptimize -q 2>/dev/null && break; sleep 1; done
# A handful of objects so MIN_OBJECTS has something to count.
for i in $(seq 1 12); do
    docker exec "$DB" psql -U maptimize -d maptimize -q -c \
        "CREATE TABLE t$i(id int primary key); INSERT INTO t$i VALUES ($i);"
done
# Bulk rows so the DATA section dominates the archive. Without this the TOC is
# most of the file, and the truncation test below cannot cut data without also
# cutting the TOC — which would make --list fail for the wrong reason and stop
# the test from pinning anything.
docker exec "$DB" psql -U maptimize -d maptimize -q -c \
    "CREATE TABLE bulk(id int primary key, pad text);
     INSERT INTO bulk SELECT g, repeat('x', 200) FROM generate_series(1, 200000) g;"

# MIN_OBJECTS/MIN_ROWS are lowered for the toy fixture; REQUIRE_MOUNT is off
# because $TMP is not its own mount point.
run() {
    BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" DB_CONTAINER="$DB" \
    MIN_FREE_GB=1 MIN_OBJECTS=10 MIN_ROWS=1 ROW_CHECK_TABLE=t1 REQUIRE_MOUNT=0 \
    KEEP_MIN="${KEEP_MIN:-3}" RETENTION_DAYS="${RETENTION_DAYS:-14}" \
    "$SCRIPT" >>"$TMP/out.log" 2>&1
}

echo "=== backup.sh ==="

# --- 1. happy path ------------------------------------------------------------
run; check "run succeeds" "$?" "0"
check "LAST_RESULT is OK" "$(cut -d' ' -f1 "$TMP/root/LAST_RESULT")" "OK"
check "uploads/temp excluded" \
      "$(find "$TMP/root/files/latest/data/uploads/temp" -type f 2>/dev/null | wc -l)" "0"
check ".env stored 0600" "$(stat -c '%a' "$TMP/root/files/latest/env")" "600"

# --- 2. hardlink dedup --------------------------------------------------------
sleep 1.1   # timestamps have second resolution; two runs must not collide
run
SNAPS=$(find "$TMP/root/files" -mindepth 1 -maxdepth 1 -type d | sort)
A=$(echo "$SNAPS" | head -1); B=$(echo "$SNAPS" | tail -1)
check "second snapshot created" "$(echo "$SNAPS" | wc -l)" "2"
check "unchanged file is hardlinked" \
      "$(stat -c '%i' "$A/data/uploads/f1.bin")" "$(stat -c '%i' "$B/data/uploads/f1.bin")"

# --- 3. truncated dump must be REJECTED ---------------------------------------
# The bug this pins: pg_restore --list reads only the TOC, which sits at the head
# of a custom-format archive, so a dump missing nearly all of its data still
# lists every object and exits 0.
# Half the file: past the TOC (which lives at the head) but missing data. A
# smaller cut would eat into the TOC itself and --list would fail for the wrong
# reason, making the second assertion below pin nothing.
GOOD=$(find "$TMP/root/db" -name '*.dump' | head -1)
head -c "$(( $(stat -c%s "$GOOD") / 2 ))" "$GOOD" > "$TMP/root/db/truncated.dump.partial"
if docker run --rm -v "$TMP/root/db:/b:ro" pgvector/pgvector:pg16 \
       pg_restore -f /dev/null /b/truncated.dump.partial >/dev/null 2>&1; then
    bad "truncated dump is rejected by full read"
else
    ok "truncated dump is rejected by full read"
fi
if docker run --rm -v "$TMP/root/db:/b:ro" pgvector/pgvector:pg16 \
       pg_restore --list /b/truncated.dump.partial >/dev/null 2>&1; then
    ok "--list alone would have ACCEPTED it (why the full read exists)"
else
    bad "--list unexpectedly rejected it; this test no longer pins anything"
fi
rm -f "$TMP/root/db/truncated.dump.partial"
# The two assertions above prove the MECHANISM; this one proves the script still
# uses it. Without it, deleting the full read from backup.sh leaves all 17 tests
# green — the gap that let the original weak check ship in the first place.
if grep -q 'pg_restore -f /dev/null' "$SCRIPT"; then
    ok "backup.sh gates on the full read"
else
    bad "backup.sh no longer performs a full read of the dump"
fi

# --- 4. retention keeps a floor ----------------------------------------------
# Age alone is not safe: a run of failures longer than RETENTION_DAYS would
# otherwise delete the last good backup before writing a new one.
# The run must FAIL for this to test anything. Pruning happens before the dump,
# so a SUCCEEDING run always leaves its own fresh backup behind and a naive
# "at least one dump exists" assertion passes even with the floor removed —
# verified by perturbation, it did exactly that. The scenario the floor exists
# for is a run of failures outliving the retention window.
touch -d "30 days ago" "$TMP/root"/db/*.dump
find "$TMP/root/files" -mindepth 1 -maxdepth 1 -type d -exec touch -d "30 days ago" {} +
AGED=$(find "$TMP/root/db" -name '*.dump' | wc -l)
BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" DB_CONTAINER=no-such-container-$$ \
    MIN_FREE_GB=1 MIN_OBJECTS=10 MIN_ROWS=1 ROW_CHECK_TABLE=t1 REQUIRE_MOUNT=0 \
    "$SCRIPT" >>"$TMP/out.log" 2>&1 && bad "aged-prune run should have failed" || true
check "expired dumps survive a failed run" \
      "$([ "$(find "$TMP/root/db" -name '*.dump' | wc -l)" -ge 1 ] && echo yes)" "yes"
check "expired snapshots survive a failed run" \
      "$([ "$(find "$TMP/root/files" -mindepth 1 -maxdepth 1 -type d | wc -l)" -ge 1 ] && echo yes)" "yes"
check "latest still resolves" "$([ -d "$TMP/root/files/latest" ] && echo yes)" "yes"
[ "$AGED" -ge 1 ] || bad "fixture bug: nothing was aged, the test above proves nothing"

# --- 5. first run must not abort ---------------------------------------------
# link_dest_for ends in a test that fails when no previous snapshot exists;
# without its explicit `return 0` set -e kills the script right after a good dump.
rm -rf "$TMP/root/files"
sleep 1.1; run
check "first-run path (no 'latest') succeeds" "$?" "0"

# --- 6. a failed dump leaves nothing behind ----------------------------------
# The restore runbook picks the newest *.dump by mtime, so a corpse from a failed
# run is what a disaster recovery would reach for.
BEFORE=$(find "$TMP/root/db" -name '*.dump' | wc -l)
DB_CONTAINER=no-such-container-$$ BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" \
    MIN_FREE_GB=1 "$SCRIPT" >>"$TMP/out.log" 2>&1 && bad "run should have failed" || true
check "no new dump after failure" "$(find "$TMP/root/db" -name '*.dump' | wc -l)" "$BEFORE"
check "no .partial left behind" "$(find "$TMP/root/db" -name '*.partial' | wc -l)" "0"
check "LAST_RESULT is FAIL" "$(cut -d' ' -f1 "$TMP/root/LAST_RESULT")" "FAIL"

# --- 7. a killed run must NOT report success ---------------------------------
# Bash runs the EXIT trap on a fatal signal with $? from the last COMPLETED
# command — normally 0 — so without explicit signal traps a SIGTERM'd backup
# writes OK over a run that died halfway. systemd SIGTERMs the cgroup on reboot.
#
# The whole toy run takes under a second, so a naive `sleep 2 && kill` races and
# kills a process that already finished successfully — the test then reads the
# OK it was supposed to catch and passes for the wrong reason. Hold the script at
# a known point instead: ROW_CHECK_TABLE is interpolated into a SELECT, so a
# cross join against pg_sleep parks it in the row check, before the dump.
sleep 1.1
BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" DB_CONTAINER="$DB" MIN_FREE_GB=1 \
    MIN_OBJECTS=10 MIN_ROWS=1 REQUIRE_MOUNT=0 \
    ROW_CHECK_TABLE='t1, (SELECT pg_sleep(6)) AS _hold' \
    "$SCRIPT" >>"$TMP/out.log" 2>&1 &
KILLPID=$!
sleep 2; kill -TERM "$KILLPID" 2>/dev/null || true
wait "$KILLPID" 2>/dev/null || true
check "SIGTERM does not report OK" "$(cut -d' ' -f1 "$TMP/root/LAST_RESULT")" "FAIL"

# --- 8. an empty database is refused -----------------------------------------
# A schema-only dump is a structurally perfect archive: it passes the full read
# AND the object count. `docker compose down -v` plus the backend's create_all
# reproduces exactly this within minutes.
docker exec "$DB" psql -U maptimize -d maptimize -q -c "DELETE FROM t1;"
sleep 1.1
BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" DB_CONTAINER="$DB" MIN_FREE_GB=1 \
    MIN_OBJECTS=10 MIN_ROWS=1 ROW_CHECK_TABLE=t1 REQUIRE_MOUNT=0 \
    "$SCRIPT" >>"$TMP/out.log" 2>&1 && bad "empty table should have failed" \
    || ok "empty source table is refused"

# --- 9. refuses to back up onto a non-mount ----------------------------------
# Without this the backup lands in the empty /backup mountpoint on the root
# disk — the disk it exists to protect against — and reports success.
docker exec "$DB" psql -U maptimize -d maptimize -q -c "INSERT INTO t1 VALUES (99);"
sleep 1.1
BACKUP_ROOT="$TMP/root" SOURCE_DIR="$TMP/src" DB_CONTAINER="$DB" MIN_FREE_GB=1 \
    MIN_OBJECTS=10 MIN_ROWS=1 ROW_CHECK_TABLE=t1 REQUIRE_MOUNT=1 \
    "$SCRIPT" >>"$TMP/out.log" 2>&1 && bad "non-mount BACKUP_ROOT should have failed" \
    || ok "refuses a BACKUP_ROOT that is not a mount point"

echo
echo "passed $PASS, failed $FAIL   (log: $TMP/out.log)"
[ "$FAIL" -eq 0 ]
