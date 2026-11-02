"""Exercise the actual workflow's persistence shell against temporary Git remotes."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WorkflowPersistenceTests(unittest.TestCase):
    def git(self, directory, *arguments):
        return subprocess.run(
            ["git", "-c", "user.name=Monitor Test", "-c", "user.email=test@example.invalid",
             *arguments], cwd=directory, text=True, capture_output=True, check=True,
        ).stdout

    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.root, "clone", str(self.remote), "working")
        self.working = self.root / "working"
        (self.working / "state").mkdir()
        self.state_path = self.working / "state" / "price_state.json"
        self.state_path.write_text('{"price": "3400.00"}\n', encoding="utf-8")
        self.git(self.working, "add", "state/price_state.json")
        self.git(self.working, "commit", "-m", "Initial state")
        self.git(self.working, "push", "origin", "main")

    def run_persistence(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "monitor.yml").read_text()
        persistence_step = workflow.split("      - name: Commit and push changed JSON state\n")[1]
        script_block = persistence_step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        environment = dict(os.environ, DEFAULT_BRANCH="main")
        return subprocess.run(
            ["bash", "-c", textwrap.dedent(script_block)], cwd=self.working,
            env=environment, text=True, capture_output=True, timeout=30,
        )

    def remote_state(self):
        return self.git(self.remote, "show", "main:state/price_state.json")

    def test_pushes_changed_state_without_unrelated_changes(self):
        desired_state = '{"price": "2990.00"}\n'
        self.state_path.write_text(desired_state, encoding="utf-8")
        (self.working / "unrelated.txt").write_text("do not commit", encoding="utf-8")
        result = self.run_persistence()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_state(), desired_state)
        self.assertEqual(self.git(self.remote, "ls-tree", "-r", "--name-only", "main").strip(),
                         "state/price_state.json")

    def test_unchanged_state_does_not_create_commit(self):
        original_commit = self.git(self.remote, "rev-parse", "main")
        result = self.run_persistence()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), original_commit)

    def test_conflict_preserves_local_recovery_json_and_remote_changes(self):
        self.git(self.root, "clone", str(self.remote), "peer")
        peer = self.root / "peer"
        remote_state = '{"price": "3100.00"}\n'
        (peer / "state" / "price_state.json").write_text(remote_state, encoding="utf-8")
        self.git(peer, "add", "state/price_state.json")
        self.git(peer, "commit", "-m", "Concurrent state update")
        self.git(peer, "push", "origin", "main")
        desired_state = '{"price": "2990.00"}\n'
        self.state_path.write_text(desired_state, encoding="utf-8")
        result = self.run_persistence()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), desired_state)
        self.assertEqual(self.remote_state(), remote_state)
        self.assertFalse((self.working / ".git" / "rebase-merge").exists())
