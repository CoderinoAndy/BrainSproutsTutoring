# BrainSprouts Tutor Management

A small FastAPI and SQLite project for learning how a tutor dashboard works.

## Run locally

```powershell
pip install -r requirements.txt
python -m uvicorn server:app --reload --port 5000
```

Open http://localhost:5000. FastAPI's interactive API documentation is at
http://localhost:5000/docs.

## Deploy on Render

The app uses local SQLite for development and PostgreSQL whenever
`DATABASE_URL` is set. A Render deployment must connect the existing
PostgreSQL database with a `DATABASE_URL` environment variable; the app will
refuse to start with Render's ephemeral SQLite storage.

After deploying, confirm the startup log says `Database ready: PostgreSQL`.
