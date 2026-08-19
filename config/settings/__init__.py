"""Settings package.

Modules
-------
base : shared settings for every environment
dev  : local development (DEBUG, SQLite, console e-mail)
prod : production hardening (PostgreSQL, secure cookies, HSTS)
test : fast test settings (in-memory DB, no migrations for speed)
"""
