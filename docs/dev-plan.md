# Streetwise — Development Plan

Build in this exact sequence. Each step produces something manually validatable before proceeding.

| Step | Module | Done when… |
|---|---|---|
| 1 | `backend/store.py` | SQLite DB creates cleanly, `events` table exists, cleanup runs without error |
| 2 | `backend/fetchers/cta.py` | Raw CTA alerts print to console as Python dicts |
| 3 | `backend/extractor.py` | Claude returns valid JSON array from CTA batch — inspect for accuracy |
| 4 | `backend/geocoder.py` | `location_string` from step 3 resolves to correct lat/lng via Nominatim |
| 5 | `backend/scorer.py` | CTA event scores ≥ 0.6, solo Reddit post scores ≤ 0.55 |
| 6 | `backend/pipeline.py` | Full CTA cycle writes events to SQLite — query DB to verify |
| 7 | `backend/fetchers/metra.py` | Add Metra to pipeline. Verify scores and corroboration logic. |
| 8 | `backend/fetchers/reddit.py` | Add Reddit. Verify keyword filter reduces noise before Claude call. |
| 9 | `backend/main.py` | FastAPI serves `/events` JSON and `/events/stream` SSE |
| 10 | `frontend/` | Leaflet map shows markers, popups work, SSE updates in real time |

## Dependencies (`requirements.txt`)
```
fastapi
uvicorn[standard]
anthropic
praw           # Reddit API
feedparser     # Metra RSS
httpx          # Async HTTP for Nominatim + CTA
pydantic
python-dotenv
apscheduler    # 5-minute poll scheduler
```

## Environment Variables (`.env`)
```
ANTHROPIC_API_KEY=sk-ant-...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=streetwise/1.0 by u/yourusername
```

## Current Status
Not started. Next up: Step 1 — `backend/store.py`.
