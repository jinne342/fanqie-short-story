#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""番茄短篇 · 模板腔扫描器（不是 AIGC 检测器）

用法：
    python check_ai.py 正文.txt
    python check_ai.py 正文.txt --min-words 6000 --strict
    python check_ai.py 正文.txt --json
    python check_ai.py 正文.txt --sections

设计原则：
- 只报“形”，不判“罪”。命中的句子可能承担叙事功能，必须人工复核。
- 分数用于迭代，不用于承诺过稿；平台审核看的是整体质量。
- 白名单：同目录 .deslop-whitelist，一行一个字面片段，支持 # 注释。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

# ---------- 词表 ----------

SUMMARY_WORDS = [
    "总而言之", "综上所述", "总的来说", "不言而喻", "毋庸置疑", "毫无疑问",
    "归根结底", "说到底", "本质上", "值得注意的是", "需要指出的是",
    "一言以蔽之", "简而言之", "众所周知",
]

EMOTION_PATTERNS = [
    r"感到[^。！？!?\n]{0,6}(?:愤怒|悲伤|震惊|绝望|委屈|害怕|难过|痛苦|欣慰|失落|羞愧|恐惧|疲惫|心累|无力|酸楚|苦涩)",
    r"心中(?:涌起|升起|泛起|一[暖凉冷酸])",
    r"内心充满",
    r"百感交集|五味杂陈|心如刀割|撕心裂肺|心如死灰",
    r"泪水(?:不受控制|夺眶而出|模糊了双眼)",
    r"一种[^。！？!?\n]{0,8}的感觉(?:油然而生|涌上心头)",
]

ACTION_CLICHES = [
    "缓缓开口", "缓缓说道", "淡淡地说", "淡淡道", "沉声道", "低声道",
    "微微一笑", "淡淡一笑", "嘴角微微上扬", "勾起一抹", "嘴角勾起",
    "眉头微皱", "眉头紧锁", "深吸一口气", "目光如炬", "目光深邃",
    "眼底闪过", "眸光", "指节泛白", "身形一滞", "脚步一顿",
    "不由自主地", "情不自禁地", "不易察觉", "若有若无",
]

WEAK_ADVERBS = [
    "微微", "淡淡", "缓缓", "轻轻", "悄然", "默默", "隐隐",
    "些许", "一丝", "一抹", "几分", "隐约", "不由得", "不禁", "忍不住",
]

METAPHOR_MARKERS = ["仿佛", "宛如", "宛若", "如同", "犹如", "好像", "像是", "像"]

ABSTRACT_NOUNS = [
    "命运", "宿命", "灵魂", "岁月", "时光", "人生", "尊严", "救赎",
    "羁绊", "执念", "众生", "尘埃", "深渊", "光芒", "温暖", "力量",
]

IDIOMS = [
    "心潮澎湃", "热血沸腾", "激动万分", "恍然大悟", "如梦初醒", "茅塞顿开",
    "不由分说", "二话不说", "当机立断", "五味杂陈", "百感交集", "心如刀割",
    "撕心裂肺", "刻骨铭心", "痛不欲生", "泪流满面", "目瞪口呆", "瞠目结舌",
    "难以置信", "不可思议", "不知所措", "无能为力", "无可奈何", "迫不及待",
    "毫不犹豫", "义无反顾", "如释重负", "心安理得", "理所当然", "天经地义",
]

TRAILER_PATTERNS = [
    r"这一切(?:才)?(?:刚刚)?开始", r"才刚刚开始", r"命运的齿轮", r"新的开始",
    r"新的人生", r"新的篇章", r"终于明白", r"没人知道", r"谁也不知道",
    r"谁也没想到", r"殊不知", r"即将(?:开始|来临|降临)", r"拉开(?:序幕|帷幕)",
]

META_PATTERNS = [
    r"第[一二三四五六七八九十百千万两0-9]+章", r"上一章", r"下一章",
    r"前一章", r"本章", r"这一章", r"前文", r"后文", r"伏笔", r"细纲", r"读者",
]

DIALOG_TAGS = [
    "说道", "问道", "答道", "笑道", "冷笑道", "骂道", "喊道", "叫道",
    "回答道", "解释道", "补充道", "低声道", "沉声道", "淡淡地说",
]

PUNCT_RUN = re.compile(r"[！!]{2,}|[？?]{2,}|[。]{4,}")
EXPLAIN_CHAIN = re.compile(
    r"(?:我知道|我明白|我意识到|她知道|她知道|他明白|他意识到|这意味着|也就是说|换句话说|真正的问题在于|关键在于)"
)
NOT_IS_STRONG = re.compile(r"不是[^。！？!?\n]{1,28}而是")
NOT_IS_WEAK = re.compile(r"不是[^。！？!?\n]{1,28}[，,]\s*是(?!不)")
NEGATION_PARADE = re.compile(r"(?:没有[^，。！？!?\n]{1,12}[，,]){2,}")
IDIOM_PAIR = re.compile(r"([\u4e00-\u9fff]{4})、([\u4e00-\u9fff]{4})")
DIGIT = re.compile(r"\d")

QUOTE_PAIRS = [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')]


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:])
    return text


def load_whitelist(path: Path) -> list[str]:
    wl = path.parent / ".deslop-whitelist"
    if not wl.exists():
        return []
    out = []
    for line in read_text(wl).splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def mask_whitelist(text: str, whitelist: list[str]) -> str:
    for item in whitelist:
        if item:
            text = text.replace(item, " " * len(item))
    return text


def mask_quotes(line: str) -> str:
    """把成对引号里的内容替换为等长空格，只留叙述层。"""
    out = line
    for open_q, close_q in QUOTE_PAIRS:
        if open_q == close_q:
            pattern = re.escape(open_q) + r"[^" + re.escape(open_q) + r"\n]*" + re.escape(close_q)
        else:
            pattern = re.escape(open_q) + r"[^" + re.escape(close_q) + r"\n]*" + re.escape(close_q)
        out = re.sub(pattern, lambda m: " " * len(m.group(0)), out)
    return out


def visible_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？!?…]+", text) if s.strip()]


def compact(text: str, n: int = 42) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def density(count: int, chars: int) -> float:
    return count / chars * 1000 if chars else 0.0


class Report:
    def __init__(self, path: Path, text: str, min_words: int):
        self.path = path
        self.min_words = min_words
        self.text = text
        self.findings: list[dict] = []
        self.lines = text.splitlines()
        self.paras: list[tuple[int, str]] = []       # (line_no, raw paragraph)
        self.narr_paras: list[tuple[int, str]] = []  # 引号外叙述
        self.dialog_lines = 0
        self.chars = 0
        self.narr_chars = 0
        self.deduction = 0
        self._collect()

    def _collect(self) -> None:
        for i, line in enumerate(self.lines, 1):
            s = line.strip()
            if not s:
                continue
            if s.startswith("#") or s.startswith("```") or s in ("---", "***"):
                continue
            if re.match(r"^(?:[-*+]\s|\d+[.)]\s|\|)", s):
                continue
            self.paras.append((i, s))
            self.chars += visible_len(s)
            self.narr_chars += visible_len(mask_quotes(s))
            if s[0] in "“「『\"":
                self.dialog_lines += 1

        for i, p in self.paras:
            n = mask_quotes(p).strip()
            if n:
                self.narr_paras.append((i, n))

    def add(self, rid: str, label: str, severity: str, count: int,
            message: str, samples: list[str], deduct: int,
            locations: list[int] | None = None) -> None:
        if count <= 0:
            return
        deduct = max(0, int(deduct))
        self.findings.append({
            "id": rid,
            "label": label,
            "severity": severity,
            "count": count,
            "message": message,
            "samples": samples[:5],
            "locations": (locations or [])[:8],
            "deduction": deduct,
        })
        self.deduction += deduct

    # ---------- 规则 ----------

    def run(self) -> None:
        if self.chars < 100:
            return
        narration_text = "\n".join(n for _, n in self.narr_paras)
        chars = max(self.narr_chars, 1)

        # 1. 不是A而是B
        strong = [m for m in NOT_IS_STRONG.finditer(narration_text) if "  " not in m.group(0)]
        self.add("not-is", "不是A而是B", "blocking", len(strong),
                 "删掉否定铺垫，直接写后项；或换成两个具体证据。",
                 [m.group(0) for m in strong], min(12 * len(strong), 36))

        # 1b. 不是A，是B（口语里常见，密度高才提示）
        weak = [m for m in NOT_IS_WEAK.finditer(narration_text) if "  " not in m.group(0)]
        wd_not = density(len(weak), chars)
        if len(weak) >= 3 and wd_not > 3:
            self.add("not-is-weak", "不是A，是B 偏多", "advisory", len(weak),
                     f"{len(weak)} 处（{wd_not:.1f}/千字）；能删否定铺垫就删，保留真正需要纠正的那一处。",
                     [m.group(0) for m in weak], min(3 * len(weak), 12))

        # 2. 结尾升华/预告
        tail = narration_text[-600:]
        tail_hits = []
        for pat in TRAILER_PATTERNS:
            tail_hits += re.findall(pat, tail)
        tail_hits = list(dict.fromkeys(tail_hits))
        self.add("trailer", "结尾升华/预告", "blocking", len(tail_hits),
                 "结尾停在一个动作/画面上，不要替读者总结人生或预告下一章。",
                 tail_hits, 15 if tail_hits else 0)

        # 3. 工程词/元信息
        meta_hits = []
        for pat in META_PATTERNS:
            meta_hits += re.findall(pat, narration_text)
        self.add("meta", "正文混入工程词", "blocking", len(meta_hits),
                 "章节号、伏笔、读者等词只放标题/大纲，不进正文。",
                 meta_hits, min(8 * len(meta_hits), 24))

        # 4. 标点连用
        punct_hits = PUNCT_RUN.findall(narration_text)
        self.add("punct-run", "标点连用", "blocking", len(punct_hits),
                 "感叹号/问号/句号连用是模板腔；保留一个，或改成动作。",
                 punct_hits, min(5 * len(punct_hits), 15))

        # 5. 总结词
        summary_hits = [w for w in SUMMARY_WORDS if w in narration_text]
        summary_count = sum(narration_text.count(w) for w in summary_hits)
        self.add("summary", "总结概括词", "blocking", summary_count,
                 "删掉总结词，直接说结论；或把结论变成动作。",
                 summary_hits, min(8 * summary_count, 24))

        # 6. Markdown 混入
        body_text = "\n".join(p for _, p in self.paras)
        md_hits = re.findall(r"\*\*|^#{1,6}\s|^---+$", body_text, re.M)
        self.add("markdown", "正文混入Markdown", "blocking", len(md_hits),
                 "正文不是 Markdown 文档；去掉 **、#、---。", md_hits,
                 min(8 * len(md_hits), 16))

        # 7. 解释链
        explain = EXPLAIN_CHAIN.findall(narration_text)
        if len(explain) >= 2:
            self.add("explain", "解释链", "advisory", len(explain),
                     "删掉“我知道/我明白/这意味着”，把判断落到证据、动作或对话上。",
                     explain, min(3 * len(explain), 12))

        # 8. 情绪直述
        emo = []
        for pat in EMOTION_PATTERNS:
            emo += re.findall(pat, narration_text)
        self.add("emotion", "情绪直述", "advisory", len(emo),
                 "情绪改由动作/物件/生理反应承担；同一情绪不重复解释。",
                 emo, min(4 * len(emo), 16))

        # 8. 套式动作
        act = [w for w in ACTION_CLICHES if w in narration_text]
        act_count = sum(narration_text.count(w) for w in act)
        self.add("action-cliche", "套式动作", "advisory", act_count,
                 "写清楚“怎么笑/看哪里/手在干什么”，不要用通用动作标签。",
                 act, min(3 * act_count, 15))

        # 9. 弱化副词密度
        weak = [w for w in WEAK_ADVERBS if w in narration_text]
        weak_count = sum(narration_text.count(w) for w in weak)
        wd = density(weak_count, chars)
        if wd > 6:
            self.add("weak-adverb", "弱化副词偏多", "advisory", weak_count,
                     f"{weak_count} 处（{wd:.1f}/千字，阈值 6）；删掉大部分“微微/淡淡/缓缓”。",
                     weak, min(4 + int(wd - 6), 14))

        # 10. 比喻密度
        met = sum(narration_text.count(w) for w in METAPHOR_MARKERS)
        md = density(met, chars)
        if md > 8:
            self.add("metaphor", "比喻密度偏高", "advisory", met,
                     f"{met} 处（{md:.1f}/千字，阈值 8）；删比喻写本体，一段最多 1 个。",
                     [], min(4 + int(md - 8), 14))

        # 11. 四字成语
        idiom_hits = [w for w in IDIOMS if w in narration_text]
        idiom_count = sum(narration_text.count(w) for w in idiom_hits)
        pairs = IDIOM_PAIR.findall(narration_text)
        idiom_pairs = [f"{a}、{b}" for a, b in pairs if a in IDIOMS and b in IDIOMS]
        if idiom_count > 8 or idiom_pairs:
            self.add("idiom", "四字成语堆叠", "advisory", idiom_count + len(idiom_pairs),
                     "连续两个四字词必须拆；成语密度高会像公文。",
                     idiom_hits + idiom_pairs, min(3 * (idiom_count + len(idiom_pairs)), 12))

        # 12. 抽象大词
        abstract = [w for w in ABSTRACT_NOUNS if w in narration_text]
        ab_count = sum(narration_text.count(w) for w in abstract)
        abd = density(ab_count, chars)
        if abd > 6:
            self.add("abstract", "抽象大词偏多", "advisory", ab_count,
                     f"{ab_count} 处（{abd:.1f}/千字）；不要把“命运/人生/灵魂”当解释。",
                     abstract, min(4 + int(abd - 6), 12))

        # 13. 否定排比
        neg = NEGATION_PARADE.findall(narration_text)
        self.add("negation", "否定排比", "advisory", len(neg),
                 "把“没有X，没有Y”换成“实际发生了什么/有什么”。",
                 neg, min(4 * len(neg), 12))

        # 14. 段落均匀
        plens = [visible_len(p) for _, p in self.paras if visible_len(p) > 0]
        if len(plens) >= 15:
            mean = statistics.mean(plens)
            cv = statistics.pstdev(plens) / mean if mean else 0
            if cv < 0.38:
                self.add("uniform-para", "段落过于均匀", "advisory", len(plens),
                         f"段长变异系数 {cv:.2f}（阈值 0.38）；打散连续同长度的段落。",
                         [], 8)

        # 15. 长段
        long_paras = [(i, p) for i, p in self.paras if visible_len(p) > 150]
        self.add("long-para", "长段落", "advisory", len(long_paras),
                 "手机阅读单段建议 ≤150 字；按镜头/动作/线索断段。",
                 [compact(p) for _, p in long_paras], min(3 * len(long_paras), 9),
                 [i for i, _ in long_paras])

        # 16. 碎句号
        run = 0
        max_run = 0
        for _, n in self.narr_paras:
            for s in split_sentences(n):
                if visible_len(s) <= 5:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0
        if max_run >= 6:
            self.add("short-run", "连续短句", "advisory", max_run,
                     f"连续 {max_run} 个 ≤5 字短句；按句群合并，补画面和连接。",
                     [], 7)

        # 17. 句长均匀
        slens = [visible_len(s) for _, n in self.narr_paras for s in split_sentences(n)]
        slens = [x for x in slens if x > 0]
        if len(slens) >= 30:
            mean = statistics.mean(slens)
            cv = statistics.pstdev(slens) / mean if mean else 0
            if cv < 0.35:
                self.add("uniform-sentence", "句长过于均匀", "advisory", len(slens),
                         f"句长变异系数 {cv:.2f}（阈值 0.35）；长短交错，长—短—短—长。",
                         [], 6)

        # 18. 对话标签
        if self.dialog_lines >= 8:
            tag_count = sum(narration_text.count(t) for t in DIALOG_TAGS)
            ratio = tag_count / self.dialog_lines
            if ratio > 0.6:
                self.add("dialog-tag", "对话标签过多", "advisory", tag_count,
                         f"标签/对话行 = {ratio:.2f}（阈值 0.6）；用动作替代，连续对话省略标签。",
                         [], 6)

        # 19. 具体锚点
        digits = len(DIGIT.findall(narration_text))
        dd = density(digits, chars)
        if dd < 3:
            self.add("concrete", "具体锚点偏少", "advisory", digits,
                     f"数字/金额/时间仅 {digits} 处（{dd:.1f}/千字）；每千字至少 3 个具体锚点。",
                     [], 6)

        # 20. 感叹号
        ex = narration_text.count("！") + narration_text.count("!")
        exd = density(ex, chars)
        if exd > 10:
            self.add("exclaim", "感叹号偏多", "advisory", ex,
                     f"{ex} 个（{exd:.1f}/千字）；爆发点才用，连续喊叫会廉价。", [], 4)

        # 21. 破折号
        dash = narration_text.count("——")
        if dash > 3:
            self.add("dash", "破折号偏多", "advisory", dash,
                     f"{dash} 处；能不用就不用，打断用动作，拖长用省略或句号。", [], 5)

        # 22. 段首重复
        run = 1
        max_opener = 1
        prev = None
        for _, p in self.paras:
            first = p[0] if p else ""
            if first in "他她我" and first == prev:
                run += 1
                max_opener = max(max_opener, run)
            else:
                run = 1
            prev = first
        if max_opener >= 4:
            self.add("opener", "段首重复", "advisory", max_opener,
                     f"连续 {max_opener} 段以同一人称开头；换动作/对话/物件切入。", [], 5)

        # 排序：阻断优先，扣分高优先
        self.findings.sort(key=lambda f: (f["severity"] != "blocking", -f["deduction"]))
        self.deduction = min(self.deduction, 80)

    @property
    def score(self) -> int:
        if self.chars < 100:
            return 0
        return max(0, 100 - self.deduction)

    @property
    def grade(self) -> str:
        if self.chars < 100:
            return "N/A"
        s = self.score
        if s >= 85:
            return "A"
        if s >= 75:
            return "B"
        if s >= 60:
            return "C"
        return "D"

    @property
    def blocking(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] == "blocking"]


def print_report(rep: Report, show_sections: bool) -> None:
    print("=" * 52)
    print("番茄短篇 · 模板腔扫描")
    print("=" * 52)
    print(f"文件      {rep.path}")
    print(f"字数      {rep.chars}（非空白）")
    print(f"段落      {len(rep.paras)}    对话行 {rep.dialog_lines}")
    print(f"指数      {rep.score}/100    等级 {rep.grade}")
    if rep.min_words > 0:
        if rep.chars < rep.min_words:
            print(f"字数门    未达标：{rep.chars} < {rep.min_words}")
        else:
            print(f"字数门    通过（≥{rep.min_words}）")
    print("-" * 52)

    if rep.chars < 100:
        print("正文过短（<100 字），不评分。请贴入完整正文或至少一节。")
        return
    if not rep.findings:
        print("未发现明显模板腔。仍请做语义十问和朗读测试。")
    for f in rep.findings:
        mark = "阻断" if f["severity"] == "blocking" else "建议"
        loc = ""
        if f["locations"]:
            loc = " 行" + ",".join(str(x) for x in f["locations"])
        print(f"[{mark}] {f['label']} ×{f['count']}（-{f['deduction']}）{loc}")
        print(f"  {f['message']}")
        if f["samples"]:
            print("  例：" + " | ".join(compact(s) for s in f["samples"][:3]))

    print("-" * 52)
    if rep.blocking:
        print(f"结论：有 {len(rep.blocking)} 类阻断项，先清零再谈润色。")
    elif rep.score >= 85:
        print("结论：可进入人工终检；重点看开篇、反转公平性和结尾。")
    elif rep.score >= 75:
        print("结论：可投但建议精修；优先处理扣分最高的 2–3 项。")
    else:
        print("结论：模板腔偏重，建议按 03-deslop 做一轮结构级改写。")
    print("说明：这是风格体检，不是 AIGC 检测器；分数不承诺过稿。")

    if show_sections:
        print("-" * 52)
        print("分节字数：")
        cur, buf = "导语", []
        for line in rep.lines:
            s = line.strip()
            if re.match(r"^(?:###\s*\d+\.?|###\s*第[一二三四五六七八九十百千0-9]+章|\d+\.)$", s):
                if buf:
                    print(f"  {cur}: {sum(visible_len(x) for x in buf)}")
                cur, buf = s, []
            elif s:
                buf.append(s)
        if buf:
            print(f"  {cur}: {sum(visible_len(x) for x in buf)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="番茄短篇模板腔扫描（风格体检，不是 AIGC 检测器）")
    ap.add_argument("files", nargs="+", help="正文文件（UTF-8）")
    ap.add_argument("--min-words", type=int, default=0, help="最低字数，短故事填 6000")
    ap.add_argument("--strict", action="store_true", help="有阻断或指数<75 时退出 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--sections", action="store_true", help="输出分节字数")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    all_reports = []
    failed = False
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            print(f"文件不存在：{name}", file=sys.stderr)
            failed = True
            continue
        text = mask_whitelist(strip_frontmatter(read_text(path)), load_whitelist(path))
        rep = Report(path, text, args.min_words)
        rep.run()
        all_reports.append(rep)

    if args.json:
        payload = {
            "files": [{
                "path": str(r.path),
                "chars": r.chars,
                "paragraphs": len(r.paras),
                "dialog_lines": r.dialog_lines,
                "score": r.score,
                "grade": r.grade,
                "word_gate": r.chars >= r.min_words,
                "findings": r.findings,
            } for r in all_reports],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for i, rep in enumerate(all_reports):
            if i:
                print()
            print_report(rep, args.sections)

    if failed:
        return 2
    if args.strict:
        for r in all_reports:
            if r.blocking or r.score < 75 or r.chars < args.min_words:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
