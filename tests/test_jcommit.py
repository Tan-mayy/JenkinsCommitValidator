import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import jcommit  # noqa: E402

DESC = "x" * 200


def msg(head="Bug: SC-991", title="Title: Fix thing", desc="Description: " + DESC):
    return "\n".join([head, title, desc]) + "\n"


class ValidateTest(unittest.TestCase):
    def ok(self, m):
        errors, _ = jcommit.validate(m)
        self.assertEqual(errors, [])

    def bad(self, m, fragment):
        errors, _ = jcommit.validate(m)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_valid(self):
        self.ok(msg())

    def test_all_types_and_prefixes(self):
        for t in jcommit.TYPES:
            for p in jcommit.JIRA_PREFIXES:
                self.ok(msg(head="%s: %s-12" % (t, p)))

    def test_bad_type(self):
        self.bad(msg(head="Fix: SC-1"), "Type 'Fix'")

    def test_bad_prefix(self):
        self.bad(msg(head="Bug: ABC-1"), "JIRA-ID")

    def test_placeholder(self):
        self.bad(msg(head="Bug: SMSD-0000"), "placeholder")

    def test_short_description_reports_deficit(self):
        self.bad(msg(desc="Description: " + "x" * 150), "50 more")

    def test_199_fails_200_passes(self):
        self.bad(msg(desc="Description: " + "x" * 199), "1 more")
        self.ok(msg(desc="Description: " + "x" * 200))

    def test_too_few_lines(self):
        self.bad("Bug: SC-1\nTitle: a\n", "3 lines")

    def test_missing_title_label(self):
        self.bad(msg(title="Fix thing"), "line 2")

    def test_git_comments_ignored(self):
        self.ok("# comment\n" + msg() + "# more\n")

    def test_merge_commit_passes_with_warning(self):
        errors, warnings = jcommit.validate("Merge branch 'a' into b\n")
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_trailer_is_warning_not_error(self):
        errors, warnings = jcommit.validate(msg() + "\nCo-Authored-By: someone\n")
        self.assertEqual(errors, [])
        self.assertTrue(warnings)


class HelpersTest(unittest.TestCase):
    def test_branch_parsing(self):
        self.assertEqual(jcommit.jira_from_branch("SC-663-idbi-consent-initiate-debug"), "SC-663")
        self.assertEqual(jcommit.jira_from_branch("feature/SMSD-42-thing"), "SMSD-42")
        self.assertIsNone(jcommit.jira_from_branch("develop"))
        self.assertIsNone(jcommit.jira_from_branch("DISC-9-x"))  # not a real prefix

    def test_build_collapses_whitespace(self):
        m = jcommit.build_message("Bug", "SC-1", " T ", "a\n  b   c")
        self.assertEqual(m, "Bug: SC-1\nTitle: T\nDescription: a b c\n")


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.env = dict(os.environ, JCOMMIT_CACHE=str(Path(self.tmp.name) / "cache.json"),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.run_cmd("git", "init", "-q", "-b", "SC-77-demo")
        self.run_cmd(sys.executable, str(ROOT / "jcommit.py"), "install")
        Path(self.repo, "f").write_text("1")
        self.run_cmd("git", "add", "f")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *cmd, **kw):
        return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True, **kw)

    def test_hook_blocks_bad_commit(self):
        r = self.run_cmd("git", "commit", "-m", "quick fix")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("would fail Jenkins", r.stderr)

    def test_new_commits_with_branch_jira_and_caches_title(self):
        r = self.run_cmd(sys.executable, str(ROOT / "jcommit.py"), "new", "-t", "Feat",
                         "--title", "Add demo", "-d", DESC)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.run_cmd("git", "log", "-1", "--format=%B").stdout
        self.assertTrue(log.startswith("Feat: SC-77\nTitle: Add demo\n"))
        self.assertIn("SC-77", Path(self.env["JCOMMIT_CACHE"]).read_text())

    def test_new_refuses_short_description(self):
        r = self.run_cmd(sys.executable, str(ROOT / "jcommit.py"), "new", "-t", "Bug",
                         "--title", "T", "-d", "too short")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.run_cmd("git", "log").returncode != 0, True)  # nothing committed


if __name__ == "__main__":
    unittest.main()
