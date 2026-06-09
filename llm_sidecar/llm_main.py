#!/usr/bin/env python3
"""
MUA600 R5 - LLM order-intake sidecar.

Usage:
  python llm_main.py "make 3 type-1 parts and 2 type-2 parts"
  python llm_main.py          # interactive prompt

Flow (fail-safe by design):
  1. Ask Ollama (llama3:latest) to parse the natural-language request.
  2. Validate the JSON strictly (up to MAX_RETRIES attempts, error fed back each time).
  3. ONLY on full validation success: write pending counts to Modbus registers
       reg 25 -> pending Part1 (type-1) count
       reg 26 -> pending Part2 (type-2) count
  4. Append an audit entry to orders.json.

Garbage from the model NEVER reaches Modbus.
The LLM never touches Modbus - the Python code is the only Modbus writer here.
The CMAS OrderIntake agent reads registers 25/26 and deploys Part agents as
the corresponding Source sensors go high.

Orders schema written to orders.json (append-only audit log):
[
  {
    "timestamp":  "2026-06-04T14:30:00",
    "raw_input":  "make 3 type-1 parts and 2 type-2 parts",
    "parsed": [
      { "part_type": 1, "quantity": 3 },
      { "part_type": 2, "quantity": 2 }
    ],
    "modbus_written": {
      "reg25_pending_part1": 3,
      "reg26_pending_part2": 2
    }
  }
]
"""

import json
import sys
import datetime
import pathlib

import requests
from pymodbus.client import ModbusTcpClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "llama3:latest"
MAX_RETRIES   = 3

MODBUS_HOST        = "127.0.0.1"
MODBUS_PORT        = 502
MODBUS_SLAVE       = 1
REG_PENDING_TYPE1  = 25   # holding register: pending Part1 count  (type-1)
REG_PENDING_TYPE2  = 26   # holding register: pending Part2 count  (type-2)

ORDERS_LOG = pathlib.Path(__file__).parent / "orders.json"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are an order parser for a manufacturing cell that produces two part types:
  part_type 1  ->  routed through Process1  (Source1 -> Process1 -> Sink)
  part_type 2  ->  routed through Process2  (Source2 -> Process2 -> Sink)

Parse the user's request and output ONLY a raw JSON object with this EXACT schema.
No markdown, no code fences, no prose, no explanation - just the JSON:

{
  "orders": [
    { "part_type": <integer 1 or 2>, "quantity": <positive integer >= 1> }
  ]
}

Rules:
- "orders" must be a non-empty list.
- "part_type" must be the integer 1 or 2 (not a string, not a float).
- "quantity" must be a positive integer >= 1 (not a string, not a float).
- If the request mentions type 1 multiple times (e.g. "2 type-1 and 1 more type-1"),
  consolidate into a single entry with the total quantity.
- Each part_type appears AT MOST ONCE in the list. Do not emit two entries for the same type.
- Output absolutely nothing other than the JSON object.
"""

# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------

def _chat(messages: list) -> str:
    """Single call to Ollama /api/chat. Returns the assistant content string."""
    payload = {
        "model":    OLLAMA_MODEL,
        "messages": messages,
        "stream":   False,
        "format":   "json",          # constrain output to a complete JSON object
        "options":  {"num_predict": 256},  # generous cap; order JSON is tiny
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot reach Ollama at {OLLAMA_URL}.\n"
            "  -> Is 'ollama serve' running in a terminal?"
        )
    except requests.HTTPError as exc:
        raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    try:
        # stream=False → single response object, content is the complete reply
        return resp.json()["message"]["content"].strip()
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Unexpected Ollama response shape: {resp.text!r}") from exc

# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences that the model might sneak in."""
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _validate(raw: str) -> dict:
    """
    Parse and validate a JSON blob from the model.
    Raises ValueError with a human-readable message on any problem.
    The returned dict is guaranteed to satisfy the schema.
    """
    text = _strip_fences(raw)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON ({exc}). Model output was: {raw!r}") from exc

    if not isinstance(obj, dict) or "orders" not in obj:
        raise ValueError(f'Root must be an object with an "orders" key. Got: {obj!r}')

    orders = obj["orders"]
    if not isinstance(orders, list) or len(orders) == 0:
        raise ValueError('"orders" must be a non-empty list.')

    seen_types: set = set()
    for i, entry in enumerate(orders):
        if not isinstance(entry, dict):
            raise ValueError(
                f"orders[{i}] must be a JSON object, got {type(entry).__name__}."
            )

        pt = entry.get("part_type")
        if not isinstance(pt, int) or isinstance(pt, bool) or pt not in (1, 2):
            raise ValueError(
                f"orders[{i}].part_type must be the integer 1 or 2, got {pt!r}."
            )

        qty = entry.get("quantity")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            raise ValueError(
                f"orders[{i}].quantity must be a positive integer (>= 1), got {qty!r}."
            )

        if pt in seen_types:
            raise ValueError(
                f"Duplicate part_type {pt}: each type may appear at most once."
            )
        seen_types.add(pt)

    return obj


def parse_with_retries(user_text: str) -> dict:
    """
    Ask the model to parse user_text into a validated order dict.
    Each failed validation attempt feeds the error message back to the model
    so it can self-correct. Raises RuntimeError if all MAX_RETRIES fail.
    """
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_text},
    ]
    last_err = None

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[LLM] attempt {attempt}/{MAX_RETRIES} ...", flush=True)
        raw = _chat(messages)
        print(f"[LLM] raw response: {raw!r}", flush=True)

        try:
            return _validate(raw)
        except ValueError as exc:
            last_err = exc
            print(f"[WARN] validation failed: {exc}", flush=True)
            if attempt < MAX_RETRIES:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your response failed validation with this error:\n"
                        f"  {exc}\n\n"
                        "Return ONLY the corrected JSON object - "
                        "no markdown, no code fences, no explanation."
                    ),
                })

    raise RuntimeError(
        f"Model failed validation after {MAX_RETRIES} attempts. "
        f"Last validation error: {last_err}"
    )

# ---------------------------------------------------------------------------
# Modbus helpers
# ---------------------------------------------------------------------------

def _open_client() -> ModbusTcpClient:
    client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT)
    if not client.connect():
        raise RuntimeError(
            f"Cannot connect to Modbus server at {MODBUS_HOST}:{MODBUS_PORT}.\n"
            "  -> Is Simulation.exe running?"
        )
    return client


def _verify_register(client: ModbusTcpClient, addr: int) -> None:
    """
    Write a sentinel, read it back, confirm they match.
    This checks that the Modbus server exposes a writable holding register
    at 'addr'. Raises RuntimeError with a clear diagnostic on any failure.

    IMPORTANT: run this before CMAS is deployed (or when source sensors are
    low) so the OrderIntake agent cannot drain the sentinel value mid-test.
    The sentinel value (0xFEFE = 65534) resets to 0 after verification.

    If this raises, check whether Simulation.exe exposes the register:
      - The simulation uses a FLAT holding-register address space (all signals,
        boolean and integer, share one address range 0-24). There is no separate
        coil space. Addresses 25 and 26 are the first genuinely free registers.
      - If the simulation's register bank is capped at address 24, try
        REG_PENDING_TYPE1 = 27 and REG_PENDING_TYPE2 = 28 instead.
        Do NOT use 17 or 18 — those are Source1.sensor and Source2.sensor.
      - Update the constants at the top of this file AND the CMAS
        OrderIntake variable bindings to match.
    """
    SENTINEL = 0xFEFE  # 65534 - clearly non-zero, fits in 16-bit unsigned

    wr = client.write_register(addr, SENTINEL, device_id=MODBUS_SLAVE)
    if wr.isError():
        raise RuntimeError(
            f"Modbus write to holding register {addr} failed: {wr}\n"
            f"  The simulation's Modbus server may not expose register {addr}.\n"
            f"  Fallback: try addresses 27/28 (REG_PENDING_TYPE1/2). NOT 17/18 — those are sensors."
        )

    rr = client.read_holding_registers(addr, count=1, device_id=MODBUS_SLAVE)
    if rr.isError():
        raise RuntimeError(
            f"Modbus read of register {addr} after write failed: {rr}"
        )

    got = rr.registers[0]
    if got != SENTINEL:
        raise RuntimeError(
            f"Register {addr} write/read mismatch: wrote {SENTINEL}, read {got}.\n"
            f"  Possible causes:\n"
            f"  1. The simulation doesn't persist writes to register {addr}.\n"
            f"  2. The CMAS OrderIntake agent drained the sentinel mid-test\n"
            f"     (run verification before deploying CMAS, or with sensors low).\n"
            f"  Fallback: try addresses 27/28 for REG_PENDING_TYPE1/2. NOT 17/18 — those are sensors."
        )

    # Reset to 0 - the sentinel must not trigger spurious CMAS deploys
    client.write_register(addr, 0, device_id=MODBUS_SLAVE)


def _check_previous_order(client: ModbusTcpClient) -> None:
    """Warn (not fail) if a previous order is still draining."""
    r1 = client.read_holding_registers(REG_PENDING_TYPE1, count=1, device_id=MODBUS_SLAVE)
    r2 = client.read_holding_registers(REG_PENDING_TYPE2, count=1, device_id=MODBUS_SLAVE)
    if r1.isError() or r2.isError():
        return  # Cannot read - verification step will catch this

    p1 = r1.registers[0]
    p2 = r2.registers[0]
    if p1 > 0 or p2 > 0:
        print(
            f"[WARN] Previous order still pending "
            f"(Part1={p1}, Part2={p2} in registers {REG_PENDING_TYPE1}/{REG_PENDING_TYPE2}). "
            "New values will OVERWRITE the old counts.",
            flush=True,
        )


def write_to_modbus(pending1: int, pending2: int) -> None:
    """
    Verify registers 25/26 are accessible, then write both pending counts.
    Nothing is written if verification fails (RuntimeError propagates up).
    """
    client = _open_client()
    try:
        _check_previous_order(client)

        print(f"[Modbus] Verifying reg {REG_PENDING_TYPE1} ...", flush=True)
        _verify_register(client, REG_PENDING_TYPE1)

        print(f"[Modbus] Verifying reg {REG_PENDING_TYPE2} ...", flush=True)
        _verify_register(client, REG_PENDING_TYPE2)

        print(
            f"[Modbus] Writing pending1={pending1} -> reg {REG_PENDING_TYPE1}",
            flush=True,
        )
        r1 = client.write_register(REG_PENDING_TYPE1, pending1, device_id=MODBUS_SLAVE)
        if r1.isError():
            raise RuntimeError(f"Write to reg {REG_PENDING_TYPE1} failed: {r1}")

        print(
            f"[Modbus] Writing pending2={pending2} -> reg {REG_PENDING_TYPE2}",
            flush=True,
        )
        r2 = client.write_register(REG_PENDING_TYPE2, pending2, device_id=MODBUS_SLAVE)
        if r2.isError():
            raise RuntimeError(f"Write to reg {REG_PENDING_TYPE2} failed: {r2}")

    finally:
        client.close()

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _log(user_text: str, order: dict, pending1: int, pending2: int) -> None:
    """Append a structured entry to orders.json (creates the file if absent)."""
    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "raw_input": user_text,
        "parsed": order["orders"],
        "modbus_written": {
            f"reg{REG_PENDING_TYPE1}_pending_part1": pending1,
            f"reg{REG_PENDING_TYPE2}_pending_part2": pending2,
        },
    }

    existing = []
    if ORDERS_LOG.exists():
        try:
            existing = json.loads(ORDERS_LOG.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []

    existing.append(entry)
    ORDERS_LOG.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Log] Appended to {ORDERS_LOG}", flush=True)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) >= 2:
        user_text = " ".join(sys.argv[1:])
    else:
        print("Enter order (e.g. 'make 3 type-1 parts and 2 type-2 parts'):")
        user_text = input("> ").strip()

    if not user_text:
        print("[ERROR] No input provided.", file=sys.stderr)
        sys.exit(1)

    print(f"[Input] {user_text!r}", flush=True)

    # Step 1: LLM parse - no side effects until validation passes
    try:
        order = parse_with_retries(user_text)
    except RuntimeError as exc:
        print(
            f"[ERROR] LLM parsing failed - nothing written to Modbus or disk.\n{exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[OK] Validated order: {order['orders']}", flush=True)

    # Extract counts (0 if a type was not mentioned in the order)
    pending1 = next(
        (e["quantity"] for e in order["orders"] if e["part_type"] == 1), 0
    )
    pending2 = next(
        (e["quantity"] for e in order["orders"] if e["part_type"] == 2), 0
    )

    # Step 2: Modbus write - only reached on full validation success
    try:
        write_to_modbus(pending1, pending2)
    except RuntimeError as exc:
        print(
            f"[ERROR] Modbus write failed - orders.json NOT written.\n{exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 3: Audit log - only after both LLM + Modbus succeed
    _log(user_text, order, pending1, pending2)

    print(
        f"\n[DONE] Order submitted to CMAS OrderIntake:"
        f"\n  Part1 (type-1) pending : {pending1}  (Modbus reg {REG_PENDING_TYPE1})"
        f"\n  Part2 (type-2) pending : {pending2}  (Modbus reg {REG_PENDING_TYPE2})"
        f"\n  Press Generate in Simulation.exe to release parts one at a time.",
        flush=True,
    )


if __name__ == "__main__":
    main()
