#!/bin/sh
# Install the better-crawler dsh bundle into a DeepSeek Harness profile and
# publish its skill.
#
#   ./install.sh [profile]        # profile defaults to "web"
#
# A stdio MCP row has to name an absolute launcher path, so this script stages
# a copy of dsh-plugin/ under $DSH_HOME/plugins/ with the real paths written in,
# then registers that copy as a profile bundle:
#
#   1. stage  -> $DSH_HOME/plugins/better-crawler-4-agent-dsh
#   2. bundle -> dsh plugin --profile <profile> add file:<stage>
#   3. skill  -> $DSH_HOME/skills/web-to-text, which every profile discovers
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/.." && pwd)
PROFILE=${1:-web}
DSH_HOME=${DSH_HOME:-$HOME/.dsh}
STAGE=$DSH_HOME/plugins/better-crawler-4-agent-dsh

command -v dsh >/dev/null 2>&1 || { echo "install.sh: dsh is not on PATH" >&2; exit 1; }
[ -f "$REPO/scripts/launch.py" ] || { echo "install.sh: expected the crawler repo at $REPO" >&2; exit 1; }

echo "==> staging $HERE -> $STAGE"
rm -rf "$STAGE"
mkdir -p "$(dirname -- "$STAGE")"
cp -R "$HERE" "$STAGE"
sed -i "s|__PLUGIN_DIR__|$STAGE|g; s|__REPO_DIR__|$REPO|g" \
	"$STAGE/cordis.patch.yml" "$STAGE/scripts/launch.sh"
chmod +x "$STAGE/scripts/launch.sh" "$STAGE/install.sh"

echo "==> bundle '$STAGE' -> profile '$PROFILE'"
dsh plugin --profile "$PROFILE" add "file:$STAGE"

echo "==> skill web-to-text -> $DSH_HOME/skills/web-to-text"
mkdir -p "$DSH_HOME/skills"
if [ -L "$DSH_HOME/skills/web-to-text" ] || [ ! -e "$DSH_HOME/skills/web-to-text" ]; then
	ln -sfn "$REPO/skills/web-to-text" "$DSH_HOME/skills/web-to-text"
else
	echo "install.sh: kept existing $DSH_HOME/skills/web-to-text (not a symlink)" >&2
fi

echo "done. restart dsh if the profile is already running."
