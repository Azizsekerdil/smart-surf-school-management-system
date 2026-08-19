"""Backup & restore: the module that decides whether a bad day is survivable.

A surf school's database holds waivers, medical notes, incident reports and the
money trail. Losing it is not an inconvenience, it is a legal problem. This app
therefore does three things and does them carefully:

* it produces a **consistent** copy (the SQLite backup API, not a file copy;
  ``pg_dump`` custom format, not a text dump),
* it **proves** the copy is intact (SHA-256 plus an engine-level integrity
  check), and
* it makes restoring **deliberately hard** — verify, type the backup code, hold
  ``backups.restore``, and take an automatic safety backup first.

Nothing here trusts a file just because a row says it exists.
"""
