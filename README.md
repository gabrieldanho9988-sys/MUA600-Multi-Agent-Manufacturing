# MUA600 — Multi-Agent Manufacturing Cell with LLM Order Intake

**Grade: A (highest in course) — University West, MUA600 Multi-Agent Systems**

A fully decentralized multi-agent manufacturing system built in CMAS 2.14,
extended with a Python/Ollama LLM sidecar for natural-language order intake.
The system controls a simulated crane manufacturing cell where autonomous
agents negotiate resources, handle process failures, and reconfigure
dynamically — without any central controller.

---

## System Architecture
---

## Requirements Implemented

| Req | Grade | Description |
|-----|-------|-------------|
| R1 | C | Decentralized single-product routing via agreeandbook() |
| R2 | C | Multiple product types (type-1 and type-2) running concurrently |
| R3 | C | Plug & Produce reconfigurability via runtime coordinate configuration |
| R4 | B | Process failure detection and automatic re-routing to alternative |
| R5 | A | LLM-based natural language order intake via Ollama/llama3 |

---

## Tech Stack

- **CMAS 2.14** — Multi-agent framework (Structured Text skill programming)
- **Python 3.x** — LLM sidecar and Modbus TCP communication
- **Ollama + llama3** — Local LLM, no internet required
- **Modbus TCP** — Communication bridge between Python and CMAS
- **pymodbus** — Python Modbus library

---

## Project Structure
---

## How to Run

### Prerequisites
- CMAS 2.14 installed
- Python 3.x installed
- Ollama installed with llama3 model pulled

### 1. Install Python dependencies
```bash
pip install -r llm_sidecar/requirements.txt
```

### 2. Pull the LLM model
```bash
ollama pull llama3
```

### 3. Start the simulation
Open `Simulation.exe` → wait for register panel

### 4. Start CMAS
Open CMAS → open `cmas_project/mua600.cmas` → click **Run**
Wait for: `Running 07: Failed 00`

### 5. Submit an order
```bash
python llm_sidecar/llm_main.py "make 3 type-1 parts and 2 type-2 parts"
```

### 6. Generate parts
Press **Generate** on Source1 and Source2 in the simulation.

---

## Key Design Decisions

**Decentralized control (R1-R2)**
Parts negotiate directly with processes and crane via `agreeandbook()`.
No central controller — each Part agent runs an independent process plan.

**Plug & Produce (R3)**
Process position is stored as a runtime variable on the process agent's
interface. Changing one value reconfigures the entire system — no code changes.

**Failure recovery (R4)**
After `ProcessInterface.run()`, the Part checks `runFailed`. On failure:
saves coordinates → unbooking → re-agreeandbook → reroutes to alternative.
If both processes fail, the part reports gracefully without crashing the system.

**LLM safety boundary (R5)**
The LLM (llama3) produces JSON only. A Python validator checks keys, types,
and value ranges before anything reaches Modbus. The LLM never writes to
hardware registers directly.

---

## Author

**Gabriel Danho**
M.Sc. AI and Automation — University West (Högskolan Väst), Sweden
B.Sc. Physics + Master of Education — Linköping University

[LinkedIn](www.linkedin.com/in/gabriel-danho-2a1a9112a)
| [GitHub](https://github.com/gabrieldanho9988-sys)

---

## Course

MUA600 Multi-Agent Systems — University West
Examiner: Mattias Bennulf
