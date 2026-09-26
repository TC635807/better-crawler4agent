#!/bin/sh
# dsh stdio launcher for the better-crawler MCP server.
#
# The MCP client starts this file without a shell, so this script owns the two
# things a bare `python scripts/launch.py` cannot do reliably:
#
#   1. pick an interpreter that actually has the crawler's dependencies
#      (playwright, mcp>=2, trafilatura, beautifulsoup4, lxml, httpx)
#   2. hand a Windows interpreter Windows-visible paths when it is reached
#      through WSL interop — a Linux path such as /mnt/d/... is meaningless to
#      python.exe, whose own re-exec logic assumes one shared path namespace.
#
# stdout carries MCP frames only. Every diagnostic goes to stderr.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Repository root, in order of precedence:
#   1. an explicit BETTER_CRAWLER_REPO
#   2. the layout this file ships in (<repo>/dsh-plugin/scripts/launch.sh)
#   3. the absolute path install.sh bakes into the staged copy it registers.
#      The token on that assignment is the only placeholder in this file, so
#      replacement is unambiguous; an unsubstituted run falls through to the
#      existence check below and fails with a readable message.
DERIVED=$(CDPATH= cd -- "$HERE/../.." 2>/dev/null && pwd || printf '')
if [ -n "${BETTER_CRAWLER_REPO:-}" ]; then
	REPO=$BETTER_CRAWLER_REPO
elif [ -n "$DERIVED" ] && [ -f "$DERIVED/scripts/launch.py" ]; then
	REPO=$DERIVED
else
	REPO=__REPO_DIR__
fi

LAUNCH=$REPO/scripts/launch.py
if [ ! -f "$LAUNCH" ]; then
	echo "better-crawler: no MCP launcher at $LAUNCH" >&2
	echo "better-crawler: run dsh-plugin/install.sh, or set BETTER_CRAWLER_REPO to the repo root" >&2
	exit 1
fi

# Interpreter selection. An explicit BETTER_CRAWLER_PYTHON always wins;
# otherwise prefer the repository's own virtualenv, then whatever python is on
# PATH (launch.py re-execs into a dependency-bearing interpreter when it can).
PY=${BETTER_CRAWLER_PYTHON:-}
if [ -z "$PY" ]; then
	for candidate in "$REPO/.venv/bin/python" "$REPO/.venv/Scripts/python.exe"; do
		if [ -f "$candidate" ]; then
			PY=$candidate
			break
		fi
	done
fi
if [ -z "$PY" ]; then
	PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
if [ -z "$PY" ]; then
	echo "better-crawler: no Python interpreter found" >&2
	echo "better-crawler: set BETTER_CRAWLER_PYTHON to an absolute interpreter path" >&2
	exit 1
fi

# A Windows interpreter reached through WSL interop only understands Windows
# paths; wslpath is the bridge.
case $PY in
*.exe | *.EXE)
	if command -v wslpath >/dev/null 2>&1; then
		case $LAUNCH in
		/mnt/*) LAUNCH=$(wslpath -w -- "$LAUNCH") ;;
		esac
	fi
	;;
esac

# exec keeps this script out of the process tree: the MCP client talks to the
# server over the inherited stdio pipes with no intermediate shell.
exec "$PY" "$LAUNCH" "$@"
