#!/usr/bin/env python3
"""Minimal MCP-style stdio tool server for local integration tests."""

from __future__ import annotations

import json
import sys
from typing import Any


RESOURCES = [
    {
        "uri": "file:///demo/readme.txt",
        "name": "readme",
        "description": "Demo resource for MCP resources/list",
        "mimeType": "text/plain",
    },
]

PROMPTS = [
    {
        "name": "summarize",
        "description": "Summarize provided text",
        "arguments": [{"name": "text", "required": True}],
    },
]

TOOLS = [
    {
        "name": "ping",
        "description": "Return pong with optional message",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        },
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
        },
    },
]


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    req_id = request.get("id", 1)
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": RESOURCES}}
    if method == "resources/read":
        uri = request.get("params", {}).get("uri", "")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [{"uri": uri, "text": "Demo resource content for tests."}],
            },
        }
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": PROMPTS}}
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name == "ping":
            message = arguments.get("message", "pong")
            return {"jsonrpc": "2.0", "id": req_id, "result": {"pong": message}}
        if name == "add":
            total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
            return {"jsonrpc": "2.0", "id": req_id, "result": {"sum": total}}
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"message": f"Unknown tool: {name}"},
        }
    return {"jsonrpc": "2.0", "id": req_id, "error": {"message": f"Unknown method: {method}"}}


def main() -> None:
    line = sys.stdin.readline()
    if not line:
        return
    request = json.loads(line)
    response = handle_request(request)
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
