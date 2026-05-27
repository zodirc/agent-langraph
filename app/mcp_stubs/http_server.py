#!/usr/bin/env python3
"""Minimal MCP-style HTTP server for integration tests (tools/resources/prompts)."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

TOOLS = [
    {
        "name": "echo",
        "description": "Echo message over HTTP MCP stub",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        },
    },
]

RESOURCES = [
    {
        "uri": "https://example.com/docs/guide",
        "name": "guide",
        "description": "HTTP stub guide resource",
        "mimeType": "text/plain",
    },
]

PROMPTS = [
    {
        "name": "qa_template",
        "description": "Simple QA prompt template",
        "arguments": [{"name": "question", "required": True}],
    },
]


class _MCPHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/tools":
            self._send_json({"tools": TOOLS})
        elif path == "/resources":
            self._send_json({"resources": RESOURCES})
        elif path == "/prompts":
            self._send_json({"prompts": PROMPTS})
        elif path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/tools/") and path.endswith("/call"):
            tool_name = path.split("/")[2]
            args = self._read_json().get("arguments", {})
            if tool_name == "echo":
                self._send_json({"echo": args.get("message", "")})
            else:
                self._send_json({"error": f"unknown tool {tool_name}"}, status=404)
        elif path == "/resources/read":
            uri = self._read_json().get("uri", "")
            self._send_json({"contents": [{"uri": uri, "text": "HTTP stub resource body"}]})
        else:
            self._send_json({"error": "not found"}, status=404)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    server = HTTPServer((host, port), _MCPHTTPHandler)
    bound_host, bound_port = server.server_address[0], server.server_address[1]
    print(
        json.dumps({"url": f"http://{bound_host}:{bound_port}", "status": "listening"}),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
