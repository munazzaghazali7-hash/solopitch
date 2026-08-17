# SoloPitch — AI-powered CRM & Outreach Engine

A multi-agent AI system built with Google ADK that helps solopreneurs
identify cold leads, research their industries, draft personalized pitches,
and schedule follow-up calls — all from a single API call.

## Agent Architecture

```
Business Manager (root_agent)
└── outreach_workflow (SequentialAgent)
    ├── researcher   → finds cold leads + industry trends
    ├── drafter      → writes personalized pitches
    ├── scheduler    → appends meeting slots + updates DB
    └── analyst      → surfaces outreach patterns
```

## Setup

### 1. Clone and install

```bash
git clone <your-repo>
cd solopitch

uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Set up PostgreSQL

```bash
psql -U your_user -d your_db -f schema.sql
```

### 4. Get a Tavily API key (free)

Sign up at https://tavily.com and paste the key into `.env`.

### 5. Run locally

```bash
adk web solopitch
# Opens at http://localhost:8000
```

Try: *"Find cold leads and run outreach"*

## Deploy to Cloud Run

```bash
source .env

# Create service account
gcloud iam service-accounts create solopitch-sa \
  --display-name="SoloPitch Service Account"

# Grant Vertex AI access
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:solopitch-sa@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Deploy
uvx --from google-adk==1.14.0 \
  adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=us-central1 \
  --service_name=solopitch \
  --with_ui \
  . \
  -- \
  --service-account=solopitch-sa@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com
```

## API Usage

```bash
curl -X POST https://<your-cloud-run-url>/run \
  -H "Content-Type: application/json" \
  -d '{"message": "Find cold leads and run outreach"}'
```

## File Structure

```
solopitch/
├── agent.py          # All agents and tools
├── __init__.py
├── .env.example
├── requirements.txt
├── schema.sql        # PostgreSQL schema + seed data
├── Dockerfile
├── notes/
│   └── follow_up.txt # Brand voice template
└── README.md
```

## MCP Tools Used

| Tool | Purpose | Agent |
|------|---------|-------|
| PostgreSQL (psycopg2) | Lead queries + outreach logging | Researcher, Scheduler |
| Tavily Search API | Industry trend research | Researcher |
| Notes (filesystem) | Brand voice templates + pitch storage | Drafter |
| Calendar (mock → Google Calendar MCP) | Available slot generation | Scheduler |
