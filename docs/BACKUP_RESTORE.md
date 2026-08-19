# Backup & Restore

A surf school's database holds every booking, payment and customer record it has.
Losing it ends the business, and restoring the *wrong* thing over a good database
is just as bad. This module is built around both risks.

---

## 1. What gets backed up

| Scope | Contents |
|---|---|
| `database` | The full database |
| `media` | Uploaded files — photos, waivers, certificates, receipts (excluding `media/private/`) |
| `config` | A manifest of configuration **keys** — never their values |
| `full` | All three |

**Secrets are never backed up.** A `full` backup records that
`NVIDIA_API_KEY` exists; it never records what it is. A leaked backup therefore
cannot leak a credential.

---

## 2. How the database is captured

**SQLite** uses the `sqlite3` backup API (`conn.backup(dest)`), not a file copy.
This matters: copying a live SQLite file while a write is in flight produces a
corrupt backup. The backup API takes a consistent snapshot of a running database.

**PostgreSQL** uses `pg_dump` in custom format, invoked with an argument list and
`shell=False`. The password is passed through the `PGPASSWORD` environment
variable, never on the command line — command lines are visible to every other
process on the machine.

If `pg_dump` is not on `PATH`, set `PG_DUMP_PATH` in `.env`. The failure message
says exactly that rather than a generic error.

---

## 3. Integrity

Every backup records a **SHA-256 checksum** of the file at creation.

`verify_backup()` re-computes it, confirms the file still exists, and inspects the
content: for SQLite it runs `PRAGMA integrity_check` against the copy; for a zip
it tests the archive. A backup that fails verification is marked `CORRUPT` and
cannot be restored.

Verify on demand from the backup detail screen, or automatically with
`.\scripts\backup.ps1 -Verify`.

---

## 4. Creating a backup

**In the application:** Backup & Restore → *Create backup now*, choose a scope.

**Command line:**
```powershell
.\scripts\backup.ps1                          # full backup
.\scripts\backup.ps1 -Scope database
.\scripts\backup.ps1 -Type daily -Verify
```

**Scheduled, without Celery or Redis** — the normal Windows setup:
```powershell
schtasks /Create /TN "SurfSchool Daily Backup" `
  /TR "powershell.exe -ExecutionPolicy Bypass -File D:\Surf_School\scripts\backup.ps1 -Type daily -Verify" `
  /SC DAILY /ST 03:00
```

**With Celery**, if Redis is available, `config/celery.py` already schedules a
daily backup at 03:00 and a weekly one on Sunday at 03:30.

---

## 5. Restoring — deliberately difficult

Restoring overwrites live data, so the flow is designed with friction:

1. **Verify.** The checksum must match. A corrupt or missing file is refused
   outright.
2. **Confirm by typing.** The operator must type the backup code exactly
   (e.g. `BKP-20260815-001`). A misclick cannot trigger a restore.
3. **Check the capability.** `backups.restore` is a privileged capability granted
   to Super Admin only by default.
4. **Take a safety backup first.** A `PRE_RESTORE` backup of the *current* state
   is created and linked to the restore record — before anything is overwritten.
5. **Restore.**
6. **Roll back on failure.** If the restore fails at any point, the safety backup
   is restored and the record is marked `ROLLED_BACK`.

Every step writes an audit entry. `RestoreRecord` keeps who confirmed it, what
they typed, the pre-flight checks, timing and the outcome.

> Restore is the one operation that can destroy data. If you are unsure, take a
> manual backup first — step 4 does it for you, but a second copy costs nothing.

---

## 6. Retention

| Setting | Default | Meaning |
|---|---|---|
| `BACKUP_RETENTION_DAILY` | 7 | Daily backups kept |
| `BACKUP_RETENTION_WEEKLY` | 4 | Weekly backups kept |
| `BACKUP_RETENTION_MONTHLY` | 12 | Monthly backups kept |

`apply_retention_policy()` deletes what falls outside the window, with two
absolute exceptions: **a manual backup is never auto-deleted**, and **the most
recent successful backup is never deleted** regardless of age. A retention policy
that can leave you with nothing is not a retention policy.

```powershell
.\scripts\backup.ps1 -ApplyRetention
```

---

## 7. Where backups live

`BACKUP_ROOT` in `.env`, defaulting to `D:\Surf_School\backups\`. That directory
is git-ignored.

**Move them off the machine.** A backup on the same disk as the database protects
against a bad migration, not against a failed drive, ransomware or a stolen
laptop. Sync `backups/` to external storage — and remember it contains full
customer personal data, so encrypt it at rest.

---

## 8. Disaster recovery

**The database is corrupt but the machine is fine**
1. Backup & Restore → pick the most recent verified backup → restore.

**The machine is gone**
1. Install Python, Node and Git on the replacement.
2. Clone the repository, run `.\scripts\setup.ps1`.
3. Copy `backups/` back into place.
4. Recreate `.env` from `.env.example` and re-enter the credentials — they were
   deliberately never in the backup.
5. Restore the most recent verified backup.

**Moving from SQLite to PostgreSQL**
1. `python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission > transfer.json`
2. Point `DATABASE_URL` at PostgreSQL.
3. `python manage.py migrate`
4. `python manage.py loaddata transfer.json`

A backup file is **not** portable between engines — a SQLite snapshot cannot be
restored into PostgreSQL. Use `dumpdata`/`loaddata` for that migration.

---

## 9. Testing your backups

An untested backup is a hope, not a plan. Quarterly:

1. Create a fresh backup.
2. Verify it.
3. Restore it into a **separate** checkout with its own database.
4. Confirm recent bookings and payments are present.

The backup test in the suite does exactly this round-trip — create, verify,
restore, compare — against a temporary database.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "pg_dump not found" | Not on `PATH` | Set `PG_DUMP_PATH` in `.env` |
| Backup marked `CORRUPT` | Checksum mismatch or a failed integrity check | Do not restore it. Investigate the storage; take a fresh backup |
| "Confirmation text does not match" | The typed code differs from the backup code | Copy it exactly, including the date part |
| Restore rolled back | The restore failed and the safety backup was reinstated | Read `RestoreRecord.error_message`; the system is back in its prior state |
| Backups directory growing | Retention not scheduled | Add `-ApplyRetention` to your scheduled task |
| Scheduled backup silently not running | Task Scheduler misconfigured | Check `logs/backup.log` — failures are recorded there |
