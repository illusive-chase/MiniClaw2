"""Bundled integration-test scenarios — see TEST.md.

A scenario is a directory under :mod:`miniclaw2.scenarios.bundled` with:

- ``brief.md``       — one-paragraph summary
- ``scenario.yaml``  — metadata + node spec
- ``prompts/*.md``   — per-step prompt text
- ``contract.md``    — gate contract (optional)
- ``seed/``          — files copied into the tempdir at launch (optional)
- ``verify.sh``      — programmatic floor, exit 0 = pass
- ``acceptance.md``  — human checklist rendered in the Verify card

The dashboard's Tests panel calls :func:`list_scenarios` and
:func:`launch_scenario`. The Verify card calls :func:`run_verify` after
all nodes terminate.
"""

from .loader import (
    Scenario,
    ScenarioError,
    list_scenarios,
    load_scenario,
)
from .launcher import launch_scenario
from .verify import VerifyResult, run_verify

__all__ = [
    "Scenario",
    "ScenarioError",
    "VerifyResult",
    "launch_scenario",
    "list_scenarios",
    "load_scenario",
    "run_verify",
]
