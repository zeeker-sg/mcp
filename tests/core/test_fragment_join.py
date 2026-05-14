"""Phase 5 — pure unit tests for `core/fragment_join.py`.

Covers `normalize_url` (8 input/expected pairs per 05-RESEARCH §4.8) and the
`compile_filter` skeleton (NotImplementedError until Plan 05-02 body-fills).
GREEN today.
"""

import anyio
import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Scheme + netloc lowercased; path preserved
        ("https://Example.Gov.SG/Decision", "https://example.gov.sg/Decision"),
        # Trailing slash stripped (non-root path)
        ("https://example.gov.sg/decision/", "https://example.gov.sg/decision"),
        # http → https scheme upgrade
        ("http://example.gov.sg/decision", "https://example.gov.sg/decision"),
        # Root path stays
        ("https://example.gov.sg/", "https://example.gov.sg/"),
        # Whitespace stripped
        ("  https://example.gov.sg/x  ", "https://example.gov.sg/x"),
        # Empty stays empty
        ("", ""),
        # Query string preserved
        ("https://example.gov.sg/page?q=1&v=2", "https://example.gov.sg/page?q=1&v=2"),
        # Fragment preserved
        ("https://example.gov.sg/page#frag", "https://example.gov.sg/page#frag"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    from mcp_zeeker.core.fragment_join import normalize_url

    assert normalize_url(raw) == expected


def test_compile_filter_skeleton_raises_not_implemented() -> None:
    """The skeleton is RED-until-Plan-05-02 by design. This test verifies the
    skeleton stays a skeleton; Plan 05-02 deletes this test when body-filling."""
    from mcp_zeeker.core.fragment_join import compile_filter

    async def _check() -> None:
        with pytest.raises(NotImplementedError, match="Plan 05-02"):
            await compile_filter("zeeker-judgements", "judgments_fragments", [])

    anyio.run(_check)
