"""Keep the whole test session off the developer's real store.

Tests that build their own isolated store already pass an explicit root,
but a bare ``Store()`` resolves ``MINICLAW_HOME`` and otherwise lands on
``~/.miniclaw2``. Touching the real store is never harmless: ``Store()``
stamps ``schema.json`` to the current ``SCHEMA_VERSION``, which turns a
concurrently running older backend read-only, and ``ProjectRegistry``
initialization sweeps every live node to CANCELLED. Point the variable at
a session-scoped temporary directory so a missing root argument degrades
to an empty throwaway store instead of the developer's own graph.

Only ``MINICLAW_HOME`` is set. ContextSpace defaults to ``$MINICLAW_HOME/
contextspace``, and tests that override the home alone rely on the two
staying together — pinning ``MINICLAW_CONTEXT_HOME`` here would split them
and strand those tests' migrations in this fixture's directory.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_miniclaw_home() -> Iterator[None]:
    previous = os.environ.get("MINICLAW_HOME")
    with tempfile.TemporaryDirectory(prefix="miniclaw-tests-") as directory:
        os.environ["MINICLAW_HOME"] = os.path.join(directory, "home")
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("MINICLAW_HOME", None)
            else:
                os.environ["MINICLAW_HOME"] = previous
