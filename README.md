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
| LLM         | Google Gemini 3.1 Flash-Lite (provider-agnostic via LiteLLM) |
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
- Gemini access, either:
  - **Vertex AI** (recommended if you have Google Cloud credit): run
    `gcloud auth application-default login`, enable the _Vertex AI API_, and set
    `MODEL`/`VERTEXAI_PROJECT` in `.env` (no rate-limit wall — see _Switching LLM
    provider_ below), or
  - a **free** Gemini API key from https://aistudio.google.com → _Get API key_
    (no card; free tier is limited to 15 requests/minute).
    No key is needed for web search or embeddings.

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

cp .env.example .env             # then paste your GOOGLE_API_KEY into .env
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
cp .env.example .env             # NEXT_PUBLIC_API_URL defaults to http://localhost:8000
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

Everything routes through `app/llm.py`, so only `.env` changes:

```bash
# Vertex AI — RECOMMENDED if you have Google Cloud credit (e.g. the $300 free
# trial). No API key, and NONE of the Developer-API 15 req/min limit.
#   1) gcloud auth application-default login
#   2) enable the "Vertex AI API" on your project
MODEL=vertex_ai/gemini-2.5-flash-lite
JUDGE_MODEL=vertex_ai/gemini-2.5-flash-lite
VERTEXAI_PROJECT=your-gcp-project-id
VERTEXAI_LOCATION=us-central1

# Groq (fast, free tier)
MODEL=groq/llama-3.3-70b-versatile
GROQ_API_KEY=...

# Ollama (fully local, no key; run `ollama serve` first)
MODEL=ollama/llama3.1
OLLAMA_API_BASE=http://localhost:11434
```

**Vertex vs Developer API:** the Gemini _Developer API_ (api key from AI Studio)
has a free tier capped at **15 requests/minute**, which a multi-agent crew
exceeds in a single run — forcing throttling and occasional 429 failures during
an eval sweep. _Vertex AI_ (authenticated by ADC, billed to Cloud credit) has
production quotas and no such wall, so the full sweep runs without throttling or
429s. (Latency is similar either way — the crew is call-bound, ~100 s, not
throttle-bound.) Both are one-line `.env` swaps.

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

## Troubleshooting

- **`Generation failed` / auth error** — check `GOOGLE_API_KEY` in `backend/.env`.
- **`Knowledge base unavailable`** — run `python -m app.rag.ingest`.
- **DB connection refused** — ensure `docker compose up -d db` is running (or fix
  `DATABASE_URL`).
- **429 / rate limit during eval** — raise `EVAL_REQUEST_DELAY` in `.env`.
- **Eval hangs / "Timeout: Connection timed out after None seconds"** — a single
  provider call stalled. Each call now has a `REQUEST_TIMEOUT` (default 120 s) so
  it fails fast and retries instead of hanging the whole sweep; lower/raise it in
  `.env` if needed. The harness also checkpoints `results.csv` after every row,
  so an interrupted run keeps its completed scenarios — just re-run to finish.
