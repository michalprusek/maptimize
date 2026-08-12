#!/bin/bash
#
# Install the backup job so it no longer depends on the checked-out git branch.
#
#   sudo ./scripts/install-backup.sh
#
# THE OUTAGE THIS PREVENTS, in full, because it cost nine nights:
#
# `maptimize-backup.service` pointed its ExecStart straight into the working
# tree — /home/cvat/maptimize/scripts/backup.sh. That file lived only on an
# unmerged branch. The moment anything else was checked out it ceased to exist,
# systemd reported 203/EXEC, and the nightly backup failed every night from
# 2026-08-04 to 2026-08-12 while the application itself stayed perfectly healthy.
# Nothing was wrong with the backup script. The path was a lie.
#
# A service that has to keep running is not allowed to read its own executable
# out of a directory whose contents change with `git checkout`. So the repo stays
# the source of truth and this installs a COPY somewhere stable.
#
# The copy can drift from the repo, which is the honest cost of the indirection.
# It is made visible rather than prevented: the installed script records the
# commit it came from, and backup.sh logs that on every run, so a stale copy is
# readable in the log instead of being a silent difference between what is in git
# and what actually ran.
#
# Re-run this after every change to backup.sh. It is idempotent.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/maptimize}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

if [ "$(id -u)" -ne 0 ]; then
    echo "This writes to $INSTALL_DIR and $UNIT_DIR — run it with sudo." >&2
    exit 1
fi

COMMIT=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
DIRTY=""
git -C "$REPO_DIR" diff --quiet -- scripts/backup.sh 2>/dev/null || DIRTY=" (uncommitted)"

install -d -m 755 "$INSTALL_DIR"
install -m 755 "$REPO_DIR/scripts/backup.sh" "$INSTALL_DIR/backup.sh"
printf 'BACKUP_SCRIPT_VERSION=%s%s\nBACKUP_SCRIPT_INSTALLED=%s\n' \
    "$COMMIT" "$DIRTY" "$(date '+%F %T')" > "$INSTALL_DIR/backup.version"
chmod 644 "$INSTALL_DIR/backup.version"

# The units are rewritten to point at the installed copy rather than the repo.
# sed on the shipped file, so the two cannot drift in the parts that are not
# about paths.
for unit in maptimize-backup.service maptimize-backup-failed.service maptimize-backup.timer; do
    [ -f "$REPO_DIR/scripts/$unit" ] || continue
    sed "s#$REPO_DIR/scripts/backup.sh#$INSTALL_DIR/backup.sh#g" \
        "$REPO_DIR/scripts/$unit" > "$UNIT_DIR/$unit"
    chmod 644 "$UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now maptimize-backup.timer

echo "installed $INSTALL_DIR/backup.sh from $COMMIT$DIRTY"
echo
echo "verify with:"
echo "  systemctl start maptimize-backup.service && cat /backup/maptimize/LAST_RESULT"
echo "  systemctl list-timers maptimize-backup.timer"
