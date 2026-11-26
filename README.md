# AI Travel Concierge (CrewAI)

A university AI-course project: an **agentic** travel planner. Seven specialised
CrewAI agents collaborate — researching a destination, curating food, reading
the traveller's persona, recommending accommodation, budgeting, planning the
days, and reviewing the result — to produce a personalised, structured
itinerary. It ships with a
FastAPI backend, a Next.js chat frontend, PostgreSQL persistence, a ChromaDB
RAG knowledge base, and an
**automated evaluation harness** that compares the multi-agent crew against a
single-LLM baseline.

> No model is trained. The project uses hosted LLMs via API; the contribution is
> the **design and evaluation** of the multi-agent system.

---

## Architecture

```
Next.js chat ──> FastAPI /chat/stream (SSE) ──> intent + slot extraction (1 LLM call)
   │                                              │  ├─ off-topic  → streamed refusal
   │  streams: meta → tokens → done              │  ├─ missing?   → streamed clarifying
   │                                              │  │               question + suggestion chips
   │                                              │  └─ ready?     → TravelCrew (below)
   │
   └────────────────────────────> enqueued as a background job (Redis/arq) ──> TravelCrew
                                          Destination → Food → Personalization →
                                          Accommodation → Budget → Planner → Reviewer
                                            │ tools: Tavily web search, Google Places
                                            │ lookup, Travelpayouts flight price lookup,
                                            │ RAG retriever (Chroma), budget calc (code)
                                          structured Itinerary (+ source citations)
                                          → PostgreSQL + boarding-pass card
                                          → optional: PDF email / Telegram delivery
Evaluation harness ──> runs each scenario through baseline AND crew, scores both.
```

The crew runs in a background worker process, not inline in the HTTP request
— see "Background worker" in Setup below. This is what lets a plannable turn
survive closing the browser tab mid-run; the client reconnects to a resumable
SSE stream for live per-agent progress.

| Layer       | Tech                                                         |
| ----------- | ------------------------------------------------------------ |
| Agents      | CrewAI (sequential process)                                  |
| Tools / RAG | LangChain + ChromaDB + local sentence-transformers           |
| LLM         | Task-based routing through LiteLLM |
| API         | FastAPI                                                      |
| Persistence | PostgreSQL (SQLAlchemy)                                      |
| Frontend    | Next.js (App Router) + Tailwind CSS v4, streaming chat UI    |
| Evaluation  | pandas + LLM-as-judge                                        |

## The seven agents

| Agent                      | Tools                     | Responsibility                                      |
| -------------------------- | ------------------------- | --------------------------------------------------- |
| Destination Researcher     | RAG, web search           | attractions matched to interests                    |
| Food & Restaurant Curator  | web search, RAG           | restaurants per budget/taste                        |
| Personalization Specialist | RAG, web search           | traveller persona → prioritize/avoid guidance       |
| Accommodation Specialist   | web search, RAG           | 2-4 named lodging options matched to persona/budget |
| Budget Analyst             | deterministic budget tool | category costs + within-budget check                |
| Itinerary Planner          | RAG                       | day-by-day plan + transport                         |
| Quality Reviewer           | (reflection)              | fixes errors, emits final itinerary                 |

---

## Beyond the core crew

The evaluation harness (below) is the academic core, but the app itself has
grown past that into a small product:

- **Accounts** — email+password or Google OAuth; chat requires a signed-in
  account. Conversations, generations, and delivery preferences are scoped
  to the owner.
- **Grounded citations** — search/places/flight-price results are attached
  to the itinerary as real, clickable sources rather than asserted as prose;
  see `Itinerary.sources`.
- **PDF delivery** — a finished (or replanned) itinerary is automatically
  rendered as a boarding-pass PDF and sent to a verified email
  (Resend) and/or a linked Telegram chat, if the account opted in — a manual
  "email me this PDF" button exists too. Neither is required to use the app.
- **Agent memory (opt-in, off by default)** — recurring preferences from a
  traveller's own past trips (pace, cuisine, budget tier) can be remembered
  and injected into future plans; toggle and "delete my memory" both live in
  Settings.
- **Location-aware flight pricing (opt-in)** — sharing browser geolocation
  lets the Destination agent look up real flight prices from where the
  traveller actually is, instead of guessing an origin.

None of the above is required to run the evaluation harness, which calls the
crew/baseline directly and has no account or Redis dependency.

---

## Prerequisites

- **Python 3.11 or 3.12** (recommended). Avoid 3.14 for now — CrewAI, ChromaDB
  and PyTorch (pulled in by the embeddings model) may not yet ship wheels for
  it. On this machine, create the venv with `python3.12`.
- Node 18+
- Docker (for PostgreSQL) **or** a local Postgres
- Credentials for your selected model providers. The example file defaults to
  Vertex AI. It also includes a Groq and Cloudflare profile for local development.
  See [model setup](#switching-llm-provider).

For existing deployments, see the [security upgrade notes](docs/security-upgrade.md).

## Setup

### 1. Database + Redis

```bash
docker compose up -d db redis    # PostgreSQL on :5432, Redis on :6379
```

Redis backs the background job queue (below) — the crew no longer runs inline
inside the HTTP request, so this step is required, not optional.

### 2. Backend API

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp -n .env.example .env          # then configure your selected provider(s)
alembic upgrade head              # apply migrations

python -m app.rag.ingest         # build the RAG vector store (first run downloads
                                 # a small embeddings model, ~90 MB)

uvicorn app.main:app --reload    # API on http://localhost:8000  (docs at /docs)
```

### 3. Background worker

**Required** — a plannable turn only *enqueues* a job; nothing ever runs it
without this process. It's a separate long-lived process, not something
`uvicorn` starts for you:

```bash
cd backend
source .venv/bin/activate
arq app.queue.worker.WorkerSettings
```

Jobs enqueued while no worker is running aren't lost — they sit in Redis and
get picked up as soon as one starts — but nothing will visibly happen (the UI
will sit at "Researching your trip…" indefinitely) until this is running.

### 4. Frontend

```bash
cd frontend
cp -n .env.example .env          # NEXT_PUBLIC_API_URL defaults to http://localhost:8000
npm install
npm run dev                      # Next.js UI on http://localhost:5173
```

Open http://localhost:5173. **An account is required to chat at all** — the
first thing you'll see is a sign-in/register prompt (email+password, or
Google if `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` are set in
`backend/.env`; see that file's comments for the Google Cloud Console setup).
Once signed in, tell Bedrock where you'd like to go — it streams a reply,
asks for anything missing (with tappable suggestion chips), then — once it
has a destination, dates, and a budget — enqueues the **CrewAI multi-agent**
crew and shows live per-agent progress until the itinerary comes back as a
boarding-pass card. (The single-LLM baseline still lives behind
`POST /itineraries?system=baseline` and the evaluation harness below, and
runs inline since it's a single LLM call, not a background job.)

---

If those ports are occupied, map each Docker service to an available host port
and update `DATABASE_URL` and `REDIS_URL` in `backend/.env`. Use the same
settings in the API and worker. To run the frontend on another port, use
`npx next dev -p 5174` and add its origin to `ALLOWED_ORIGINS` in
`backend/.env`. Set `FRONTEND_URL` to that origin if you use Google sign-in.

Chat text arrives over server-sent events. The UI paces incoming text so a
burst of tokens can still appear progressively. It follows the browser’s
reduced-motion preference. When chat has the required trip details, the API
hands the request to the worker and the UI shows live itinerary progress.

## Evaluation

```bash
cd backend
python -m evaluation.run_eval                 # all scenarios, both systems
python -m evaluation.run_eval --limit 2       # quick smoke run
python -m evaluation.run_eval --no-judge      # skip LLM-as-judge metrics
python -m evaluation.run_eval --runs 3        # repeat 3x -> mean ± std (variance)
python -m evaluation.run_eval --rag-ablation  # add a retrieval-disabled crew
python -m evaluation.run_eval --hierarchical  # add a manager-delegation crew
python -m evaluation.reviewer_probe           # Reviewer Detection Rate probe
```

Outputs `backend/evaluation/results.csv` and `results.md` (paste the tables into
the report). Metrics: task completion, constraint satisfaction, budget accuracy,
feasibility, relevance, hallucination count, response time, token usage, and a
user-satisfaction proxy.

**Controlled experiments (added to address the report's validity threats):**

- `--runs N` repeats every _(scenario, system)_ N times and reports each metric
  as **mean ± std**, so the LLM-judged comparisons carry a variance estimate
  instead of resting on a single run.
- `--rag-ablation` adds a **`crew_norag`** control — the identical crew with the
  RAG `knowledge_base` tool withheld from every agent — to isolate the knowledge
  base's contribution.
- `--hierarchical` adds a **`crew_hier`** control that swaps the sequential
  pipeline for CrewAI's manager-delegation topology (`Process.hierarchical`), so
  you can compare topologies head-to-head. It is opt-in because it issues extra
  manager LLM calls and is non-deterministic (see the report's §6.3 rationale
  for why sequential is the default).

The scenario set now spans **10** trips (the original six plus a family of four,
a longer high-altitude trek, and southern-hemisphere / North-African
destinations) for a more diverse sample.

---

## Switching LLM provider

Use [`backend/.env.example`](backend/.env.example) as a guide for setting model
IDs and credentials in `backend/.env`, then restart the API and worker.
`MODEL` supplies the default model. Use `TIER_CHEAP`, `TIER_STANDARD`, `TIER_QUALITY`, and
`TASK_TIERS` to assign different models to chat, research, planning, or review.

The optional mixed profile uses Groq GPT-OSS for chat, Cloudflare GLM-4.7-Flash
for research and planning, and Cloudflare GPT-OSS for structured review. Set
`CLOUDFLARE_CREW_MAX_RPM=30` for the documented starting point when every crew
model runs on Cloudflare. The Cloudflare reviewer validates the itinerary and
shares `REQUEST_TIMEOUT` across its first request and one retry.

`GROQ_REUSE_EXTRACTED_REPLY=true` saves a model call on eligible Groq chat
turns by reusing the extraction reply. Use the default `false` to stream text
from the reply model. `GROQ_COMPACT_PLANNING=true` is a separate option for
Groq planning that generates a draft without the full research crew.
See [hosted model setup and checks](docs/agents/hosted-free-models.md).

The production Docker stack defaults to the mixed profile. Set the model IDs
and provider credentials from the [root environment template](.env.example)
in Dokploy or in the root `.env`, then redeploy the backend and worker together.
When migrating from Vertex AI, replace any existing `MODEL` and `JUDGE_MODEL`
values and remove `VERTEXAI_PROJECT`, `VERTEXAI_LOCATION`, and `GCP_KEY_FILE`.

## Repository layout

```
backend/app/            config, schemas, LLM factory, FastAPI app, DB models
backend/app/crew/       agents, tasks, tools, crew assembly, source citations
backend/app/rag/        curated knowledge base + Chroma ingest
backend/app/auth/       accounts, sessions, Google OAuth
backend/app/queue/      arq background jobs (the crew runs here, not in the request)
backend/app/delivery/   PDF rendering, email (Resend), Telegram
backend/app/routers/    auth/conversations/jobs/settings/geo route modules
backend/alembic/        DB migrations
backend/evaluation/     scenarios, metrics, run_eval, reviewer_probe
frontend/               Next.js (App Router) streaming chat UI
```

## Development checks

Run `python -m unittest discover -s tests` from `backend/`. Run `npm run lint`
and `npm run build` from `frontend/`.

## Troubleshooting

- **Model authentication failed.** Check the credentials for the provider assigned to that task.
- **An itinerary stays queued.** Confirm the API and worker use the same Redis URL and that the worker is running.
- **`Knowledge base unavailable`** — run `python -m app.rag.ingest`.
- **DB connection refused** — ensure `docker compose up -d db` is running (or fix
  `DATABASE_URL`).
- **429 / rate limit during eval** — raise `EVAL_REQUEST_DELAY` in `.env`.
- **Eval hangs / "Timeout: Connection timed out after None seconds"** — a single
  provider call stalled. Each call now has a `REQUEST_TIMEOUT` (default 120 s) so
  it fails fast and retries instead of hanging the whole sweep; lower/raise it in
  `.env` if needed. The harness also checkpoints `results.csv` after every row,
  so an interrupted run keeps its completed scenarios — just re-run to finish.
