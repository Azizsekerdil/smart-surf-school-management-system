# Deployment

Three realistic targets, in the order a surf school actually grows into them.

---

## 1. Single machine at the school (recommended start)

One Windows PC behind the counter. No cloud, no monthly bill, no dependency on
the beach's internet connection.

### 1.1 Prepare

```powershell
cd D:\Surf_School
.\scripts\setup.ps1
.\.venv\Scripts\python.exe manage.py createsuperuser
```

### 1.2 Use a real WSGI server

`runserver` is a development tool — single-threaded, no static-file caching, and
it restarts on file changes. Use Waitress, which is pure Python and works well on
Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install waitress
.\.venv\Scripts\python.exe -m waitress --listen=0.0.0.0:8000 config.wsgi:application
```

### 1.3 Settings

In `.env`:

```ini
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<50+ random characters>
DJANGO_ALLOWED_HOSTS=surf-desk,192.168.1.50
DJANGO_CSRF_TRUSTED_ORIGINS=http://surf-desk:8000,http://192.168.1.50:8000
# On a LAN without TLS these must stay False, or you will lock yourself out:
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
AI_TERMINAL_ENABLED=False
```

Generate the key with:

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

`prod.py` refuses to start with a weak key or a wildcard `ALLOWED_HOSTS` — that
is intentional, not an obstacle.

### 1.4 Static files

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

WhiteNoise serves them from the same process; no separate web server is needed.

### 1.5 Run as a service

So it survives a reboot and does not depend on someone being logged in:

```powershell
schtasks /Create /TN "SurfSchool" `
  /TR "D:\Surf_School\.venv\Scripts\python.exe -m waitress --listen=0.0.0.0:8000 config.wsgi:application" `
  /SC ONSTART /RU SYSTEM /RL HIGHEST
```

For proper service semantics (auto-restart, logging), use
[NSSM](https://nssm.cc/) instead.

### 1.6 Firewall

```powershell
New-NetFirewallRule -DisplayName "Surf School" -Direction Inbound `
  -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

Use `-Profile Private` only. Do not expose port 8000 to the internet without TLS
and a reverse proxy.

### 1.7 Scheduled work

```powershell
schtasks /Create /TN "SurfSchool Backup" /SC DAILY /ST 03:00 `
  /TR "powershell.exe -ExecutionPolicy Bypass -File D:\Surf_School\scripts\backup.ps1 -Type daily -Verify"

schtasks /Create /TN "SurfSchool Conditions" /SC MINUTE /MO 30 `
  /TR "D:\Surf_School\.venv\Scripts\python.exe D:\Surf_School\manage.py refresh_conditions"
```

No Celery or Redis required.

---

## 2. Single machine with PostgreSQL

Move to PostgreSQL when more than a few people use the system at once, or when
the data matters enough to want Postgres' durability and tooling.

```powershell
winget install PostgreSQL.PostgreSQL.17
```

```sql
CREATE DATABASE surf_school ENCODING 'UTF8';
CREATE USER surf_user WITH PASSWORD 'a-strong-password';
GRANT ALL PRIVILEGES ON DATABASE surf_school TO surf_user;
ALTER DATABASE surf_school OWNER TO surf_user;
```

```ini
DATABASE_URL=postgres://surf_user:a-strong-password@localhost:5432/surf_school
DATABASE_CONN_MAX_AGE=60
```

Migrate the existing data — **a SQLite backup file cannot be restored into
PostgreSQL**, so use Django's serialisation:

```powershell
# with DATABASE_URL still pointing at SQLite
.\.venv\Scripts\python.exe manage.py dumpdata --natural-foreign --natural-primary `
  -e contenttypes -e auth.Permission -e sessions --indent 2 > transfer.json

# switch DATABASE_URL to PostgreSQL, then
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata transfer.json
```

Verify before deleting anything:

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from apps.bookings.models import Booking; print(Booking.objects.count())"
```

Set `PG_DUMP_PATH` in `.env` if `pg_dump` is not on `PATH`, so backups keep working.

---

## 3. Internet-facing deployment

Needed only if customers book online or staff work remotely.

### 3.1 Shape

```
Internet → Nginx (TLS termination) → Waitress/Gunicorn → Django
                                   → PostgreSQL
                                   → Redis (cache + Celery)
```

### 3.2 Settings

```ini
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=app.yoursurfschool.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://app.yoursurfschool.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_TASK_ALWAYS_EAGER=False
AI_TERMINAL_ENABLED=False
```

### 3.3 Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name app.yoursurfschool.com;

    ssl_certificate     /etc/letsencrypt/live/app.yoursurfschool.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.yoursurfschool.com/privkey.pem;

    client_max_body_size 12M;   # matches MAX_UPLOAD_SIZE_BYTES

    location /static/ { alias /srv/surf_school/staticfiles/; expires 30d; }
    location /media/  { alias /srv/surf_school/media/; expires 7d; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # required by SECURE_PROXY_SSL_HEADER
        proxy_read_timeout 180s;                      # AI responses can be slow
    }
}
server { listen 80; server_name app.yoursurfschool.com; return 301 https://$host$request_uri; }
```

`media/private/` must **not** be served by Nginx — it holds confidential
documents and is delivered through Django so permissions are enforced.

### 3.4 Celery

```bash
celery -A config worker -l info
celery -A config beat -l info
```

On Windows, add `--pool=solo`.

---

## 4. Pre-flight checklist

```powershell
.\.venv\Scripts\python.exe manage.py check --deploy --settings=config.settings.prod
```

- [ ] `DJANGO_DEBUG=False`
- [ ] `DJANGO_SECRET_KEY` is 50+ random characters and is not the development default
- [ ] `DJANGO_ALLOWED_HOSTS` lists explicit hostnames, no `*`
- [ ] `.env` is not tracked by git (`git check-ignore .env` prints `.env`)
- [ ] `collectstatic` has run
- [ ] Migrations applied
- [ ] `bootstrap_roles` has run
- [ ] `i18n_compile` has run
- [ ] A superuser exists and no demo accounts remain
- [ ] Backups scheduled **and a restore tested**
- [ ] `AI_TERMINAL_ENABLED=False`
- [ ] TLS in front of any internet-facing deployment
- [ ] Open-Meteo commercial terms reviewed (see `docs/OPEN_SOURCE_LICENSES.md`)

---

## 5. Upgrading

```powershell
.\scripts\backup.ps1                              # always first
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install; npm run build
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py i18n_compile
.\.venv\Scripts\python.exe manage.py bootstrap_roles
# restart the service
```

Review migrations before applying them in production:

```powershell
.\.venv\Scripts\python.exe manage.py showmigrations
.\.venv\Scripts\python.exe manage.py sqlmigrate <app> <number>
```

---

## 6. Monitoring

| Signal | Where |
|---|---|
| Application health | `GET /api/health/` — 200 healthy, 503 unhealthy |
| Errors | `logs/surf_school.log` |
| Security events | `logs/security.log` (JSON) |
| AI usage and cost | `logs/ai.log` and the AI Usage screen |
| Audit trail | `/audit/` in the application |
| Backup outcomes | `logs/backup.log` and the Backup screen |

`/api/health/` is safe to expose to a load balancer: anonymous callers get only
`{"status": …, "version": …}`; component detail requires authentication.

---

## 7. Data protection

The system holds names, contact details, birth dates, **medical notes**,
emergency contacts and payment history. Wherever you deploy it:

* restrict who holds roles with `customers.export` and `finance.view`;
* encrypt backups at rest — they contain everything;
* set a retention policy for former customers;
* remember `marketing_consent` is recorded per customer and exports flag it;
* the audit log tells you who accessed what, which is what a data-protection
  enquiry will ask for.
