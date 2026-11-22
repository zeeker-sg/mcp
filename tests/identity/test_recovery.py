import sqlite3

import pytest

from zeeker_identity.db import open_registry
from zeeker_identity.errors import RegistryError
from zeeker_identity.recovery import backup, restore_copy

from .conftest import claims, decide, state


def test_restore_is_separate_and_preserves_post_backup_decisions(registry, imported):
    backup(registry, "before-review")
    decide(registry, claims(registry, "individual_review")[0]["claim_id"])
    authoritative = state(registry)
    result = restore_copy(registry, "before-review", "old-snapshot")
    assert result["promoted"] is False
    assert state(registry) == authoritative
    with sqlite3.connect(registry / "restores" / result["artifact"]) as restored:
        assert restored.execute("SELECT count(*) FROM review_decision").fetchone()[0] == 0
    with open_registry(registry, readonly=True) as live:
        assert live.execute("SELECT count(*) FROM review_decision").fetchone()[0] == 1


@pytest.mark.parametrize("name", ["../registry", "/registry", "a/b", "", ".."])
def test_no_restore_promotion_or_path_traversal_surface(registry, imported, name):
    backup(registry, "safe")
    before = state(registry)
    with pytest.raises(RegistryError):
        restore_copy(registry, "safe", name)
    assert state(registry) == before


def test_artifact_overwrite_refused(registry, imported):
    original = backup(registry, "unique")
    with pytest.raises(RegistryError):
        backup(registry, "unique")
    assert restore_copy(registry, "unique", "copy")["sha256"] == original["sha256"]
    with pytest.raises(RegistryError):
        restore_copy(registry, "unique", "copy")
