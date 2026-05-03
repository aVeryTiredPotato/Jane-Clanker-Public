from __future__ import annotations

import unittest

from runtime import gitUpdate


class GitUpdateDependencyTests(unittest.TestCase):
    def test_requirements_changed_detects_root_requirements_file(self):
        self.assertTrue(gitUpdate._requirementsChanged(["requirements.txt"]))
        self.assertTrue(gitUpdate._requirementsChanged(["./requirements.txt"]))
        self.assertTrue(gitUpdate._requirementsChanged(["runtime/gitUpdate.py", "requirements.txt"]))

    def test_requirements_changed_ignores_other_paths(self):
        self.assertFalse(gitUpdate._requirementsChanged([]))
        self.assertFalse(gitUpdate._requirementsChanged(["docs/requirements.txt"]))
        self.assertFalse(gitUpdate._requirementsChanged(["requirements-dev.txt"]))


if __name__ == "__main__":
    unittest.main()
