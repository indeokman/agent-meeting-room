#!/usr/bin/env python3
"""Zero-dependency stdio <-> HTTP MCP Bridge for Claude Code & Hermes.

Useful in corporate networks where:
1. A client version prefers `stdio` MCP servers over direct `streamable_http`, or
2. Corporate `HTTP_PROXY` / `HTTPS_PROXY` environment variables interfere with internal
   Linux server IPs (this bridge explicitly bypasses proxies for internal LAN calls).

Usage:
  python3 mcp_stdio_bridge.py --url http://10.20.30.50:8080/mcp --agent-id agent_a
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Meeting Room stdio MCP Bridge")
    parser.add_argument("--url", required=True, help="Meeting Room /mcp URL (e.g. http://10.20.30.50:8080/mcp)")
    parser.add_argument("--agent-id", default="agent_a", help="Agent ID (agent_a .. agent_d)")
    parser.add_argument("--token", default="", help="Optional API token when AMR_AUTH_MODE=token")
    args = parser.parse_args()

    sep = "&" if "?" in args.url else "?"
    endpoint = f"{args.url}{sep}agent_id={urllib.parse.quote(args.agent_id)}&client=StdioBridge"

    # Bypass corporate HTTP_PROXY so internal Linux lab IPs connect directly
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = line.encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Agent-Id": args.agent_id,
            }
            if args.token:
                headers["Authorization"] = f"Bearer {args.token}"

            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            with opener.open(req, timeout=3600) as resp:
                if resp.status == 202:
                    continue
                body = resp.read().decode("utf-8").strip()
                if body:
                    sys.stdout.write(body + "\n")
                    sys.stdout.flush()
        except Exception as exc:
            try:
                msg = json.loads(line)
                req_id = msg.get("id")
            except Exception:
                req_id = None
            if req_id is not None:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Bridge connection error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
