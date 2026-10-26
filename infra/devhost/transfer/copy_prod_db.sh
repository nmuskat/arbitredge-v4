#!/usr/bin/env bash
# =============================================================================
# Copy the newest PRODUCTION BACKUP of arbitredge.db to the V4 dev droplet.
#
# Run ON THE DEV DROPLET as root, in a session opened with agent forwarding:
#     ssh -A root@<dev droplet IP>
#     bash transfer/copy_prod_db.sh            (--check: preflight only)
#
# Production impact, by design:
#   * the LIVE database is never opened or copied: the source is the newest
#     completed file in production's own backup directory (written daily by
#     V3's integrity-checked backup job);
#   * read-only: production gets no new files, no writes, no restarts;
#   * the reader runs at nice 19 / idle I/O priority, is rate-limited
#     (default 20 MB/s) and drops each chunk from the page cache so the
#     1 GB production server's cache (live DB, website) is not evicted.
# On the dev side the copy is SHA-256 verified end to end, integrity-checked,
# scanned for API keys (same-length redaction, then re-checked), and stored
# root-owned, read-only to arbitredge-dev.
# =============================================================================
set -Eeuo pipefail
umask 077
PROD="${PROD:-root@143.244.160.20}"
PROD_SSH="${PROD_SSH:-ssh -T -o ConnectTimeout=15}"
PROD_BACKUP_DIR="${PROD_BACKUP_DIR:-/opt/arbitredge/db/backups}"
PROD_DB="${PROD_DB:-/opt/arbitredge/db/arbitredge.db}"
IMPORT_DIR="${IMPORT_DIR:-/srv/arbitredge_v4/import}"
DEV_GROUP="${DEV_GROUP:-arbitredge-dev}"
ETC_DIR="${ETC_DIR:-/etc/arbitredge-v4}"
GW_CODE="${GW_CODE:-/opt/arbitredge-v4-gateway}"
RATE_MBPS="${RATE_MBPS:-20}"
MIN_AGE_MIN="${MIN_AGE_MIN:-10}"     # skip a backup that may still be being written
ASSUME_YES="${ASSUME_YES:-0}"
MODE=apply; [[ "${1:-}" == --check ]] && MODE=check

[[ $EUID -eq 0 ]] || { echo "run as root on the dev droplet" >&2; exit 2; }
[[ -e /opt/arbitredge ]] && { echo "refusing: this looks like production (/opt/arbitredge exists)" >&2; exit 2; }
[[ -d "$IMPORT_DIR" ]] || { echo "run devhost_bootstrap.sh first ($IMPORT_DIR missing)" >&2; exit 2; }

# ---------------------------------------------------------------- preflight (production, read-only)
read -r -d '' PREFLIGHT <<'REMOTE' || true
set -u
echo "NOW $(date +%s)"
df -Pk / | awk 'NR==2 {print "DF", $4, $5}'
echo "MEM $(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
echo "LOAD $(cut -d' ' -f1-3 /proc/loadavg)"
[ -f "$DB" ] && echo "LIVE $(stat -c %s "$DB")"
if [ -d "$BK" ]; then
  for f in "$BK"/arbitredge_backup_*.db; do
    [ -f "$f" ] || continue
    [ "$(head -c 15 "$f" 2>/dev/null)" = "SQLite format 3" ] || { echo "NOTSQLITE $f"; continue; }
    echo "BK $(stat -c '%Y %s' "$f") $f"
  done
else
  echo "NOBKDIR $BK"
fi
REMOTE

echo "== Production preflight (read-only)"
pre="$($PROD_SSH "$PROD" "BK='$PROD_BACKUP_DIR' DB='$PROD_DB' sh -s" <<<"$PREFLIGHT")" \
  || { echo "cannot reach production over SSH (did you connect with ssh -A?)" >&2; exit 1; }
now=$(awk '$1=="NOW"{print $2}' <<<"$pre")
read -r _ prod_free_kb prod_use < <(grep '^DF ' <<<"$pre")
echo "production disk: $((prod_free_kb/1024)) MB free ($prod_use used); RAM available: $(awk '$1=="MEM"{print $2}' <<<"$pre") MB; load: $(grep '^LOAD' <<<"$pre" | cut -d' ' -f2-)"
grep -q '^NOBKDIR' <<<"$pre" && { echo "REFUSED: backup directory $PROD_BACKUP_DIR not found on production." >&2
  echo "  (Set PROD_BACKUP_DIR=... if backups live elsewhere. The live database is never copied by this script.)" >&2; exit 2; }
grep '^NOTSQLITE' <<<"$pre" | sed 's/^NOTSQLITE /note: not a SQLite file, ignored: /' || true

pick="$(grep '^BK ' <<<"$pre" | awk -v now="$now" -v min="$MIN_AGE_MIN" '(now-$2) >= min*60' | sort -k2,2n | tail -1 || true)"
[[ -n "$pick" ]] || { echo "REFUSED: no completed backup (older than ${MIN_AGE_MIN} min) in $PROD_BACKUP_DIR." >&2
  echo "  Production backups may be failing (disk nearly full): see PROD_DISK_PLAN.md." >&2; exit 2; }
read -r _ bk_mtime bk_size bk_path <<<"$pick"
age_h=$(( (now - bk_mtime) / 3600 ))
echo "source: $bk_path"
echo "        $((bk_size/1024/1024)) MB, completed $(date -u -d "@$bk_mtime" '+%Y-%m-%d %H:%M UTC') (${age_h} h ago)"
(( age_h > 48 )) && echo "WARNING: newest backup is ${age_h} h old — production's daily backup is probably failing (see PROD_DISK_PLAN.md). Copy is still safe; data will be that old."

# ---------------------------------------------------------------- dev-side capacity
dev_free_kb=$(df -Pk "$IMPORT_DIR" | awk 'NR==2 {print $4}')
need_kb=$(( bk_size/1024*11/10 + 5*1024*1024 ))
(( dev_free_kb >= need_kb )) || { echo "REFUSED: dev disk free $((dev_free_kb/1024)) MB < needed $((need_kb/1024)) MB" >&2; exit 2; }
eta_min=$(( bk_size / (RATE_MBPS*1024*1024) / 60 + 1 ))
echo "transfer: ~${eta_min} min at ${RATE_MBPS} MB/s (nice 19, idle I/O, page-cache friendly)"

[[ $MODE == check ]] && { echo "(--check: nothing copied)"; exit 0; }
if [[ "$ASSUME_YES" != 1 ]]; then
  read -r -p "Copy this backup to $IMPORT_DIR? Type 'yes': " ans
  [[ "$ans" == yes ]] || { echo "aborted; nothing copied"; exit 1; }
fi

# ---------------------------------------------------------------- stream (production, read-only)
read -r -d '' STREAMER <<'PY' || true
import hashlib, os, sys, time
path, rate = sys.argv[1], float(sys.argv[2]) * 1024 * 1024
fd = os.open(path, os.O_RDONLY)
st0 = os.fstat(fd)
h, n, t0, out = hashlib.sha256(), 0, time.time(), sys.stdout.buffer
while True:
    b = os.read(fd, 1 << 20)
    if not b:
        break
    h.update(b)
    out.write(b)
    try:
        os.posix_fadvise(fd, n, len(b), os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass
    n += len(b)
    ahead = n / rate - (time.time() - t0)
    if ahead > 0:
        time.sleep(ahead)
out.flush()
st1 = os.fstat(fd)
changed = int(st1.st_mtime != st0.st_mtime or st1.st_size != st0.st_size)
sys.stderr.write("STREAM %s %d %d\n" % (h.hexdigest(), n, changed))
PY

part="$IMPORT_DIR/.incoming.part"
errf="$(mktemp)"
trap 'rm -f "$part" "$errf"' EXIT
echo "== Streaming..."
# The streamer source travels on stdin; the file bytes come back on stdout, the checksum on stderr.
$PROD_SSH "$PROD" "sh -c 'code=\$(cat); exec nice -n 19 ionice -c 3 python3 -I -c \"\$code\" \"\$0\" \"\$1\"' '$bk_path' '$RATE_MBPS'" \
  <<<"$STREAMER" >"$part" 2>"$errf" || { echo "stream failed: $(tail -3 "$errf")" >&2; exit 1; }
read -r tag remote_sha remote_bytes changed < <(grep '^STREAM ' "$errf" | tail -1) || true
[[ "${tag:-}" == STREAM ]] || { echo "stream ended without a checksum; discarded" >&2; exit 1; }
[[ "$changed" == 0 ]] || { echo "source file changed during the copy; discarded (re-run later)" >&2; exit 1; }
local_sha=$(sha256sum "$part" | awk '{print $1}')
local_bytes=$(stat -c %s "$part")
[[ "$local_sha" == "$remote_sha" && "$local_bytes" == "$remote_bytes" && "$local_bytes" == "$bk_size" ]] \
  || { echo "checksum/size mismatch (remote $remote_sha/$remote_bytes, local $local_sha/$local_bytes); discarded" >&2; exit 1; }
echo "sha256 verified end to end: $local_sha"

# ---------------------------------------------------------------- verify, scan, redact, publish (dev)
echo "== Integrity check and API-key scan"
report="$(python3 -I - "$part" "$ETC_DIR/odds_api_key" "$GW_CODE" <<'PY'
import json, mmap, os, re, sqlite3, sys
path, keyfile, gwcode = sys.argv[1:4]

def check(p):
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
        r = c.execute("PRAGMA quick_check").fetchall()
        tables = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        c.close()
        return [x[0] for x in r], tables
    except sqlite3.DatabaseError as e:      # malformed file: report as a failed check -> quarantine
        return [f"error: {e}"], []

patterns = [re.compile(rb"apiKey=[A-Za-z0-9]{20,64}"), re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")]
exact = None
if os.path.exists(keyfile):
    sys.path.insert(0, gwcode)
    import odds_gateway
    exact = odds_gateway.read_key(keyfile).encode()

qc, tables = check(path)
redactions = 0
unredactable = 0
with open(path, "r+b") as f:
    mm = mmap.mmap(f.fileno(), 0)
    if exact:
        i = mm.find(exact)
        while i != -1:
            mm[i:i + len(exact)] = b"X" * len(exact); redactions += 1
            i = mm.find(exact, i + len(exact))
    for m in list(patterns[0].finditer(mm)):
        s, e = m.start() + len(b"apiKey="), m.end()
        if mm[s:e] != b"X" * (e - s):        # skip values already redacted above
            mm[s:e] = b"X" * (e - s); redactions += 1
    unredactable = len(list(patterns[1].finditer(mm)))
    mm.flush(); mm.close()
qc2, _ = check(path) if redactions else (qc, tables)
print(json.dumps({"quick_check": qc, "quick_check_after_redaction": qc2, "tables": len(tables),
                  "redactions": redactions, "private_key_blocks": unredactable}))
PY
)" || { echo "integrity check failed to run; discarded" >&2; exit 1; }
echo "$report"
ok=$(python3 -I -c 'import json,sys; r=json.loads(sys.argv[1]); print(int(r["quick_check"]==["ok"] and r["quick_check_after_redaction"]==["ok"] and r["private_key_blocks"]==0))' "$report")

stamp=$(date -u -d "@$bk_mtime" +%Y%m%dT%H%M%SZ)
if [[ "$ok" != 1 ]]; then
  q="$IMPORT_DIR/QUARANTINE_arbitredge_prod_copy_$stamp.db"
  mv -f "$part" "$q"; chown root:root "$q"; chmod 0400 "$q"
  echo "QUARANTINED (root-only, not readable by the dev user): $q" >&2
  echo "Reason: integrity check failed or private-key material found. Review before releasing." >&2
  exit 3
fi
final="$IMPORT_DIR/arbitredge_prod_copy_$stamp.db"
mv -f "$part" "$final"
chown root:"$DEV_GROUP" "$final"; chmod 0440 "$final"
python3 -I - "$IMPORT_DIR/arbitredge_prod_copy_$stamp.json" <<PY
import json, sys
json.dump({"source_host": "$PROD", "source_backup": "$bk_path", "backup_completed_utc": "$stamp",
           "bytes": $local_bytes, "sha256_as_streamed": "$local_sha", "copied_at_utc": "$(date -u +%FT%TZ)",
           "verify": json.loads('''$report'''), "note": "Redacted copies differ from the source hash by design."},
          open(sys.argv[1], "w"), indent=2)
PY
chown root:"$DEV_GROUP" "$IMPORT_DIR/arbitredge_prod_copy_$stamp.json"; chmod 0440 "$IMPORT_DIR/arbitredge_prod_copy_$stamp.json"
echo "DONE: $final (root:$DEV_GROUP 0440, read-only to the dev user)"
