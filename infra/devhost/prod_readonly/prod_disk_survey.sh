#!/usr/bin/env bash
# =============================================================================
# ArbitrEdge V3 production — disk survey. STRICTLY READ-ONLY.
#
#   ssh root@143.244.160.20 'bash -s' < prod_disk_survey.sh > survey.txt
#
# Writes nothing on the server (no temp files, no logs, no package installs),
# opens no database, restarts nothing. Runs at nice 19 / idle I/O priority.
# Output is redacted (anything shaped like KEY=/TOKEN=/SECRET=/PASS= values,
# apiKey= query values) so it can be pasted back for analysis.
# =============================================================================
set -u
exec 2>&1
renice -n 19 $$ >/dev/null 2>&1; ionice -c 3 -p $$ >/dev/null 2>&1
APP="${APP:-/opt/arbitredge}"
redact() { sed -E 's/((KEY|TOKEN|SECRET|PASS(WORD)?|PWD|CREDENTIAL|AUTH)[A-Za-z_]*=)[^ "'"'"']+/\1[REDACTED]/Ig; s/(apiKey=)[A-Za-z0-9]+/\1[REDACTED]/g'; }
sec() { printf '\n### %s\n' "$*"; }

sec "time / host"
date -u; uptime; uname -r

sec "filesystems"
df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null
df -i / | tail -1
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null

sec "memory"
free -m; swapon --show 2>/dev/null

sec "largest top-level directories on / (same filesystem only)"
timeout 300 du -xh --max-depth=2 / 2>/dev/null | sort -rh | head -25

sec "application tree $APP"
timeout 120 du -xh --max-depth=2 "$APP" 2>/dev/null | sort -rh | head -20

sec "live database files (size only; not opened)"
ls -la --time-style=+%Y-%m-%dT%H:%M "$APP"/db/*.db* 2>/dev/null
for f in "$APP"/db/arbitredge.db; do
  [ -f "$f" ] && printf 'journal mode header bytes 18-19: %s (2 2 = WAL, 1 1 = rollback)\n' "$(od -An -tu1 -j18 -N2 "$f" | xargs)"
done

sec "backups ($APP/db/backups)"
ls -la --time-style=+%Y-%m-%dT%H:%M "$APP"/db/backups 2>/dev/null | tail -40
printf 'backup count: %s   total: %s\n' "$(ls "$APP"/db/backups/arbitredge_backup_*.db 2>/dev/null | wc -l)" \
  "$(du -ch "$APP"/db/backups/arbitredge_backup_*.db 2>/dev/null | tail -1 | cut -f1)"
sec "backup log (last 30 lines, redacted)"
tail -n 30 "$APP"/db/backups/backup.log 2>/dev/null | redact

sec "other backup-like files anywhere on / (>100 MB)"
timeout 300 find / -xdev \( -path /proc -o -path /sys \) -prune -o -type f -size +100M \
  \( -name '*.db' -o -name '*.sqlite*' -o -name '*backup*' -o -name '*.bak' -o -name '*.tar*' -o -name '*.gz' -o -name '*.zst' \) \
  -printf '%s\t%TY-%Tm-%Td\t%p\n' 2>/dev/null | sort -rn | awk -F'\t' '{printf "%8.1f GB  %s  %s\n", $1/1073741824, $2, $3}' | head -30

sec "largest files on / (>200 MB, any type)"
timeout 300 find / -xdev \( -path /proc -o -path /sys \) -prune -o -type f -size +200M \
  -printf '%s\t%p\n' 2>/dev/null | sort -rn | awk -F'\t' '{printf "%8.1f GB  %s\n", $1/1073741824, $2}' | head -20

sec "regenerable space (NOT deleted by this script)"
journalctl --disk-usage 2>/dev/null
du -sh /var/log /var/cache/apt /var/lib/apt/lists /tmp /var/tmp /root/.cache /var/lib/snapd 2>/dev/null
for h in /home/*; do du -sh "$h/.cache" 2>/dev/null; done
printf 'installed kernels: '; dpkg -l 'linux-image-[0-9]*' 2>/dev/null | awk '/^ii/ {print $2}' | xargs; echo "running: $(uname -r)"
ls /var/crash 2>/dev/null | head

sec "collector / services schedule (redacted)"
crontab -l 2>/dev/null | grep -v '^#' | redact
ls /etc/cron.d 2>/dev/null
systemctl list-timers --all --no-pager 2>/dev/null | head -20
systemctl list-units --type=service --no-pager --no-legend 2>/dev/null | grep -Ei 'arbitredge|gunicorn|caddy|collect'

sec "database growth estimate from backup sizes"
ls -l --time-style=+%Y-%m-%d "$APP"/db/backups/arbitredge_backup_*.db 2>/dev/null | awk '{print $6, $5}' | sort | awk '
  NR==1 {d0=$1; s0=$2} {d=$1; s=$2}
  END { if (NR>1) printf "first %s %.2f GB -> last %s %.2f GB over %d backups\n", d0, s0/1073741824, d, s/1073741824, NR }'

sec "DigitalOcean region (metadata, local link only)"
curl -s -m 2 http://169.254.169.254/metadata/v1/region 2>/dev/null; echo

sec "end"
