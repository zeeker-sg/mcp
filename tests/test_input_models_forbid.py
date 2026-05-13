"""
ANNO-04: All Pydantic input models in tools/*_models.py use extra='forbid'.

Iterates over every BaseModel subclass in the three tool model modules and asserts
that model_config["extra"] == "forbid". Collects the full list so a count check
catches a future regression where someone removes a model.
"""

from __future__ import annotations

import inspect

from pydantic import BaseModel

import mcp_zeeker.tools.discovery_models as discovery_models
import mcp_zeeker.tools.retrieval_models as retrieval_models
import mcp_zeeker.tools.search_models as search_models


def _collect_models(module) -> list[type[BaseModel]]:
    """Return all BaseModel subclasses defined in the given module."""
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module.__name__
    ]


def test_all_tool_input_models_use_extra_forbid():
    """ANNO-04: Every input model in tools/*_models.py uses model_config extra='forbid'."""
    all_models: list[type[BaseModel]] = []
    for module in (discovery_models, retrieval_models, search_models):
        all_models.extend(_collect_models(module))

    # Sanity: at least 6 models must exist (3 discovery + 2 retrieval + 1 search)
    assert len(all_models) >= 6, (
        f"Expected >= 6 input models across tool modules, found {len(all_models)}: "
        f"{[m.__name__ for m in all_models]}"
    )

    failures = []
    for cls in all_models:
        extra_setting = cls.model_config.get("extra")
        if extra_setting != "forbid":
            failures.append(f"{cls.__name__}: extra={extra_setting!r}")

    assert not failures, "The following models do not use extra='forbid':\n" + "\n".join(failures)
