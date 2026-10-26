# V3 production disk: plan (no backups deleted, collector never stopped)

**Status:** plan only. Nothing has been run on production. Every step below needs your go-ahead, and the first step is read-only.

## What is probably happening

- **Facts:**
  - The disk is 24 GB, about 98% used, so roughly 0.5 GB is free.
  - The live database is about 8.6 GB.
  - V3's collector makes a full, integrity-checked copy of the database every 24 hours into `/opt/arbitredge/db/backups/`.
  - V3 deletes old backups only *after* a new one succeeds (`backup/backup_db.py` retention, per its tests).
- **Consequence (inferred; the survey will confirm):**
  - A new backup needs about 8.6 GB free, so **daily backups are almost certainly failing now**.
  - Because retention runs only after a success, the old backups are never rotated. The disk stays stuck near full.
  - Your newest valid backup may be days old.
- **Risk:** when the disk reaches 100%, SQLite rejects writes (`SQLITE_FULL`). The database does not corrupt, but the **collector stops saving odds** until space is freed. A failing backup attempt can use up the last free space on its own. Database growth will eventually do the same.

## Step 0: survey (read-only, about 2–5 minutes, safe at any time)

```bash
ssh root@143.244.160.20 'bash -s' < prod_readonly/prod_disk_survey.sh > survey.txt
```

The survey reports:
- what fills the disk and the backup count, sizes and dates;
- the last 30 backup-log lines;
- database growth, and space that can be regenerated (logs, package cache, old kernels);
- the collector schedule.

It writes nothing, opens no database and restarts nothing. Secret values are redacted (tested). **Paste `survey.txt` back to me.** I will turn the plan below into exact commands sized to the real numbers.

## Step 1: add storage without downtime (recommended)

Attach a **DigitalOcean Block Storage Volume** to the production droplet:
- Volumes **hot-attach**: no reboot, no service restart.
- Size: about 100 GB (about **$10/month** at $0.10/GB). It can be enlarged later, online.
- Choose the same region as the droplet and "Automatically format & mount" (ext4).
- **Not chosen:** resizing the droplet disk, which requires powering the droplet off and would interrupt the collector.

## Step 2: preserve every existing backup on the volume

1. Copy (not move) every existing backup to `/mnt/<volume>/archive_<date>/`.
   - Use the same low-priority, page-cache-friendly reader as the V4 transfer: nice 19, idle I/O, rate-limited, chunks dropped from cache. The 1 GB server's live database and website stay responsive.
   - Verify each copy with SHA-256 against the original.
2. This archive folder sits **outside V3's retention path**, so V3's own retention can never delete these copies.

## Step 3: send future backups to the volume, with no code change and no restart

1. Create `/mnt/<volume>/arbitredge_backups/` holding verified copies of the current backups.
2. Between collector runs, never during a backup, bind-mount it over the old path:
   `mount --bind /mnt/<volume>/arbitredge_backups /opt/arbitredge/db/backups`, plus an `/etc/fstab` entry.
3. V3 keeps writing to the same path, and the bytes now land on the volume.
4. Rollback is `umount /opt/arbitredge/db/backups`, which instantly re-exposes the original directory.

## Step 4: free root-disk space (choose one)

- **Option A: relocate (recommended).**
  - What it does: after Steps 2–3 are verified, remove the **root-disk duplicates** of backups that exist, hash-verified, in two places on the volume (archive + active).
  - Space recovered: roughly the size of the current backups.
  - Backups preserved? Every backup still exists. This is a move, and it needs your explicit yes, file by file if you prefer.
- **Option B: strict no-removal.**
  - What it does: keep the root-disk copies. Steps 1–3 stop backups from consuming root disk. You can also approve clearing regenerable files only: old system journal (`journalctl --vacuum-size=200M`), the apt package cache (`apt-get clean`), and old kernels.
  - Space recovered: typically 0.5–3 GB (the survey will say). Headroom stays thin, and DB growth will hit the limit again.
  - Backups preserved? No backup is touched.

## Step 5: keep it from recurring

- A daily disk-usage and backup-freshness alert (warn at 85%, and when the newest backup is more than 26 hours old). This is a small production change; I will propose it separately for approval.
- Optional off-server copy of backups (DigitalOcean Spaces, about $5/month for 250 GB) to protect against losing the droplet.

## Explicitly not done

- No database file is moved.
- No service is stopped.
- No collector or website restart.
- No credentials are changed.
- No step is run without your approval.
