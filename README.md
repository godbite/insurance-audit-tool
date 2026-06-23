# Plum Claims Audit Portal

Plum Claims Audit Portal is an **AI-driven Multi-Agent Insurance Claim Auditing Platform**. It automates the verification, structured extraction, cross-document consistency checks, and policy rule evaluation for claims using a distributed background pipeline.

---

## 👁️ System Architecture

![System Architecture](./assets/system_architecture.png)

The platform is designed as a decoupled, real-time reactive system consisting of the following layers:

### 1. Frontend Portal (React & WebSockets)
* **Vite-based React SPA** styled with premium custom Vanilla CSS (dark mode default, glassmorphic layouts, animated states).
* **Hash Routing Routing** (`#/claims/{id}`) for clean deep-linking, page reload persistence, and state retrieval.
* **WebSocket Channel Listener** streaming real-time stage execution states directly from the background Celery workers.
* **Robust UI Rendering**: Defensive display fallbacks to prevent React runtime failures when claims terminate early.

### 2. FastAPI Web Server
* **Asynchronous Endpoints** for claim registration, historical trace observation, and database inquiries.
* **Redis-cached Fast Path** for active pipeline results.
* **WebSocket Server Gateway** utilizing Redis Pub/Sub backend channels to pipe background events instantly to the client.

### 3. Background Processing (Celery & Redis)
* **Asynchronous Task Workers** executing the multi-agent claims audit pipeline without blocking HTTP threads.
* **Redis Pub/Sub & Caching**: Acts as the message broker for Celery, is the real-time event publisher channel, and caches final trace result payloads.

### 4. Relational Database (PostgreSQL)
* **SQLAlchemy (Async)** database session model mappings.
* **Alembic Migrations** database table creation scripts (`claims`, `decisions`, `traces`).

### 5. Multi-Agent Audit Pipeline
* **Doc Classification Agent**: Identifies document types (prescriptions, hospital bills, test results) via text and visual signals.
* **Doc Verification Agent**: Verifies that all required files and metadata matching the category are present.
* **Data Extraction Agent**: Combines PaddleOCR parsing with LLMs (Groq Llama-3, OpenRouter, Gemini) to extract structured fields into Pydantic models.
* **Cross-Doc Consistency Agent**: Validates field consistency across documents (e.g. confirming patient name matching).
* **Rules & AI Decisioning Engine**: Computes wait periods, copays, network discounts, and limits. Validates logic with a secondary LLM validation run.

### 6. Observability Engine (Langfuse)
* **Execution Traces**: Collects input, output, duration, and latency of every agent run.
* **Token/Cost Tracking**: Tracks API tokens used and total execution costs.

---

## ✨ Features

- **Real-Time Visual Pipeline Tracking**: Animated stage steps (In Progress ↻, Passed ✓, Failed ✗) with inline warning descriptions.
- **Rules Execution Grid**: Visual cards highlighting specific check executions and tripped policy parameters.
- **Detailed Line-Item Breakdown**: Tabular view of claimed vs approved amounts, resolution decisions, and reasons.
- **Obsolescent Trace Logger**: Collapsible tree viewer rendering raw pipeline execution traces directly inside the browser.
- **Drag-and-Drop Form Submission**: Fast claim intakes with preset filling buttons for easy demonstration.

---

## 🚀 Getting Started

### Prerequisites
* **Python 3.10+** (Virtual Environment recommended)
* **Node.js 18+** & **npm**
* **Docker** (to run Postgres & Redis infrastructure)

---

### Setup Guide

#### 1. Start Infrastructure
Start the database and Redis instances using docker-compose:
```bash
docker-compose up -d
```

#### 2. Backend Setup
Navigate to the `backend/` directory, set up your virtual environment, and install dependencies:
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # or install via pyproject.toml
```

Configure your environment settings in a `.env` file (see `.env.example` if available):
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/plum
REDIS_URL=redis://127.0.0.1:6379/0
GROQ_API_KEY=your-groq-api-key
PROVIDER_ORDER=groq  # Ensures Groq is prioritized
```

Apply database migrations:
```bash
alembic upgrade head
```

Start the FastAPI server:
```bash
uvicorn app.main:app --reload
```

Start the Celery worker process:
```bash
celery -A app.core.celery_app worker --loglevel=info
```

---

#### 3. Frontend Setup
Navigate to the `frontend/` directory, install packages, and launch Vite dev server:
```bash
cd ../frontend
npm install
npm run dev
```
Open `http://localhost:5173` to explore the dashboard.

---

## 🧪 Testing

Run backend unit tests to verify the rules engine and agent validations:
```bash
cd backend
PYTHONPATH=. .venv/bin/activate
pytest
```
All tests should execute and pass successfully.