# Legacy MCP tool fixture

`legacy-tools.json` records the six tool definitions served by the pristine
pre-registry baseline at `36814fc5c35d4d169c613f7d875112f0d0b8668f`. The capture
uses MCP `initialize` and `tools/list` over Streamable HTTP. It sorts definitions
by name and sorts JSON keys to make changes easy to review.

From the registry checkout, use its installed development virtual environment
and pass the path to a separate, clean checkout of the baseline commit. Choose
a candidate path that does not already exist.

```sh
.venv/bin/python scripts/capture_legacy_tools.py /path/to/baseline /tmp/legacy-tools-candidate.json
diff -u tests/identity/fixtures/legacy-tools.json /tmp/legacy-tools-candidate.json
```

The capture binds an ephemeral loopback port, points the Datasette upstream at
loopback, disables the background warmer, and clears the upstream access token.
The only MCP requests are for initialization and tool definitions. Review any
diff before replacing the fixture when intentionally changing the baseline.
