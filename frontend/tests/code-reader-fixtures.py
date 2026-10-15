"""Fixtures for run-code-reader-browser.mjs, produced by the real analyser.

Printed as JSON: {path: {text, sha256, analysis}}. Generated at test time so
the browser test always exercises the current wire format.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from miniclaw2 import code_analysis as ca  # noqa: E402

big = "\n".join(f"line_{i} = {i}" for i in range(1, 3200)) + "\nNEEDLE_AT_THE_END = 'find me'\n"
files = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "class Engine:\n"
        '    """Runs things."""\n'
        "\n"
        "    def start(self, speed: int = 1) -> int:\n"
        '        """Start the engine."""\n'
        "        return speed\n"
    ),
    "main.py": (
        "from pkg.core import Engine\n"
        "\n"
        "\n"
        "def run() -> None:\n"
        "    engine = Engine()\n"
        "    engine.start(speed=2)\n"
        '    print(len("x"))\n'
        "\n"
        "\n"
        "if True:\n"
        "\tpass\n"
    ),
    "pkg/big.py": big,
}
texts = {path: ca.normalize_newlines(text) for path, text in files.items()}
results = ca.analyze_project(texts)
out = {}
for path, text in texts.items():
    sha = hashlib.sha256(text.encode()).hexdigest()
    results[path]["sha256"] = sha
    out[path] = {"text": text, "sha256": sha, "analysis": results[path]}
json.dump(out, sys.stdout)
