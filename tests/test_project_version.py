from __future__ import annotations

import unittest

from backend.project_version import PROJECT_VERSION, SEMVER_PATTERN, load_project_version


class ProjectVersionTests(unittest.TestCase):
    def test_authoritative_version_is_valid_semver(self):
        self.assertRegex(PROJECT_VERSION, SEMVER_PATTERN)
        self.assertEqual(load_project_version(), PROJECT_VERSION)


if __name__ == "__main__":
    unittest.main()
