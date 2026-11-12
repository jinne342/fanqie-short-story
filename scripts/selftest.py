# -*- coding: utf-8 -*-
"""Self-test: the AI draft must score lower than the human draft.

Run from the repository root:
    python scripts/selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI = ROOT / "examples" / "case-01" / "ai-draft.txt"
HUMAN = ROOT / "examples" / "case-01" / "human-draft.txt"


def score(path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_ai.py"), str(path), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode not in (0, 1):
        raise SystemExit(f"checker failed on {path}:\n{result.stderr}")
    return json.loads(result.stdout)["files"][0]


def main() -> int:
    ai = score(AI)
    human = score(HUMAN)
    print(f"AI   : {ai['score']}/100 {ai['grade']}  blocking={sum(1 for f in ai['findings'] if f['severity'] == 'blocking')}")
    print(f"HUMAN: {human['score']}/100 {human['grade']}  blocking={sum(1 for f in human['findings'] if f['severity'] == 'blocking')}")

    ok = True
    if ai["score"] >= human["score"]:
        print("FAIL: AI draft should score lower than human draft")
        ok = False
    if human["grade"] != "A":
        print("FAIL: human draft should be grade A")
        ok = False
    if any(f["severity"] == "blocking" for f in human["findings"]):
        print("FAIL: human draft should have no blocking findings")
        ok = False

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
