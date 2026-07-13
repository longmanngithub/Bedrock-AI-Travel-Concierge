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
   └────────────────────────────> TravelCrew (CrewAI, sequential)
                                          Destination → Food → Personalization →
                                          Accommodation → Budget → Planner → Reviewer
                                            │ tools: web search (LangChain),
                                            │ RAG retriever (Chroma),
                                            │ budget calc (code)
                                          structured Itinerary
                                          → PostgreSQL + boarding-pass card
Evaluation harness ──> runs each scenario through baseline AND crew, scores both.
```

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

### 1. Database

```bash
docker compose up -d db          # starts PostgreSQL on :5432
```

### 2. Backend

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env             # then paste your GOOGLE_API_KEY into .env

python -m app.rag.ingest         # build the RAG vector store (first run downloads
                                 # a small embeddings model, ~90 MB)

uvicorn app.main:app --reload    # API on http://localhost:8000  (docs at /docs)
```

### 3. Frontend

```bash
cd frontend
cp .env.example .env             # NEXT_PUBLIC_API_URL defaults to http://localhost:8000
npm install
npm run dev                      # Next.js UI on http://localhost:5173
```

Open http://localhost:5173 and just chat: tell Bedrock where you'd like to go.
It streams a reply, asks for anything missing (with tappable suggestion chips
and a free-text "Something else" option), then — once it has a destination,
dates, and a budget — runs the **CrewAI multi-agent** crew and returns the
itinerary as a boarding-pass card. (The single-LLM baseline still lives behind
`POST /itineraries?system=baseline` and the evaluation harness below.)

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
backend/app/          config, schemas, LLM factory, FastAPI app, DB models
backend/app/crew/     agents, tasks, tools, crew assembly
backend/app/rag/      curated knowledge base + Chroma ingest
backend/evaluation/   scenarios, metrics, run_eval, reviewer_probe
frontend/             Next.js (App Router) streaming chat UI
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
