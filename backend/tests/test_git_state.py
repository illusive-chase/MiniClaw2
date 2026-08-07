from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.git_state import (
    commit_all,
    commit_graph,
    ensure_miniclaw_git_excluded,
    git_pull_rebase,
    git_review_snapshot,
    git_status,
)


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
    def test_review_snapshot_handles_unborn_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "first.txt").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", "first.txt"], cwd=repo, check=True)

            snapshot = git_review_snapshot(str(repo))

            self.assertIsNone(snapshot.head_sha)
            self.assertEqual(snapshot.dirty_paths, ("first.txt",))
            self.assertIn("diff --git a/first.txt b/first.txt", snapshot.patch)
            self.assertIn("+first", snapshot.patch)

    def test_review_snapshot_replaces_non_utf8_diff_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            (repo / "seed.txt").write_bytes(b"changed\xff\n")

            snapshot = git_review_snapshot(str(repo))

            self.assertEqual(snapshot.dirty_paths, ("seed.txt",))
            self.assertIn("changed\ufffd", snapshot.patch)
            self.assertTrue(snapshot.diff_sha256)

    def test_review_snapshot_raises_when_tracked_diff_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            (repo / "seed.txt").write_text("changed\n", encoding="utf-8")
            failed = subprocess.CompletedProcess(
                args=["git", "diff"],
                returncode=128,
                stdout=b"",
                stderr=b"fatal: audit diff failed",
            )

            with patch("miniclaw2.git_state._git_bytes", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "audit diff failed"):
                    git_review_snapshot(str(repo))

    def test_status_degrades_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            status = git_status(raw)
            self.assertFalse(status.is_repo)
            self.assertIsNone(status.head)

    def test_status_counts_non_framework_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            head = _init_repo(repo)
            (repo / "real.txt").write_text("real\n", encoding="utf-8")
            generated = repo / ".miniclaw2" / "graph"
            generated.mkdir(parents=True)
            (generated / "preview.json").write_text("{}", encoding="utf-8")

            status = git_status(str(repo))

            self.assertTrue(status.is_repo)
            self.assertEqual(status.head, head)
            self.assertEqual(status.dirty_count, 1)

    def test_status_reports_file_states_and_line_counts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            seed = repo / "seed.txt"
            seed.write_text("seed changed\nsecond\n", encoding="utf-8")
            subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
            seed.write_text("seed changed\nsecond\nthird\n", encoding="utf-8")
            (repo / "new file.txt").write_text("alpha\nbeta", encoding="utf-8")
            (repo / "image.bin").write_bytes(b"image\x00data")

            status = git_status(str(repo))
            files = {item.path: item for item in status.files}

            self.assertEqual(status.dirty_count, 3)
            self.assertEqual(files["seed.txt"].index_status, "M")
            self.assertEqual(files["seed.txt"].worktree_status, "M")
            self.assertEqual(files["seed.txt"].additions, 3)
            self.assertEqual(files["seed.txt"].deletions, 1)
            self.assertEqual(files["new file.txt"].additions, 2)
            self.assertFalse(files["new file.txt"].binary)
            self.assertTrue(files["image.bin"].binary)

    def test_status_reports_renamed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            subprocess.run(
                ["git", "mv", "seed.txt", "renamed seed.txt"],
                cwd=repo,
                check=True,
            )

            status = git_status(str(repo))

            self.assertEqual(status.dirty_count, 1)
            self.assertEqual(status.files[0].path, "renamed seed.txt")
            self.assertEqual(status.files[0].old_path, "seed.txt")
            self.assertEqual(status.files[0].index_status, "R")
            self.assertEqual(status.files[0].additions, 0)
            self.assertEqual(status.files[0].deletions, 0)

    def test_commit_graph_orders_oldest_first_and_counts_external(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            first = _init_repo(repo)
            commits = [first]
            for index in range(2):
                (repo / f"c{index}.txt").write_text(str(index), encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-q", "-m", f"c{index}"], cwd=repo, check=True)
                commits.append(_head(repo))

            graph = commit_graph(str(repo), {commits[0], commits[2]})

            self.assertEqual([item.sha for item in graph], [commits[0], commits[2]])
            self.assertEqual(graph[1].external_count_before, 1)

    def test_commit_graph_resolves_aliases_transitively(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            live = _init_repo(repo)

            graph = commit_graph(str(repo), {"old-a"}, {"old-a": "old-b", "old-b": live})

            self.assertEqual(len(graph), 1)
            self.assertEqual(graph[0].sha, live)
            self.assertEqual(graph[0].aliases, ["old-a"])

    def test_commit_graph_interleaves_timestamped_stale_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            initial = _init_repo(repo)
            (repo / "live.txt").write_text("live\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "live"], cwd=repo, check=True)
            head = _head(repo)

            graph = commit_graph(
                str(repo),
                {initial, head, "reset-away"},
                ref_timestamps={"reset-away": 0},
            )

            self.assertEqual([item.sha for item in graph], ["reset-away", initial, head])

    def test_non_repo_commit_graph_is_deterministic_and_aliases_exclude_self(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            graph = commit_graph(raw, {"b", "a"}, {"a": "b"})

            self.assertEqual([item.sha for item in graph], ["b"])
            self.assertEqual(graph[0].aliases, ["a"])

    def test_non_repo_commit_graph_orders_by_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            graph = commit_graph(
                raw,
                {"latest", "earliest", "middle", "unobserved"},
                ref_timestamps={
                    "latest": 300,
                    "earliest": 100,
                    "middle": 200,
                },
            )

            self.assertEqual(
                [item.sha for item in graph],
                ["earliest", "middle", "latest", "unobserved"],
            )

    def test_non_repo_commit_graph_orders_resolved_alias_by_oldest_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            graph = commit_graph(
                raw,
                {"old-a", "new-a", "commit-b"},
                {"old-a": "new-a"},
                {"old-a": 100, "new-a": 300, "commit-b": 200},
            )

            self.assertEqual([item.sha for item in graph], ["new-a", "commit-b"])
            self.assertEqual(graph[0].aliases, ["old-a"])

    def test_pull_conflict_aborts_own_rebase_and_restores_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            origin = root / "origin"
            origin.mkdir()
            _init_repo(origin)
            clone = root / "clone"
            subprocess.run(
                ["git", "clone", "-q", str(origin), str(clone)], check=True
            )
            subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=clone, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
            (origin / "seed.txt").write_text("remote\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-aqm", "remote"], cwd=origin, check=True)
            (clone / "seed.txt").write_text("local\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-aqm", "local"], cwd=clone, check=True)
            local_head = _head(clone)

            head, error = git_pull_rebase(str(clone))

            self.assertIsNotNone(error)
            self.assertIn("seed.txt", error or "")
            self.assertEqual(head, local_head)
            self.assertFalse((clone / ".git" / "rebase-merge").exists())
            self.assertFalse((clone / ".git" / "rebase-apply").exists())
            porcelain = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(porcelain, "")

    def test_pull_does_not_abort_preexisting_rebase(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            _init_repo(repo)
            rebase_dir = repo / ".git" / "rebase-merge"
            rebase_dir.mkdir()
            marker = rebase_dir / "head-name"
            marker.write_text("refs/heads/main\n", encoding="utf-8")

            head, error = git_pull_rebase(str(repo))

            self.assertIsNotNone(head)
            self.assertIn("already in progress", error or "")
            self.assertTrue(marker.exists())

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

    def test_commit_all_handles_ignored_miniclaw_dir_on_unborn_branch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t.t"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "t"], cwd=repo, check=True
            )
            self.assertIsNone(ensure_miniclaw_git_excluded(str(repo)))
            (repo / "real.txt").write_text("real\n", encoding="utf-8")
            generated = repo / ".miniclaw2" / "graph"
            generated.mkdir(parents=True)
            (generated / "preview.json").write_text("{}", encoding="utf-8")

            new_head, err = commit_all(str(repo), "first commit")

            self.assertIsNone(err)
            self.assertIsNotNone(new_head)
            show = subprocess.run(
                ["git", "show", "--name-only", "--format=", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(show, ["real.txt"])

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
