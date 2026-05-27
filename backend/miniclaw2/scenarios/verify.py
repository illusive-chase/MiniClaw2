"""Run a scenario's verify.sh and return the result."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ..domain import Project
from .loader import load_scenario


@dataclass(slots=True)
class VerifyResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_verify(project: Project, *, timeout: float = 60.0) -> VerifyResult:
    """Execute the scenario's verify.sh in ``project.root_path``.

    Returns a :class:`VerifyResult` describing the outcome. Raises
    ``ValueError`` if the project has no associated scenario, or the
    scenario fails to load.
    """
    if not project.scenario_name:
        raise ValueError("project has no scenario_name; cannot verify")

    scenario = load_scenario(project.scenario_name)
    verify_path = scenario.verify_path

    # Make the script executable on disk so we can invoke it directly.
    try:
        mode = verify_path.stat().st_mode
        verify_path.chmod(mode | 0o111)
    except OSError:
        pass

    env = dict(os.environ)
    env.setdefault("CI", "1")
    env["MINICLAW_PROJECT_ID"] = project.id
    # MINICLAW_HOME is already set if the user overrode it; otherwise
    # we inherit the same default as Store (~/.miniclaw2). Setting it
    # explicitly here means verify.sh doesn't have to guess.
    if "MINICLAW_HOME" not in env:
        from pathlib import Path
        env["MINICLAW_HOME"] = str(Path.home() / ".miniclaw2")

    try:
        result = subprocess.run(
            ["bash", str(verify_path)],
            cwd=project.root_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return VerifyResult(
            exit_code=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            timed_out=True,
        )

    return VerifyResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
