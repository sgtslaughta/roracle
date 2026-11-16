import json

HDRS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(mc, headers, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return mc.post("/mcp", headers={**HDRS, **headers}, content=json.dumps(body))


def call(mc, headers, tool, **args):
    r = rpc(mc, headers, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"] if res["content"] else ""
    return res.get("isError", False), res.get("structuredContent") or (
        json.loads(text) if not res.get("isError") else text
    )
