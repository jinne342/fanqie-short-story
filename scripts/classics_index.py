#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名著库：种子检索、原文索引、反抄袭相似度。

三个层次：
  seed        100 本技法种子卡（随 skill 发布，无需原文）
  build       把本地 .txt/.md/.docx 建成本地 SQLite 全文索引（可到 1 万本）
  query       从索引里取 3–5 段最相关的原文
  similarity  检查生成稿与索引/原文的最长重合片段

用法：
  python classics_index.py seed --query "冷眼 婚姻" --limit 5
  python classics_index.py build --source D:\\books --index D:\\books\\classics.sqlite
  python classics_index.py query --index D:\\books\\classics.sqlite --query "雨 葬礼" --limit 5
  python classics_index.py similarity --index D:\\books\\classics.sqlite --file 正文.txt
  python classics_index.py similarity --against 原文.txt --file 正文.txt

设计原则：
- 不把 1 万本全文塞进上下文；只取 top-k 段落。
- 不抄句子；相似度 >= 12 字重合必须改。
- 索引只存本地，不随 skill 发布；种子卡只含技法摘要，不含长引文。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_SEED = Path(__file__).resolve().parent.parent / "assets" / "classics-seed.json"
TEXT_EXTS = {".txt", ".md", ".markdown"}
MAX_CHARS = 650
OVERLAP = 80


def out(text: str = "") -> None:
    print(text)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_docx(path: Path) -> str:
    try:
        from docx import Document
    except Exception:
        return ""
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def visible_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


# ---------------- seed ----------------

def load_seed(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"seed not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def seed_query(args: argparse.Namespace) -> int:
    items = load_seed(Path(args.seed))
    terms = [t for t in re.split(r"[\s,，、;；]+", args.query.strip()) if t]
    if not terms:
        raise SystemExit("empty query")

    scored = []
    for item in items:
        blob = " ".join(str(item.get(k, "")) for k in ("title", "author", "category", "category_full", "move"))
        score = 0
        for term in terms:
            if term in item.get("title", ""):
                score += 8
            if term in item.get("author", ""):
                score += 6
            if term in item.get("category", "") or term in item.get("category_full", ""):
                score += 4
            if term in item.get("move", ""):
                score += 3
            if term in blob:
                score += 1
        if score:
            scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    picked = scored[: args.limit]

    if args.json:
        out(json.dumps([{"score": s, **it} for s, it in picked], ensure_ascii=False, indent=2))
    else:
        if not picked:
            out("没有命中。换更宽的关键词，如“冷眼”“葬礼”“短句”“物件”。")
            return 0
        for i, (score, it) in enumerate(picked, 1):
            out(f"{i}. 《{it['title']}》{it.get('author', '')} [{it.get('category', '')}]")
            out(f"   可偷：{it.get('move', '')}")
    return 0


# ---------------- build ----------------

def iter_files(source: Path, exts: set[str]):
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in exts:
            yield path


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines()[:20]:
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        m = re.match(r"^《(.+?)》", s)
        if m:
            return m.group(1)
        if s and len(s) <= 30 and not s.startswith(("作者", "简介", "序")):
            return s
    return path.stem


def split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in text.splitlines() if p.strip()]
    return parts


def chunk_text(text: str) -> list[str]:
    parts = split_paragraphs(text)
    chunks: list[str] = []
    buf = ""
    for p in parts:
        if not buf:
            buf = p
        elif visible_len(buf) + visible_len(p) <= MAX_CHARS:
            buf += "\n" + p
        else:
            chunks.append(buf)
            tail = buf[-OVERLAP:] if len(buf) > OVERLAP else buf
            buf = tail + "\n" + p
    if buf.strip():
        chunks.append(buf)
    return [c for c in chunks if visible_len(c) >= 40]


def build(args: argparse.Namespace) -> int:
    source = Path(args.source)
    if not source.is_dir():
        raise SystemExit(f"source not found: {source}")
    index = Path(args.index)
    index.parent.mkdir(parents=True, exist_ok=True)
    if index.exists() and not args.force:
        raise SystemExit(f"index exists: {index} (use --force)")

    meta = {}
    if args.meta:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))

    exts = set(args.ext.split(",")) if args.ext else TEXT_EXTS | {".docx"}
    exts = {e if e.startswith(".") else "." + e for e in exts}

    if index.exists():
        index.unlink()
    conn = sqlite3.connect(index)
    conn.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, path TEXT, tags TEXT);
        CREATE TABLE passages (id INTEGER PRIMARY KEY, book_id INTEGER, seq INTEGER, text TEXT);
        CREATE VIRTUAL TABLE passages_fts USING fts5(text, title, author, tokenize='trigram');
        """
    )

    book_id = 0
    passage_id = 0
    files = list(iter_files(source, exts))
    for n, path in enumerate(files, 1):
        if path.suffix.lower() == ".docx":
            text = read_docx(path)
        else:
            text = read_text(path)
        if visible_len(text) < 40:
            continue

        rel = str(path.relative_to(source))
        info = meta.get(rel) or meta.get(path.name) or {}
        title = info.get("title") or extract_title(text, path)
        author = info.get("author") or ""
        tags = ",".join(info.get("tags", []))

        chunks = chunk_text(text)
        if not chunks:
            continue
        book_id += 1
        conn.execute("INSERT INTO books(id,title,author,path,tags) VALUES(?,?,?,?,?)",
                     (book_id, title, author, rel, tags))
        for seq, chunk in enumerate(chunks, 1):
            passage_id += 1
            conn.execute("INSERT INTO passages(id,book_id,seq,text) VALUES(?,?,?,?)",
                         (passage_id, book_id, seq, chunk))
            conn.execute("INSERT INTO passages_fts(rowid,text,title,author) VALUES(?,?,?,?)",
                         (passage_id, chunk, title, author))

        if n % 100 == 0:
            out(f"  scanned {n}/{len(files)} files, {passage_id} passages")

    conn.commit()
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta(key,value) VALUES('version','1')")
    conn.execute("INSERT INTO meta(key,value) VALUES('files',?)", (str(book_id),))
    conn.execute("INSERT INTO meta(key,value) VALUES('passages',?)", (str(passage_id),))
    conn.commit()
    conn.close()

    out(f"index: {index}")
    out(f"books: {book_id}  passages: {passage_id}")
    return 0


# ---------------- query ----------------

def connect(index: Path) -> sqlite3.Connection:
    if not index.exists():
        raise SystemExit(f"index not found: {index}")
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    return conn


def fts_query(terms: list[str]) -> str:
    clean = [t.replace('"', " ").strip() for t in terms if t.strip()]
    return " OR ".join(f'"{t}"' for t in clean)


def like_query(terms: list[str]) -> tuple[str, list[str]]:
    where = " OR ".join("p.text LIKE ?" for _ in terms)
    return where, [f"%{t}%" for t in terms]


def query_rows(conn: sqlite3.Connection, query: str, limit: int):
    terms = [t for t in re.split(r"[\s,，、;；]+", query.strip()) if t]
    if not terms:
        return []
    if any(len(t) >= 3 for t in terms):
        try:
            sql = """
                SELECT p.text, b.title, b.author, b.path, bm25(passages_fts) AS score
                FROM passages_fts f
                JOIN passages p ON p.id = f.rowid
                JOIN books b ON b.id = p.book_id
                WHERE passages_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """
            return conn.execute(sql, (fts_query(terms), limit)).fetchall()
        except sqlite3.OperationalError:
            pass
    where, params = like_query(terms)
    sql = f"""
        SELECT p.text, b.title, b.author, b.path, 0 AS score
        FROM passages p JOIN books b ON b.id = p.book_id
        WHERE {where}
        LIMIT ?
    """
    return conn.execute(sql, (*params, limit)).fetchall()


def snippet(text: str, terms: list[str], width: int = 70) -> str:
    pos = min((text.find(t) for t in terms if text.find(t) >= 0), default=0)
    start = max(0, pos - width // 2)
    end = min(len(text), pos + width)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + re.sub(r"\s+", " ", text[start:end]).strip() + suffix


def query(args: argparse.Namespace) -> int:
    conn = connect(Path(args.index))
    terms = [t for t in re.split(r"[\s,，、;；]+", args.query.strip()) if t]
    rows = query_rows(conn, args.query, args.limit)
    if args.json:
        out(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
    else:
        if not rows:
            out("没有命中。换更宽的关键词，或先 build 索引。")
            return 0
        for i, r in enumerate(rows, 1):
            out(f"{i}. 《{r['title']}》{r['author']}  [{r['path']}]")
            out(f"   {snippet(r['text'], terms)}")
    conn.close()
    return 0


# ---------------- similarity ----------------

def max_common(a: str, b: str, n: int = 8) -> tuple[int, str]:
    if len(a) < n or len(b) < n:
        return 0, ""
    grams = {a[i:i + n]: i for i in range(len(a) - n + 1)}
    best_len = 0
    best = ""
    i = 0
    while i <= len(b) - n:
        g = b[i:i + n]
        if g in grams:
            j = grams[g]
            left = 0
            while j - left - 1 >= 0 and i - left - 1 >= 0 and a[j - left - 1] == b[i - left - 1]:
                left += 1
            right = n
            while j + right < len(a) and i + right < len(b) and a[j + right] == b[i + right]:
                right += 1
            length = left + right
            if length > best_len:
                best_len = length
                best = b[i - left:i + right]
            i += max(1, right)
        else:
            i += 1
    return best_len, best


def sample_queries(text: str, limit: int = 4) -> str:
    parts = [p.strip() for p in re.split(r"[。！？!?…\n]+", text)]
    picked = [p for p in parts if 8 <= visible_len(p) <= 40][:limit]
    return " ".join(picked) if picked else text[:24]


def similarity(args: argparse.Namespace) -> int:
    gen = read_text(Path(args.file))
    gen = re.sub(r"\s+", "", gen)
    candidates: list[tuple[str, str]] = []

    if args.against:
        src = read_text(Path(args.against))
        for para in split_paragraphs(src):
            if visible_len(para) >= 40:
                candidates.append(("原文", para))
    else:
        conn = connect(Path(args.index))
        rows = query_rows(conn, sample_queries(gen), args.limit)
        for r in rows:
            candidates.append((f"《{r['title']}》{r['author']}", r["text"]))
        conn.close()

    best_len = 0
    best_src = ""
    best_text = ""
    for label, text in candidates:
        clean = re.sub(r"\s+", "", text)
        length, chunk = max_common(gen, clean)
        if length > best_len:
            best_len, best_src, best_text = length, label, chunk

    flagged = best_len >= args.threshold
    if args.json:
        out(json.dumps({
            "max_overlap": best_len,
            "threshold": args.threshold,
            "flagged": flagged,
            "source": best_src,
            "overlap": best_text,
        }, ensure_ascii=False, indent=2))
    else:
        out(f"最长重合：{best_len} 字（阈值 {args.threshold}）")
        if best_src:
            out(f"来源：{best_src}")
            out(f"重合片段：{best_text[:120]}")
        if flagged:
            out("需要改写：换人物、地点、动作顺序和句子结构；不要只替换同义词。")
        else:
            out("未发现长段重合。仍要检查专名、情节链和意象是否照搬。")

    return 1 if (args.strict and flagged) else 0


# ---------------- stats ----------------

def stats(args: argparse.Namespace) -> int:
    conn = connect(Path(args.index))
    books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    passages = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    out(f"books: {books}")
    out(f"passages: {passages}")
    if args.top:
        rows = conn.execute("""
            SELECT b.title, b.author, COUNT(*) AS n
            FROM passages p JOIN books b ON b.id=p.book_id
            GROUP BY b.id ORDER BY n DESC LIMIT ?
        """, (args.top,)).fetchall()
        for r in rows:
            out(f"  《{r['title']}》{r['author']}  {r['n']} 段")
    conn.close()
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="名著库：种子检索 / 原文索引 / 反抄袭相似度")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seed", help="查询内置 100 本技法种子卡")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--seed", default=str(DEFAULT_SEED))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=seed_query)

    p = sub.add_parser("build", help="把本地书库建成 SQLite 索引")
    p.add_argument("--source", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--meta", default="")
    p.add_argument("--ext", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=build)

    p = sub.add_parser("query", help="从索引检索原文段落")
    p.add_argument("--index", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=query)

    p = sub.add_parser("similarity", help="检查生成稿与原文的最长重合")
    p.add_argument("--file", required=True)
    p.add_argument("--index", default="")
    p.add_argument("--against", default="")
    p.add_argument("--threshold", type=int, default=12)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=similarity)

    p = sub.add_parser("stats", help="索引统计")
    p.add_argument("--index", required=True)
    p.add_argument("--top", type=int, default=0)
    p.set_defaults(func=stats)

    args = ap.parse_args()
    if args.cmd == "similarity" and not args.index and not args.against:
        raise SystemExit("similarity 需要 --index 或 --against")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
