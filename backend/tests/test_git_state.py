from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from miniclaw2.git_state import commit_all, ensure_miniclaw_git_excluded


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=path, check=True
    )
    return _head(path)


def _head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class GitStateTest(unittest.TestCase):
    def test_commit_all_excludes_miniclaw_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            (repo / "real.txt").write_text("real\n", encoding="utf-8")
            generated = repo / ".miniclaw2" / "graph" / "lanes" / "l1"
            generated.mkdir(parents=True)
            (generated / "preview.json").write_text("{}", encoding="utf-8")

            new_head, err = commit_all(str(repo), "commit real changes")

            self.assertIsNone(err)
            self.assertIsNotNone(new_head)
            show = subprocess.run(
                ["git", "show", "--name-only", "--format=", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertIn("real.txt", show)
            self.assertNotIn(".miniclaw2/graph/lanes/l1/preview.json", show)

    def test_commit_all_ignores_miniclaw_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            initial = _init_repo(repo)
            generated = repo / ".miniclaw2" / "outputs" / "n1"
            generated.mkdir(parents=True)
            (generated / "artifact.txt").write_text("generated\n", encoding="utf-8")

            new_head, err = commit_all(str(repo), "generated only")

            self.assertIsNone(err)
            self.assertIsNone(new_head)
            self.assertEqual(_head(repo), initial)

    def test_ensure_miniclaw_git_excluded_appends_once(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)

            self.assertIsNone(ensure_miniclaw_git_excluded(str(repo)))
            self.assertIsNone(ensure_miniclaw_git_excluded(str(repo)))

            exclude = (repo / ".git" / "info" / "exclude").read_text(
                encoding="utf-8"
            )
            self.assertEqual(exclude.splitlines().count(".miniclaw2/"), 1)


if __name__ == "__main__":
    unittest.main()
