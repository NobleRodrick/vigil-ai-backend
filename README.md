# VIGIL-AI Cameroun — Backend

National Cybersecurity AI Detection Platform — backend API.
Built for the 5th Digital Innovation Week 2026, MINPOSTEL Cameroun.

**100% free stack** — no paid services required to run the MVP.

---

## Stack

| Layer | Technology | Cost |
|---|---|---|
| API Framework | FastAPI 0.115 (Python 3.12) | Free |
| Database | PostgreSQL 16 | Free |
| Cache / Queue | Redis 7.2 | Free |
| Task Queue | Celery 5.4 | Free |
| AI Text Detection | Google Gemini API (`gemini-2.0-flash`) | Free tier |
| AI Image/Deepfake Detection | Google Gemini API (vision, same model) | Free tier |
| AI Audio Detection | Google Gemini API (native audio understanding) + heuristics | Free tier |
| File Storage | Local filesystem (Docker volume) | Free |
| Email (dev) | MailHog (catches emails locally) | Free |
| Task Monitoring | Flower | Free |
| Auth | JWT (HS256) + bcrypt | Free |

No paid AI API is required to run the MVP end-to-end. If you leave `GEMINI_API_KEY` blank, every detector automatically falls back to a built-in heuristic analyzer so the whole pipeline still works — just with lower accuracy.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + Docker Compose (v2)
- That's it — Python, PostgreSQL, and Redis all run inside containers.

(If you prefer running natively without Docker, you'll also need Python 3.12+, PostgreSQL 16, and Redis 7 installed locally.)

---

## Quick Start (Docker — recommended)

```bash
# 1. Clone / unzip the project, then enter it
cd vigil-ai-backend

# 2. Copy environment template and edit values
cp .env.example .env

# 3. (Optional but recommended) Get a FREE Gemini API key
#    → https://aistudio.google.com/apikey → "Create API key"
#    Paste it into .env as GEMINI_API_KEY=xxxxx
#    Without a key, the system still works using local heuristic detectors.

# 4. Build and start everything
docker-compose up -d --build

# 5. Run database migrations
docker-compose exec api alembic upgrade head

# 6. Seed the database (creates admin + demo accounts)
docker-compose exec api python scripts/seed_db.py --with-demo-data
```

Your backend is now running:

| Service | URL |
|---|---|
| API + Swagger Docs | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| Health check | http://localhost:8000/health |
| Celery Flower (task monitor) | http://localhost:5555 |
| MailHog (catches dev emails) | http://localhost:8025 |

### Default login credentials (created by the seed script)

| Role | Email | Password |
|---|---|---|
| Admin | `admin@vigilai.cm` | `VigilAdmin2026!` |
| Analyst (demo) | `analyst@antic.cm` | `AnalystPass2026!` |
| Viewer (demo) | `viewer@minpostel.cm` | `ViewerPass2026!` |

**Change the admin password immediately** via `POST /api/v1/auth/change-password` after first login.

---

## Quick Start (without Docker)

```bash
# 1. Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start PostgreSQL and Redis locally (must be running on default ports)
#    macOS: brew services start postgresql@16 redis
#    Ubuntu: sudo systemctl start postgresql redis-server

# 4. Create the database
createdb vigilai_db

# 5. Copy and edit environment file
cp .env.example .env
# Edit DATABASE_URL, REDIS_URL etc. if your local setup differs from defaults

# 6. Run migrations
alembic upgrade head

# 7. Seed the database
python scripts/seed_db.py --with-demo-data

# 8. Start the API server
uvicorn app.main:app --reload

# 9. In a separate terminal — start the Celery worker (required for AI analysis to run)
celery -A app.workers.celery_app worker --loglevel=info -Q analysis,alerts,default

# 10. (Optional) In a third terminal — start Celery Beat for scheduled tasks
celery -A app.workers.celery_app beat --loglevel=info
```

---

## Testing the API

### 1. Log in

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "analyst@antic.cm", "password": "AnalystPass2026!"}'
```

Copy the `access_token` from the response.

### 2. Submit text for AI analysis

```bash
curl -X POST http://localhost:8000/api/v1/submissions/text \
  -H "Authorization: Bearer <YOUR_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "content_text": "Il convient de noter que cette annonce gouvernementale représente une avancée majeure. En conclusion, il est essentiel de souligner les bénéfices considérables pour la population.",
    "language": "fr"
  }'
```

You'll get back a `case_number` (e.g. `VIGIL-2026-00001`) immediately — analysis runs asynchronously in the Celery worker.

### 3. Check the result

```bash
curl http://localhost:8000/api/v1/submissions/<submission_id> \
  -H "Authorization: Bearer <YOUR_ACCESS_TOKEN>"
```

### 4. View the dashboard

```bash
curl http://localhost:8000/api/v1/analytics/overview \
  -H "Authorization: Bearer <YOUR_ACCESS_TOKEN>"
```

### 5. Real-time notifications (WebSocket)

Connect to `ws://localhost:8000/ws/notifications?token=<YOUR_ACCESS_TOKEN>` to receive a push the moment an analysis completes — no polling required.

---

## Running Tests

```bash
# Create a test database first
docker-compose exec db createdb -U vigilai vigilai_db_test
# or locally: createdb vigilai_db_test

# Run the full test suite with coverage
pytest --cov=app --cov-report=term-missing

# Run a specific test file
pytest tests/test_auth.py -v
```

Target: **≥70% backend coverage** (enforced in `pyproject.toml`).

---

## Project Structure

```
vigil-ai-backend/
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── config.py                # Settings (env vars)
│   ├── database.py              # Async SQLAlchemy engine/session
│   ├── models/                  # SQLAlchemy ORM models (9 tables)
│   ├── schemas/                 # Pydantic request/response schemas
│   ├── core/
│   │   ├── security.py          # JWT, bcrypt, password reset tokens
│   │   ├── exceptions.py        # Custom exceptions + handlers
│   │   └── middleware.py        # Logging, security headers
│   ├── api/
│   │   ├── deps.py              # Auth, RBAC, pagination dependencies
│   │   └── v1/                  # All REST endpoints + WebSocket
│   ├── ai/
│   │   └── engine.py            # Text/Image/Video/Audio detectors
│   ├── workers/
│   │   ├── celery_app.py        # Celery configuration
│   │   └── tasks.py             # Async analysis, alerts, reports
│   └── services/
│       └── storage_service.py   # File upload validation & storage
├── migrations/                  # Alembic database migrations
├── scripts/
│   └── seed_db.py               # Creates roles + admin user
├── tests/                       # pytest test suite
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md  (this file)
```

---

## API Overview

Full interactive documentation is auto-generated at **`/docs`**. Summary:

| Method | Endpoint | Role | Description |
|---|---|---|---|
| POST | `/api/v1/auth/login` | Public | Get JWT tokens |
| POST | `/api/v1/auth/refresh` | Public | Refresh access token |
| GET | `/api/v1/auth/me` | Any | Current user profile |
| POST | `/api/v1/submissions/text` | Analyst | Submit text for AI analysis |
| POST | `/api/v1/submissions/image` | Analyst | Upload image for deepfake check |
| POST | `/api/v1/submissions/video` | Analyst | Submit video URL for analysis |
| POST | `/api/v1/submissions/audio` | Analyst | Upload audio for voice-clone check |
| GET | `/api/v1/cases` | Any | List cases (filterable) |
| GET | `/api/v1/cases/{id}` | Any | Full case detail |
| PATCH | `/api/v1/cases/{id}/status` | Analyst | Move case through workflow |
| POST | `/api/v1/cases/{id}/notes` | Analyst | Add investigation note |
| POST | `/api/v1/cases/{id}/escalate` | Analyst | Escalate to Admin |
| GET | `/api/v1/cases/export/csv` | Admin | Export cases to CSV |
| GET | `/api/v1/analytics/overview` | Any | Dashboard KPIs |
| GET | `/api/v1/analytics/timeline` | Any | 30-day threat trend |
| GET | `/api/v1/users` | Admin | Manage user accounts |
| WS | `/ws/notifications` | Any | Real-time push notifications |

---

## Switching to Real Email Delivery

By default, emails print to the console (and are visible at http://localhost:8025 via MailHog). To send real emails using a free Gmail account:

1. Enable 2FA on your Gmail account
2. Generate an "App Password" at https://myaccount.google.com/apppasswords
3. In `.env`, set:
   ```
   EMAIL_ENABLED=True
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-gmail@gmail.com
   SMTP_PASSWORD=<app-password>
   SMTP_USE_TLS=True
   ```

---

## Common Issues

**"Connection refused" to PostgreSQL/Redis** → Make sure `docker-compose up -d` finished and containers are healthy: `docker-compose ps`

**Celery worker not picking up tasks** → Check `docker-compose logs celery_worker`. Make sure Redis is reachable.

**Gemini API returns 429** → You've hit the free-tier daily/per-minute quota (15 req/min, 1500 req/day). The engine automatically falls back to the heuristic analyzer — no action needed, just expect lower accuracy until quota resets.

**File upload fails with "Invalid file type"** → The server validates actual file content (via `python-magic`), not just the extension. Make sure the file isn't corrupted.

---

## Next Steps

Once you've confirmed the backend works on your machine, we'll move on to building the React frontend (Phase 2) that connects to this API.
