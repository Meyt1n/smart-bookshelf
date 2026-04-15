from __future__ import annotations

import argparse
import json
import sys
from urllib import error, request


def http_json(url: str, *, method: str = "GET", payload: dict | None = None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, headers=headers, method=method)
    with request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw or "{}")


def extract_register_block(response_payload: dict) -> dict:
    data = response_payload.get("data") or {}
    if "register_bank" in data:
        data = data["register_bank"]
    return data


def extract_registers(response_payload: dict) -> dict:
    block = extract_register_block(response_payload)
    return block.get("registers") or {}


def print_json(title: str, payload: dict):
    print(f"\n== {title} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Smoke test the Raspberry Pi bridge talking to the STM32 I2C slave.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--cmd", type=int, default=1)
    parser.add_argument("--floor-id", type=int, default=1)
    parser.add_argument("--cell-id", type=int, default=0)
    parser.add_argument("--cid", type=int, default=1)
    parser.add_argument("--title", default="Smoke Test Book")
    parser.add_argument("--skip-reset", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    dispatch_payload = {
        "cmd": args.cmd,
        "floor_id": args.floor_id,
        "cell_id": args.cell_id,
        "cid": args.cid,
        "title": args.title,
    }

    try:
        health = http_json(f"{base_url}/health")
        print_json("Health", health)

        if not args.skip_reset:
            reset_payload = http_json(f"{base_url}/reset", method="POST", payload={})
            print_json("Reset", reset_payload)

        dispatch = http_json(f"{base_url}/dispatch", method="POST", payload=dispatch_payload)
        print_json("Dispatch", dispatch)

        registers = http_json(f"{base_url}/registers")
        print_json("Registers After Dispatch", registers)
        final_registers = extract_registers(registers)
        ack = final_registers.get("reg4")
        if ack == 0:
            print("\nSTM32 accepted the command and cleared reg0.")
        else:
            print(f"\nBridge returned, current reg4={ack}.")
        return 0

    except error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
