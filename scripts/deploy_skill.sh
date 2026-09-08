#!/usr/bin/env bash
# Deploy the real-information-analysis skill as a self-contained bundle.
#
# Why a script: SKILL.md Step 4 imports real_information_analysis directly,
# so a skill copy without the package only executes when the agent happens
# to sit in the repo working directory — a silently non-runnable deployment
# (found in the 2026-09 audit), and the manual copy that preceded this
# script had already drifted one version behind the repo.
#
# Bundle: SKILL.md + references/ + the (pure-stdlib) package itself.
# Self-verifying: refuses to deploy on version drift, and proves the
# deployed bundle imports from a neutral cwd before declaring success.
#
# Usage: scripts/deploy_skill.sh [DEST]
#   DEST defaults to ~/.agents/skills/real-information-analysis
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/.agents/skills/real-information-analysis}"

SKILL_VERSION="$(sed -n 's/^version: //p' "$REPO_ROOT/SKILL.md" | head -1)"
PKG_VERSION="$(python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from real_information_analysis._version import __version__ as v; print(v)")"

if [ -z "$SKILL_VERSION" ]; then
  echo "[deploy] cannot read version from SKILL.md frontmatter" >&2
  exit 1
fi
if [ "$SKILL_VERSION" != "$PKG_VERSION" ]; then
  echo "[deploy] version drift: SKILL.md=$SKILL_VERSION but _version.py=$PKG_VERSION — fix before deploying" >&2
  exit 1
fi

mkdir -p "$DEST"
rm -rf "$DEST/real_information_analysis" "$DEST/references"
cp "$REPO_ROOT/SKILL.md" "$DEST/SKILL.md"
cp -R "$REPO_ROOT/references" "$DEST/references"
cp -R "$REPO_ROOT/real_information_analysis" "$DEST/real_information_analysis"
find "$DEST/real_information_analysis" -name '__pycache__' -type d -exec rm -rf {} +

# Prove the deployed bundle works from a neutral cwd (the failure mode this
# script exists to prevent: skill deploys but cannot execute).
DEPLOYED="$DEST" EXPECTED="$SKILL_VERSION" python3 - <<'EOF'
import os
import sys

dest = os.environ["DEPLOYED"]
sys.path.insert(0, dest)
import real_information_analysis as ria

version = ria.__version__
assert version == os.environ["EXPECTED"], f"deployed version {version} != {os.environ['EXPECTED']}"
# The headline imports SKILL.md Step 4 performs, plus the newer guards.
from real_information_analysis import (  # noqa: F401
    PolymarketProvider,
    scalar_event_consistency,
    ledger_coherence_lint,
)
print(f"[deploy] bundle imports OK from neutral cwd (v{version})")
EOF

echo "[deploy] $DEST updated to v$SKILL_VERSION"
