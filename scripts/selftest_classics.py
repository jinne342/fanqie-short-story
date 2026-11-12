# -*- coding: utf-8 -*-
"""Self-test for the classics seed/index/similarity pipeline.

Run from the repository root:
    python scripts/selftest_classics.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "classics_index.py"
PY = sys.executable


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main() -> int:
    ok = True

    # 1. seed query
    r = run("seed", "--query", "婚姻 冷眼", "--limit", "5", "--json")
    if r.returncode != 0:
        print("FAIL seed: nonzero exit")
        print(r.stderr)
        return 1
    items = json.loads(r.stdout)
    titles = " ".join(i["title"] for i in items)
    print("seed:", titles)
    if "倾城之恋" not in titles and "孔乙己" not in titles:
        print("FAIL seed: expected 倾城之恋 or 孔乙己")
        ok = False

    # 2. build + query + similarity
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.txt").write_text(
            "雨落在青石板上，声音很轻。她把伞收起来，站在屋檐下，看见对面的灯一盏一盏灭掉。"
            "巷子深处有人推门，门轴响了一声，又停了。她数到第三声，才往里走。",
            encoding="utf-8",
        )
        (d / "b.txt").write_text(
            "她把碗放下，碗底和桌面碰了一下。汤已经凉了，上面结了一层薄薄的油。"
            "她没有动筷子，只是看着窗外。楼下有人在喊孩子回家吃饭，声音拖得很长。",
            encoding="utf-8",
        )
        (d / "c.txt").write_text(
            "老人出海第八十五天，船上只剩一条鱼。海面很平，太阳落下去的时候，天和海是一个颜色。"
            "他把手放在船舷上，手背上全是旧伤。他没有说话。",
            encoding="utf-8",
        )
        index = d / "classics.sqlite"
        r = run("build", "--source", str(d), "--index", str(index), "--force")
        print(r.stdout.strip())
        if r.returncode != 0:
            print("FAIL build")
            print(r.stderr)
            return 1

        r = run("query", "--index", str(index), "--query", "碗 凉了", "--limit", "3")
        print(r.stdout.strip())
        if r.returncode != 0 or "碗" not in r.stdout:
            print("FAIL query")
            ok = False

        copy = d / "gen_copy.txt"
        copy.write_text(
            "她把碗放下，碗底和桌面碰了一下。汤已经凉了，上面结了一层薄薄的油。"
            "她起身去关窗，窗外什么都没有。",
            encoding="utf-8",
        )
        r = run("similarity", "--index", str(index), "--file", str(copy), "--strict")
        print(r.stdout.strip())
        if r.returncode != 1:
            print("FAIL similarity: copied text should be flagged")
            ok = False

        orig = d / "gen_ok.txt"
        orig.write_text(
            "她把搪瓷盆搁在灶台上，盆底磕掉了一小块瓷。面汤早就坨了，表面浮着一层凝固的油花。"
            "她没拿筷子，先去看窗子有没有关严。",
            encoding="utf-8",
        )
        r = run("similarity", "--index", str(index), "--file", str(orig))
        print(r.stdout.strip())
        if r.returncode != 0:
            print("FAIL similarity: original text should pass")
            ok = False

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
