#!/bin/sh
# Install the better-crawler dsh bundle into a DeepSeek Harness profile and
# publish its skill.
#
#   ./install.sh [profile]        # profile defaults to "web"
#
# The repository root is the bundle package (package.json declares
# dsh.bundle.patch), so installing is one package-manager call:
#
#   1. bundle -> dsh plugin --profile <profile> add file:<repo>
#   2. skill  -> $DSH_HOME/skills/web-to-text, which every profile discovers
#
# Nothing is staged or rewritten: the bundle's cordis.patch.yml computes its
# launcher path at boot from the running profile's directory, so the registry
# entry keeps working no matter where pnpm installs the package. The same
# package installs straight from GitHub:
#
#   dsh plugin --profile web add github:TC635807/better-crawler4agent
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/.." && pwd)
PROFILE=${1:-web}
DSH_HOME=${DSH_HOME:-$HOME/.dsh}

command -v dsh >/dev/null 2>&1 || { echo "install.sh: dsh is not on PATH" >&2; exit 1; }
[ -f "$REPO/package.json" ] || { echo "install.sh: no bundle manifest at $REPO/package.json" >&2; exit 1; }
[ -f "$REPO/scripts/launch.py" ] || { echo "install.sh: expected the crawler repo at $REPO" >&2; exit 1; }

echo "==> bundle '$REPO' -> profile '$PROFILE'"
dsh plugin --profile "$PROFILE" add "file:$REPO"

echo "==> skill web-to-text -> $DSH_HOME/skills/web-to-text"
mkdir -p "$DSH_HOME/skills"
if [ -L "$DSH_HOME/skills/web-to-text" ] || [ ! -e "$DSH_HOME/skills/web-to-text" ]; then
	ln -sfn "$REPO/skills/web-to-text" "$DSH_HOME/skills/web-to-text"
else
	echo "install.sh: kept existing $DSH_HOME/skills/web-to-text (not a symlink)" >&2
fi

# A package-manager copy carries only the committed files, and .venv/ plus
# .playwright/ are gitignored — they do not travel with it. When this clone has
# them, point the row back here so the launcher uses them.
if [ -d "$REPO/.venv" ] || [ -d "$REPO/.playwright" ]; then
	echo "note: the installed copy has no .venv/.playwright. To use this clone's, add to"
	echo "      $DSH_HOME/profiles/$PROFILE/cordis.patch.yml:"
	echo "      - id: mcp-better-crawler"
	echo "        config:"
	echo "          env:"
	echo "            BETTER_CRAWLER_REPO: $REPO"
fi

echo "done. restart dsh if the profile is already running."
