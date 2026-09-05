"""Keep SKILL.md frontmatter version in sync with the package version.

The SKILL.md is user-facing methodology and the package is the tooling behind
it — they ship together, and a silent drift between the two versions is how a
"docs are behind the code" rot starts (PRODUCTIZATION_PLAN.md §六).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_information_analysis import __version__

SKILL_PATH = ROOT / "SKILL.md"


class VersionConsistencyTests(unittest.TestCase):
    def test_skill_md_frontmatter_version_matches_package(self) -> None:
        frontmatter = SKILL_PATH.read_text().split("---", 2)[1]
        match = re.search(r"^version:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        self.assertIsNotNone(match, "SKILL.md frontmatter has no version field")
        self.assertEqual(match.group(1), __version__)  # type: ignore[union-attr]

    def test_skill_md_name_matches_directory_name(self) -> None:
        frontmatter = SKILL_PATH.read_text().split("---", 2)[1]
        match = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        self.assertIsNotNone(match, "SKILL.md frontmatter has no name field")
        self.assertEqual(match.group(1), "real-information-analysis")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
