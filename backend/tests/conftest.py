"""Keep the whole test session off the developer's real store.

Tests that build their own isolated store already pass an explicit root,
but a bare ``Store()`` resolves ``MINICLAW_HOME`` and otherwise lands on
``~/.miniclaw2``. Touching the real store is never harmless: ``Store()``
stamps ``schema.json`` to the current ``SCHEMA_VERSION``, which turns a
concurrently running older backend read-only, and ``ProjectRegistry``
initialization sweeps every live node to CANCELLED. Point the variable at
a per-test temporary directory so a missing root argument degrades
to an empty throwaway store instead of the developer's own graph.

每个测试开始时解除外部 ContextSpace 覆盖，结束时恢复原环境。
未显式覆盖的 ContextSpace 始终跟随该测试的 MINICLAW_HOME，避免失败测试污染后续用例。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_miniclaw_home() -> Iterator[None]:
    previous = os.environ.get("MINICLAW_HOME")
    previous_context = os.environ.pop("MINICLAW_CONTEXT_HOME", None)
    with tempfile.TemporaryDirectory(prefix="miniclaw-tests-") as directory:
        os.environ["MINICLAW_HOME"] = os.path.join(directory, "home")
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("MINICLAW_HOME", None)
            else:
                os.environ["MINICLAW_HOME"] = previous
            if previous_context is None:
                os.environ.pop("MINICLAW_CONTEXT_HOME", None)
            else:
                os.environ["MINICLAW_CONTEXT_HOME"] = previous_context
