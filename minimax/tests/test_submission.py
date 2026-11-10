import difflib
import filecmp
import unittest
from pathlib import Path

from minimax_sdd.tests_runner import run_items_tests

ROOT = Path(__file__).resolve().parents[1]


def unified_diff() -> str:
    before = (ROOT / "snapshots" / "before" / "items.py").read_text(encoding="utf-8").splitlines(True)
    after = (ROOT / "src" / "items.py").read_text(encoding="utf-8").splitlines(True)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile="snapshots/before/items.py",
            tofile="src/items.py",
        )
    )


class SubmissionTest(unittest.TestCase):
    def test_after_snapshot_matches_current_code(self):
        self.assertTrue(
            filecmp.cmp(
                ROOT / "src" / "items.py",
                ROOT / "snapshots" / "after" / "items.py",
                shallow=False,
            )
        )

    def test_checked_in_diff_matches_the_two_snapshots(self):
        checked = (ROOT / "records" / "code_diff.txt").read_text(encoding="utf-8")
        self.assertEqual(checked, unified_diff())

    def test_same_suite_fails_before_and_passes_after(self):
        before_code, before_log = run_items_tests("snapshots/before")
        after_code, after_log = run_items_tests("src")
        self.assertEqual(before_code, 1)
        self.assertIn("AssertionError", before_log)
        self.assertIn("test_filter_by_status", before_log)
        self.assertIn("FAIL", before_log)
        self.assertNotIn("ImportError", before_log)
        self.assertNotIn("ModuleNotFoundError", before_log)
        self.assertEqual(after_code, 0)
        self.assertIn("code_version: items-before-1", before_log)
        self.assertIn("code_version: items-after-1", after_log)
        self.assertIn("exit_code: 1", before_log)
        self.assertIn("exit_code: 0", after_log)

    def test_development_record_names_the_same_change(self):
        record = (ROOT / "records" / "minimax-session.md").read_text(encoding="utf-8")
        diff = (ROOT / "records" / "code_diff.txt").read_text(encoding="utf-8")
        self.assertIn("minimax/MiniMax-M3.1-Flash", record)
        self.assertIn("specs/items.md", record)
        self.assertIn("items.list", record)
        self.assertIn("test_filter_by_status", record)
        self.assertIn("test_list_sorted_by_time", record)
        self.assertIn("if status is None:", diff)
        self.assertIn("if status is None:", record)

    def test_required_materials_are_present(self):
        required = [
            "README.md",
            "RUNBOOK.md",
            "sample.md",
            "tech-note.md",
            "requirements.txt",
            "src/items.py",
            "snapshots/before/items.py",
            "snapshots/after/items.py",
            "specs/items.md",
            "specs/items-updated.md",
            "tests/test_items.py",
            "records/code_diff.txt",
            "records/minimax-session.md",
            "records/seekdb-queries.md",
            "records/powercontext-state.md",
            "records/test_run_before.log",
            "records/test_run_after.log",
        ]
        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual(missing, [], f"缺少提交材料：{missing}")

    def test_runbook_covers_both_external_services(self):
        runbook = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("seekdb", runbook)
        self.assertIn("PowerContext", runbook)
        self.assertIn("2881", runbook)
        self.assertIn("minimax_sdd doctor", runbook)

    def test_logs_record_command_version_and_exit_code(self):
        for name, version, code in (
            ("test_run_before.log", "items-before-1", "exit_code: 1"),
            ("test_run_after.log", "items-after-1", "exit_code: 0"),
        ):
            text = (ROOT / "records" / name).read_text(encoding="utf-8")
            self.assertIn("command:", text, name)
            self.assertIn(f"code_version: {version}", text, name)
            self.assertIn(code, text, name)

    def test_evidence_has_no_paths_from_another_machine(self):
        """日志必须由本仓库跑出来，不能混入别的机器上的路径。"""
        stale = ("F:\\contest", "F:/contest", "/home/runner")
        for path in sorted((ROOT / "records").rglob("*")):
            if not path.is_file() or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in stale:
                self.assertNotIn(marker, text, f"{path} 含有其它机器的路径 {marker}")


if __name__ == "__main__":
    unittest.main()
