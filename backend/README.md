# Rayda Fleet Copilot 

An agentic AI system that enables IT administrators to interact with device telemetry using natural language. Built with LangGraph, FastAPI, and SQLAlchemy.

---

## Table of Contents

- [Architecture](#architecture)
- [Tool Catalog](#tool-catalog)
- [Grounding Strategy](#grounding-strategy)
- [Guardrails](#guardrails)
- [Installation & Setup](#installation--setup)
- [Running the Agent](#running-the-agent)
- [Running Evaluations](#running-evaluations)
- [Key Design Decisions](#key-design-decisions-and-trade-offs)

---

## Architecture

```
User / IT Admin
      │
      ▼
FastAPI REST API  (/api/v1/chats)
      │
      ▼
LangGraph Functional Agent  (agent.py)
      │
      ├── call_llm (@task)          — invokes LLM with bound tools
      ├── execute_tool (@task)      — enforces tenant isolation on every call
      │
      ├─── [Tool Calls] ───────────────────────────────────────┐
      │         check_fleet_compliance                          │
      │         analyze_battery_degradation                     │
      │         get_low_disk_space_devices                      │
      │         analyze_ram_constraints_over_time               │
      │         get_recent_audit_logs                           │
      │         propose_remediation_action  ──► INTERRUPT       │
      │                                         (HitL pause)   │
      └─────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                              AuditLog (SQLite)
```

### Agent Loop

The agent uses LangGraph's **Functional API** (`@entrypoint` / `@task`) with an explicit `while True` reasoning loop:

1. LLM receives the system prompt (injected with `company_id`) and the user's question
2. LLM decides whether to call a tool or respond directly
3. If tool calls are present, each is dispatched via `execute_tool` which enforces tenant isolation by overwriting `company_id`
4. If `propose_remediation_action` is called, the graph **pauses** via `interrupt()` and waits for explicit human approval via the `/chats/approve` endpoint
5. After approval/rejection, the decision is written to the `audit_logs` table and execution resumes
6. The loop ends when the LLM produces a plain text response with no tool calls

### Data Layer

Telemetry is stored in a normalised SQLite database with 10 tables:

| Table | Description |
|---|---|
| `snapshots` | Core record — device, company, timestamp |
| `os_info` | OS platform, version, kernel |
| `device_identities` | Serial number, model, processor |
| `memory_stats` | RAM usage per snapshot |
| `battery_stats` | Battery health, cycle count |
| `disk_volumes` | Volume name, size, available bytes |
| `network_interfaces` | Network adapters |
| `installed_software` | Software inventory |
| `compliance_results` | Per-check pass/fail status |
| `audit_logs` | Immutable record of every proposed and decided action |

---

## Tool Catalog

| Tool | Type | Description |
|---|---|---|
| `check_fleet_compliance` | Analytical | Returns devices failing compliance checks at the latest snapshot. Supports optional `severity` filter. |
| `analyze_battery_degradation` | Analytical | Identifies devices with battery cycle counts above a threshold at the latest snapshot. |
| `get_low_disk_space_devices` | Analytical | Finds devices with available disk space below a configurable GB threshold at the latest snapshot. |
| `analyze_ram_constraints_over_time` | Trend | Scans all historical snapshots and flags devices where RAM usage exceeded a threshold in more than 50% of recordings. |
| `analyze_compliance_drift` | Trend | Scans compliance history per device per check and classifies trend as `drifting_worse`, `persistently_failing`, `improving`, or `stable_mixed`. Skips consistently passing devices. |
| `get_recent_audit_logs` | Audit | Retrieves the history of proposed and decided remediation actions for the tenant. |
| `propose_remediation_action` | Action | Triggers a Human-in-the-Loop interrupt. Supports: `create_upgrade_order`, `open_remediation_ticket`, `notify_employee`, `flag_device_for_replacement`. |

---

## Grounding Strategy

Every claim the agent makes must be traceable to a specific database record. This is enforced at two levels:

**Tool level** — Every analytical tool returns `evidence_snapshot_id` (the primary key of the snapshot row) and `collected_at` (the exact timestamp of the reading). The LLM cannot answer from memory; it must call a tool first.

**Prompt level** — The system prompt instructs the agent:
> "You must ONLY answer questions using the data provided by your tools. Whenever you make a claim, you MUST cite the `collected_at` timestamp and `evidence_snapshot_id` provided by the tool."

This means every factual statement in a response maps back to a specific row in the database, making answers auditable and reproducible.

---

## Guardrails

### 1. Tenant Isolation

`company_id` is injected into every tool call by `execute_tool()` in `agent.py`, overwriting whatever value the LLM might have placed there. Even if a user asks "check compliance for company X", the tool will always query the company associated with the authenticated session.

```python
# From agent.py — execute_tool task
args["company_id"] = company_id  # always overwritten from session context
```

### 2. Human-in-the-Loop for State-Changing Actions

`propose_remediation_action` never executes directly. When the LLM calls it, the graph pauses via `interrupt()` and returns a `pending_human_in_the_loop` response to the caller. Execution only resumes when the IT admin posts an explicit `approved` or `rejected` decision to `/api/v1/chats/approve`.

### 3. Audit Logging

Every action proposal — whether approved or rejected — is written to the `audit_logs` table with:
- Timestamp (UTC)
- `company_id`
- Action type and full proposal details
- Human decision

This provides an immutable audit trail of all operational actions.

### 4. Evidence Requirement

The system prompt explicitly instructs the agent to refuse actions without sufficient telemetry evidence. Action proposals must include a `reason` field citing specific data.

---

## Installation & Setup

### Prerequisites

- Python 3.14+
- One of: [uv](https://docs.astral.sh/uv/), a Python virtual environment, or Docker

**1. Clone the repository**

```bash
git clone https://github.com/iyanuashiri/rayda-project.git
cd rayda-project/backend
```

**2. Configure environment variables**

Create `app/.env` with the following keys:

```env
TELEMETRY_SQLITE_FILE_NAME=../telemetry.db
OPENROUTER_API_KEY=your_openrouter_api_key
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=rayda-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

**3. Install, migrate, and load data** — pick one method below.

---

### Option A — uv (recommended)

```bash
uv sync
uv run alembic upgrade head
uv run python -m app.core.load
```

---

### Option B — Virtual Environment

```bash
# Create and activate
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -e .

# Run migrations and load data
alembic upgrade head
python -m app.core.load
```

---

### Option C — Docker

```bash
# Build and start (migrations run automatically on container startup)
docker compose up --build

# In a separate terminal, load the dataset into the running container
docker compose exec api python -m app.core.load
```

The API will be available at `http://localhost:8000`.  
To stop: `docker compose down`

---

## Running the Agent

### Option A — LangGraph Dev Server (recommended for development)

Runs the agent graph with a built-in playground UI and LangSmith tracing:

```bash
uv run langgraph dev
```

The playground will be available at `http://localhost:2024`.

In the playground, send inputs as JSON in the following format:

```json
{
  "company_id": "acme-001",
  "question": "Which devices are low on disk space?"
}
```

To resume after a Human-in-the-Loop pause, send the approval decision:

```json
{
  "action_decision": "approved"
}
```

---

### Option B — FastAPI Server

```bash
uv run uvicorn app.main:app --reload
```

API docs available at `http://localhost:8000/docs`.

#### Example 1: Ask a question

```bash
curl -X POST http://localhost:8000/api/v1/chats/ \
  -H "Content-Type: application/json" \
  -H "thread-id: session-abc" \
  -d '{"company_id": "acme-001", "question": "Which devices are low on disk space?"}'
```

#### Example 2: Request an action (triggers Human-in-the-Loop pause)

This question asks the agent to propose a remediation action. The agent will pause and return a `pending_human_in_the_loop` response rather than acting immediately.

```bash
curl -X POST http://localhost:8000/api/v1/chats/ \
  -H "Content-Type: application/json" \
  -H "thread-id: session-abc" \
  -d '{
    "company_id": "acme-001",
    "question": "Device DEV-001 has a battery cycle count of 620. Create an upgrade order to replace the battery."
  }'
```

The response will look like:

```json
{
  "status": "pending_human_in_the_loop",
  "message": "An action has been proposed and requires explicit approval.",
  "requires_approval": true,
  "pending_action": {
    "company_id": "acme-001",
    "device_id": "DEV-001",
    "action_type": "create_upgrade_order",
    "reason": "Battery cycle count is 620, exceeding the end-of-life threshold of 500."
  }
}
```

#### Example 3: Approve the proposed action

Resume the paused session using the same `thread-id`. The agent will execute the action and return a final answer.

```bash
curl -X POST http://localhost:8000/api/v1/chats/approve \
  -H "Content-Type: application/json" \
  -H "thread-id: session-abc" \
  -d '{"company_id": "acme-001", "thread_id": "session-abc", "action_decision": "approved"}'
```

To reject instead, set `"action_decision": "rejected"`.

---

## Running Evaluations

The evaluation suite runs the real LangGraph agent against the real database and uses **LLM-as-judge scoring** — a separate judge LLM evaluates each agent response against a rubric and returns a structured verdict with a score and reason. This is more reliable than keyword matching because the judge can reason about whether the agent actually met the criteria.

```bash
uv run python -m app.evaluate
```

Each test produces:
- A **pass/fail** verdict
- A **score out of 10**
- A **plain-English reason** from the judge explaining what passed or failed

The Human-in-the-Loop test (Test 7) is evaluated deterministically — it passes if and only if the graph emits an `__interrupt__` event, which requires no judge call.

### Test Cases

| # | Name | Rubric area |
|---|---|---|
| 1 | Zero Hallucination | Grounding & Correctness |
| 2 | Grounded Storage Analysis | Grounding & Correctness |
| 3 | Battery End-of-Life Trend Detection | Insight & Trend Detection |
| 4 | Compliance Drift Over Time | Insight & Trend Detection |
| 5 | RAM Constraints Over Time | Insight & Trend Detection |
| 6 | Adversarial Tenant Isolation | Guardrails |
| 7 | Human-in-the-Loop Action Guardrail | Guardrails |
| 8 | Audit Log Retrieval | Action Quality |

---

## Running Unit & Integration Tests

The unit and integration test suite uses an in-memory SQLite database — no LLM calls, no real `telemetry.db`. Fast and fully deterministic.

```bash
uv run pytest tests/
```

Run with verbose output:

```bash
uv run pytest tests/ -v
```

### Test Coverage

| File | What it covers |
|---|---|
| `tests/test_tools.py` | All 6 tools — correct filtering, GB conversion, time-series logic, `None` value safety, evidence fields, tenant isolation |
| `tests/test_guardrails.py` | `execute_tool` always injects `company_id`; invalid action types are rejected before interrupt fires |
| `tests/test_api.py` | `/chats/` and `/chats/approve` endpoints — success, interrupt, approval flow, schema validation, error handling |

---

## Key Design Decisions and Trade-offs

### Functional API over StateGraph

LangGraph's Functional API (`@entrypoint` / `@task`) was chosen over `StateGraph` because the reasoning loop is explicit Python — easier to read, debug, and reason about. The trade-off is less built-in visualisation compared to a node-based graph, but LangSmith tracing compensates for this.

### Single `propose_remediation_action` tool vs. four separate tools

The four action types (`create_upgrade_order`, `open_remediation_ticket`, `notify_employee`, `flag_device_for_replacement`) are unified under one tool with an `action_type` enum. This keeps the interrupt logic in one place and makes it impossible to accidentally bypass the Human-in-the-Loop guardrail by calling a different action tool directly.

### `company_id` injection at the executor level

Tenant isolation is enforced in `execute_tool()`, not in the prompt. Prompt-level instructions can be overridden by a sufficiently creative user query. Injecting `company_id` in code makes it structurally impossible for the LLM to query another tenant's data, regardless of what it was asked.

### SQLite for storage

SQLite is used for simplicity and portability. In production this would be replaced with PostgreSQL. The SQLAlchemy ORM layer means this is a one-line change in `database.py`.

### Normalised schema over a document store

Telemetry is stored in a normalised relational schema rather than as raw JSON blobs. This enables precise SQL queries (e.g. "devices with >500 battery cycles"), proper indexing, and time-series analysis across snapshots — things that would require expensive full-document scans in a document store.
