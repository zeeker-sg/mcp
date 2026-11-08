"""Capture the baseline tool definitions over MCP Streamable HTTP."""

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def capture(url):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            if initialized.server_info.name != "zeeker":
                raise RuntimeError(f"Unexpected MCP server: {initialized.server_info.name}")
            result = await session.list_tools()
            if result.next_cursor is not None or len(result.tools) != 6:
                raise RuntimeError("Expected a single MCP response containing all six tools")
            return [
                tool.model_dump(mode="json")
                for tool in sorted(result.tools, key=lambda tool: tool.name)
            ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_checkout", type=Path, help="Pristine baseline checkout")
    parser.add_argument("output", type=Path, help="New path for the candidate fixture")
    args = parser.parse_args()

    source = args.base_checkout.resolve() / "src"
    if not (source / "mcp_zeeker" / "app.py").is_file():
        parser.error(f"No Zeeker MCP application under {source}")

    os.environ.update(
        UPSTREAM_URL="http://127.0.0.1:1",
        FTS_WARMER_ENABLED="0",
        ZEEKER_FULL_ACCESS_TOKEN="",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    sys.path.insert(0, str(source))
    from mcp_zeeker.app import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Uvicorn failed to start")
                time.sleep(0.02)
            tools = asyncio.run(capture(f"http://127.0.0.1:{port}/mcp/"))
            with args.output.open("x", encoding="utf-8") as output:
                output.write(json.dumps(tools, indent=2, sort_keys=True) + "\n")
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
