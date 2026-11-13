# Recruitment Management Frontend

Next.js dashboard for the Recruitment Management API.

## Run locally

From `frontend`, install dependencies and create a local environment file:

```powershell
npm install
Copy-Item .env.example .env.local
```

Set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` to the FastAPI URL, then run:

```powershell
npm run dev
```

Start the backend separately from `backend` with `python -m uvicorn app.main:app --reload`.

## Verify

```powershell
npm run lint
npm run typecheck
npm run build
```
