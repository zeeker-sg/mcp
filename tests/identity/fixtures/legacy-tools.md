# Legacy MCP tool fixture

`legacy-tools.json` records the six tool definitions served by the MCP server.
The capture uses MCP `initialize` and `tools/list` over Streamable HTTP. It
sorts definitions by name and sorts JSON keys to make changes easy to review.

## Provenance

- Originally captured from the pristine pre-registry baseline at
  `36814fc5c35d4d169c613f7d875112f0d0b8668f` (fastmcp 3.4 era), proving the
  registry increment changed nothing in the served contract. That capture is
  preserved in git history.
- Refreshed for the fastmcp 4 migration (`~=4.2`, mcp SDK v2): the
  pristine-baseline checkout can no longer run under the fastmcp 4 venv (the
  baseline imports `fastmcp.tools.tool`, removed in fastmcp 4), so the fixture
  is now captured from the migrated checkout itself. The refresh diff against
  the 3.4-era capture was reviewed and contains only framework-shape changes:
  snake_case `model_dump` keys (`inputSchema` → `input_schema`, hint keys) and
  tool-level `title` auto-derived from the tool name. Descriptions, schemas and
  annotation values are unchanged.

## Refreshing

Use the installed development virtual environment of the checkout being
captured. Choose a candidate path that does not already exist.

```sh
.venv/bin/python scripts/capture_legacy_tools.py /path/to/checkout /tmp/legacy-tools-candidate.json
diff -u tests/identity/fixtures/legacy-tools.json /tmp/legacy-tools-candidate.json
```

The capture binds an ephemeral loopback port, points the Datasette upstream at
loopback, disables the background warmer, and clears the upstream access token.
The only MCP requests are for initialization and tool definitions. Review any
diff before replacing the fixture; the boundary test pins whatever the server
actually serves, so an unexplained diff means the served contract changed.
