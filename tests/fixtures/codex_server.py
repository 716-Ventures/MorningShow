"""Offline JSON-lines peer for testing the real subprocess transport."""

import json
import sys
import time

mode = sys.argv[1]
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if mode == "eof":
        break
    if mode == "timeout":
        time.sleep(30)
        break
    if mode == "malformed":
        print("not-json", flush=True)
        break
    if mode == "array":
        print("[]", flush=True)
        break
    if mode == "large":
        print("x" * 2_100_000, flush=True)
        break
    if mode == "params":
        print(json.dumps({"method": "test", "params": None}), flush=True)
        break
    if mode == "tool":
        print(
            json.dumps({"id": 99, "method": "item/commandExecution/requestApproval", "params": {}}),
            flush=True,
        )
        break
    if mode == "flood":
        for _ in range(2050):
            print(json.dumps({"method": "notification", "params": {}}), flush=True)
        break
    print(json.dumps({"method": "notice", "params": {"ready": True}}), flush=True)
    if mode == "error":
        result = {"error": {"message": "private-secret-provider-error"}}
    elif mode == "wrong-result":
        result = {"result": []}
    else:
        result = {"result": {"account": None, "echo": request.get("params", {})}}
    print(json.dumps({"id": request["id"], **result}), flush=True)
