# Demo Guide — MUA600 Multi-Agent Manufacturing Cell

This guide walks through a complete live demonstration of all five requirements (R1–R5) in order. Each section includes the exact PowerShell commands, expected console output, and what to observe in the simulation.

---

## Prerequisites

Make sure the following are installed and working before starting:

- CMAS 2.14  
- Python 3.x with dependencies: pip install \-r llm\_sidecar/requirements.txt  
- Ollama with llama3: ollama pull llama3

**Note:** Running the live simulation requires CMAS 2.14 and the University West simulation environment, which are not included in this repository. The CMAS model, Python sidecar, and documentation are provided so the design and implementation can be studied in full.

---

## System Startup (required before every demo)

### 1\. Clone the repository

git clone https://github.com/gabrieldanho9988-sys/MUA600-Multi-Agent-Manufacturing.git

cd MUA600-Multi-Agent-Manufacturing

All commands below are run from inside this folder.

### 2\. Start the simulation

Double-click Simulation.exe \-\> wait for the register panel to appear.

### 3\. Start CMAS

Open cmas.exe \-\> open cmas\_project/mua600.cmas \-\> click **Run**

Wait until the status bar shows:

Running 07: Failed 00: Finished 00

### 4\. Warm up the LLM

ollama run llama3 "hi"

Wait for a reply before continuing.

---

## R1 — Decentralized Single-Product Routing

**What it shows:** A Part agent negotiates independently with a Process agent and the Crane — no central controller coordinates them.

### Run

python llm\_sidecar\\llm\_main.py "make 1 type-1 part"

Press **Generate** on Source1 in the simulation.

### Expected PowerShell output

\[Input\] 'make 1 type-1 part'

\[LLM\] attempt 1/3 ...

\[LLM\] raw response: '{"orders": \[{"part\_type": 1, "quantity": 1}\]}'

\[OK\] Validated order: \[{'part\_type': 1, 'quantity': 1}\]

\[Modbus\] Writing pending1=1 \-\> reg 25

\[Modbus\] Writing pending2=0 \-\> reg 26

\[DONE\] Order submitted to CMAS OrderIntake:

  Part1 (type-1) pending : 1  (Modbus reg 25\)

  Part2 (type-2) pending : 0  (Modbus reg 26\)

  Press Generate in Simulation.exe to release parts one at a time.

### Expected CMAS console output

OrderIntake: deploying Part1, pending after \= 0

Start process plan for process 1

Start to run Process1

ask a transporter to move to sink

### What to observe

- \[1\]setX in the register panel updates as the crane moves  
- \[2\]setY \= 150 during transit, drops to 82 during pickup/placement  
- \[19\]p1running \= 1 while Process1 is active  
- Status bar \-\> **Finished 01**

### Key point

The Crane only receives X/Y coordinates. It has no knowledge of part type, process plan, or route. The Part agent owns all routing logic — that is decentralized control.

---

## R2 — Multiple Product Types Concurrently

**What it shows:** Type-1 and type-2 parts run simultaneously on separate process stations. The Crane serves both without any changes to its code.

### Run

python llm\_sidecar\\llm\_main.py "make 2 type-1 parts and 2 type-2 parts"

Then alternate Generate presses:

- Press **Generate on Source1** \-\> type-1 part released  
- Press **Generate on Source2** \-\> type-2 part released  
- Repeat as each source clears

### Expected PowerShell output

\[Input\] 'make 2 type-1 parts and 2 type-2 parts'

\[LLM\] attempt 1/3 ...

\[LLM\] raw response: '{"orders": \[{"part\_type": 1, "quantity": 2}, {"part\_type": 2, "quantity": 2}\]}'

\[OK\] Validated order: \[{'part\_type': 1, 'quantity': 2}, {'part\_type': 2, 'quantity': 2}\]

\[Modbus\] Writing pending1=2 \-\> reg 25

\[Modbus\] Writing pending2=2 \-\> reg 26

\[DONE\] Order submitted to CMAS OrderIntake:

  Part1 (type-1) pending : 2  (Modbus reg 25\)

  Part2 (type-2) pending : 2  (Modbus reg 26\)

### Expected CMAS console output

OrderIntake: deploying Part1, pending after \= 1

Start process plan for process 1

Start to run Process1

OrderIntake: deploying Part2, pending after \= 1

Start process plan for process 2

Start to run Process2

### What to observe

- Two blue part squares visible in the simulation moving independently  
- \[19\]p1running and \[20\]p2running both active  
- Crane serves both routes without collision  
- Status bar \-\> **Finished 04**

### Key point

The only difference between RunProcess1 and RunProcess2 is one line: ProcessInterface.process := 2. The Crane code is identical to R1. Adding a new product type requires only a new Part template — nothing else changes.

---

## R3 — Plug & Produce Reconfigurability

**What it shows:** Changing one data value on a process agent moves the crane to a new coordinate — no code changes anywhere.

### Round 1 — Baseline (x \= 450\)

Confirm in CMAS Modelling Tree:

Process1 \-\> Interfaces \-\> ProcessInterface \-\> Variables \-\> x \-\> 450

python llm\_sidecar\\llm\_main.py "make 1 type-1 part"

Press **Generate on Source1** \-\> watch \[1\]setX \-\> crane goes to **450**.

### Round 2 — Reconfigured (x \= 600\)

1. Click **Stop** in CMAS  
2. In Modelling Tree: Process1 \-\> Interfaces \-\> ProcessInterface \-\> Variables \-\> x  
3. Change value from **450 \-\> 600** \-\> press Enter  
4. Click **Update** \-\> click **Run** \-\> wait 10 seconds

python llm\_sidecar\\llm\_main.py "make 1 type-1 part"

Press **Generate on Source1** \-\> watch \[1\]setX \-\> crane goes to **600**.

The part will not complete at x=600 (no physical station there) — this is expected and is the proof.

### What to observe

- Round 1: setX reaches 450 during Process1 approach  
- Round 2: setX reaches 600 — a completely different coordinate  
- Zero changes to crane code, process plan, or any other agent

### Key point

Process position is stored as a runtime variable on the process agent's own interface. The crane asks "where are you?" at runtime via getXY() and follows the answer. Reconfiguration is a data change, not a code change.

### Restore after demo

1. Stop CMAS  
2. Set x back to **450** in Modelling Tree  
3. Restart Simulation.exe \-\> CMAS \-\> Run

---

## R4 — Process Failure Detection and Recovery

**What it shows:** When Process1 reports a failure, the Part agent detects it, saves its position, unbooks Process1, and automatically re-routes to Process2 — without any central controller involvement.

### How failure is triggered

Process1's run skill is configured with agent.failed := true in state 10\. This simulates a sensor or hardware failure signal. In a real system this flag would be set by the process agent itself when a physical sensor detects an error.

### Run

python llm\_sidecar\\llm\_main.py "make 1 type-1 part"

Press **Generate on Source1**.

### Expected CMAS console output

OrderIntake: deploying Part1, pending after \= 0

Start process plan for process 1

Start to run Process2

ask a transporter to move to sink

### What to observe

- Part books Process1 \-\> Process1 immediately fails  
- Part saves Process1 coordinates \-\> unbooks \-\> calls agreeandbook() again  
- CMAS finds Process2 as alternative \-\> Part re-routes there  
- Crane moves part from Process1 location to Process2  
- Part completes at Process2 \-\> delivered to Sink  
- Status bar \-\> **Finished 01**

### Key point

The recovery uses the same agreeandbook() mechanism as R1. The Part does not have a hardcoded fallback — it re-runs the negotiation protocol. If Process2 were also busy, CMAS would wait. If a third process existed, CMAS could book that instead.

### Restore after demo

Remove agent.failed := true from Process1's run skill \-\> click **Update** \-\> system returns to normal.

---

## R5 — LLM Natural Language Order Intake

**What it shows:** A natural language string is converted to validated structured JSON by a local LLM (llama3 via Ollama), then consumed by the CMAS OrderIntake agent to drive the manufacturing system.

Natural language \-\> llama3 \-\> JSON validator \-\> Modbus TCP

\-\> OrderIntake agent \-\> Part agents \-\> Manufacturing cell

### Demo 1 — Main order

python llm\_sidecar\\llm\_main.py "make 3 type-1 parts and 2 type-2 parts"

Press **Generate on Source1** x 3 and **Generate on Source2** x 2\.

### Expected output

\[Input\] 'make 3 type-1 parts and 2 type-2 parts'

\[LLM\] attempt 1/3 ...

\[LLM\] raw response: '{"orders": \[{"part\_type": 1, "quantity": 3}, {"part\_type": 2, "quantity": 2}\]}'

\[OK\] Validated order: \[{'part\_type': 1, 'quantity': 3}, {'part\_type': 2, 'quantity': 2}\]

\[Modbus\] Writing pending1=3 \-\> reg 25

\[Modbus\] Writing pending2=2 \-\> reg 26

\[DONE\] Order submitted to CMAS OrderIntake:

  Part1 (type-1) pending : 3  (Modbus reg 25\)

  Part2 (type-2) pending : 2  (Modbus reg 26\)

Status bar \-\> **Finished 05**

### Demo 2 — Flexible natural language

python llm\_sidecar\\llm\_main.py "produce 2 type-1 and 1 type-2"

Same result — different phrasing, same validated JSON output.

### Validation boundary

The Python validator checks several conditions before anything reaches Modbus:

"orders" in data                    \# correct key exists

isinstance(val\["part\_type"\], int)   \# correct type

val\["part\_type"\] in {1, 2}          \# sensible value

val\["quantity"\] \> 0                 \# positive quantity

If any check fails \-\> LLM is retried up to 3 times \-\> order rejected. Nothing unsafe ever reaches Modbus or the agents.

### Key point

The LLM never writes to Modbus directly. It produces JSON only. A deterministic Python validator is the safety boundary between the non-deterministic LLM and the physical manufacturing system. llama3 runs fully locally — no internet, no API key, no data leaves the machine.

---

## Full System Architecture

\+-----------------------------------------------------+

|                  Python LLM Sidecar                 |

|  Natural Language \-\> llama3 \-\> Validator \-\> Modbus  |

\+----------------------------+------------------------+

                             | Registers 25/26

\+----------------------------v------------------------+

|                    CMAS 2.14                        |

|  OrderIntake \-\> Part1/Part2 \-\> Process1/2 \+ Crane   |

\+----------------------------+------------------------+

                             | Modbus TCP Registers 1-24

\+----------------------------v------------------------+

|                 Simulation.exe                      |

|         Physical crane, sensors, processes          |

\+-----------------------------------------------------+

---

## Register Reference

| Register | Name | Description |
| :---- | :---- | :---- |
| 1 | setX | Crane target X position |
| 2 | setY | Crane target Y position |
| 3 | vacuum | Crane gripper on/off |
| 15 | atX | Crane current X position |
| 16 | atY | Crane current Y position |
| 17 | source1 | Part present at Source1 |
| 18 | source2 | Part present at Source2 |
| 19 | p1running | Process1 active |
| 20 | p2running | Process2 active |
| 21 | p1sensor | Part detected at Process1 |
| 22 | p2sensor | Part detected at Process2 |
| 25 | pending1 | Type-1 parts to deploy (LLM bridge) |
| 26 | pending2 | Type-2 parts to deploy (LLM bridge) |

---

*MUA600 Multi-Agent Systems — University West* *Grade: A (highest in course)*  
