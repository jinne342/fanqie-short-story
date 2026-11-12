#!/usr/bin/env bash
set -euo pipefail

DEST="${1:-$HOME/.dsh/skills}"
TARGET="$DEST/fanqie-short-story"
mkdir -p "$TARGET"

cp -R SKILL.md prompt.md README.md LICENSE assets references scripts examples "$TARGET/"

echo "Installed to: $TARGET"
echo "Start a new session, then say: 用番茄短篇 skill 写一篇世情反转，1.2 万字。"
