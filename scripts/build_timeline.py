#!/usr/bin/env python3
"""build_timeline.py — 按企业聚合面经，生成时间线骨架与报告草稿

用法:
    python build_timeline.py --normalized .jobpilot/normalized.json \
        --companies "字节跳动|字节|bytedance,腾讯|tencent" \
        --role "后端" --out-dir .jobpilot

说明:
    --companies 用逗号分隔企业，用竖线分隔同一企业的别名。
    脚本只做确定性聚合与证据摘录；语义结论由 Agent 在 report-draft.md 上补写。

输出:
    <out-dir>/dossier.json      结构化聚合结果
    <out-dir>/report-draft.md   带事实表的草稿

退出码: 0 成功｜2 参数错误｜5 数据缺失
"""

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

STAGE_ORDER = ["投递", "简历", "笔试", "测评", "一面", "二面", "三面", "四面", "HR面", "终面", "OC", "offer"]

STAGE_PATTERNS = [
    ("投递", r"投递|投简历|网申|内推"),
    ("笔试", r"笔试|在线测评|测评|机试|coding test"),
    ("一面", r"一面|1面|初面|第一面"),
    ("二面", r"二面|2面|复面|第二面"),
    ("三面", r"三面|3面|第三面"),
    ("四面", r"四面|4面|第四面"),
    ("HR面", r"hr面|hr 面|人力面"),
    ("终面", r"终面|终局面|最后一面"),
    ("OC", r"oc|意向书|口头offer"),
    ("offer", r"offer|录用|录取"),
]

RESULT_PATTERN = re.compile(r"oc|offer|挂了|凉了|凉经|泡池子|池子|感谢信|拒信|通过|上岸", re.IGNORECASE)
QUESTION_PATTERN = re.compile(r"[?？]|求问|想问|请问|有没有|多久|什么时候")
DATE_HINT_PATTERN = re.compile(
    r"(\d{1,2})\s*[.月]\s*(\d{1,2})\s*日?|(\d+)\s*天后|隔了?\s*(\d+)\s*天|一周后|两周后|当天|第二天"
)

COMPANY_SUFFIX_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{2,10})(?:公司|集团|科技|网络|信息|银行|证券|汽车|医疗)"
)
TITLE_HEAD_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z]{2,10})[\s\-_|｜:：]")

TITLE_STOPWORDS = {
    "面经", "面试", "实习", "校招", "社招", "求职", "招聘", "内推", "凉经",
    "分享", "记录", "大厂", "春招", "秋招", "日常", "我的", "求助", "请问",
}


def extract_company_candidates(posts):
    """从标题/正文中提取候选企业名，供用户或 Agent 选择。不做实体库推断。"""
    counter = Counter()
    for post in posts:
        title = (post.get("title") or "").strip()
        head = TITLE_HEAD_RE.match(title)
        if head:
            name = head.group(1)
            if name not in TITLE_STOPWORDS and not re.search(r"面经|面试|实习|招聘", name):
                counter[name] += 1
        text = title + " " + (post.get("desc") or "")[:200]
        for match in COMPANY_SUFFIX_RE.finditer(text):
            counter[match.group(0)] += 1
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common(20)
        if count >= 2
    ]


def log(message):
    print(message, file=sys.stderr)


def parse_companies(raw):
    groups = []
    if not raw:
        return groups
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        aliases = [alias.strip() for alias in chunk.split("|") if alias.strip()]
        if not aliases:
            continue
        groups.append({"name": aliases[0], "aliases": aliases})
    return groups


def match_company(post, groups):
    text = (post.get("title") or "") + "\n" + (post.get("desc") or "")
    lowered = text.lower()
    hits = []
    for group in groups:
        matched = []
        for alias in group["aliases"]:
            if alias.lower() in lowered:
                matched.append(alias)
        if matched:
            hits.append({"name": group["name"], "matched_aliases": matched})
    if not hits:
        return None, []
    hits.sort(key=lambda item: len(item["matched_aliases"]), reverse=True)
    return hits[0]["name"], [item["name"] for item in hits]


def split_sentences(text):
    parts = re.split(r"[\n。！!；;，]+", text)
    return [part.strip() for part in parts if part.strip()]


def find_date_hint(sentence):
    match = DATE_HINT_PATTERN.search(sentence)
    return match.group(0).strip() if match else None


def extract_process_evidence(post):
    evidence = []
    for sentence in split_sentences(post.get("desc") or ""):
        for stage, pattern in STAGE_PATTERNS:
            if re.search(pattern, sentence, re.IGNORECASE):
                evidence.append(
                    {
                        "note_id": post.get("note_id"),
                        "stage": stage,
                        "sentence": sentence[:200],
                        "date_hint": find_date_hint(sentence),
                        "published_at": post.get("published_at"),
                    }
                )
                break
    return evidence


def month_key(iso_text):
    if not iso_text:
        return None
    return iso_text[:7]


def comment_highlights(post, limit=8):
    rows = []
    for comment in post.get("comments") or []:
        if comment.get("noise_flags"):
            continue
        content = (comment.get("content") or "").strip()
        if len(content) < 6:
            continue
        rows.append(
            {
                "note_id": post.get("note_id"),
                "author": comment.get("author") or "",
                "content": content[:300],
                "likes": comment.get("likes") or 0,
                "published_at": comment.get("published_at"),
                "is_question": bool(QUESTION_PATTERN.search(content)),
                "is_result": bool(RESULT_PATTERN.search(content)),
            }
        )
    rows.sort(key=lambda item: item["likes"], reverse=True)
    return rows[:limit]


def build_company(group, posts):
    owned = [post for post in posts if post.get("_company") == group["name"]]
    interview_posts = [post for post in owned if post.get("is_interview")]
    recruitment_posts = [post for post in owned if post.get("is_recruitment")]

    stage_counter = Counter()
    evidence = []
    highlights = []
    questions = []
    results = []

    for post in interview_posts:
        for item in extract_process_evidence(post):
            stage_counter[item["stage"]] += 1
            if len(evidence) < 60:
                evidence.append(item)
        for row in comment_highlights(post):
            highlights.append(row)
            if row["is_question"] and len(questions) < 20:
                questions.append(row)
            if row["is_result"] and len(results) < 20:
                results.append(row)

    highlights.sort(key=lambda item: item["likes"], reverse=True)
    dated = [post.get("published_at") for post in owned if post.get("published_at")]
    months = Counter(month_key(value) for value in dated if month_key(value))

    gaps = []
    if not owned:
        gaps.append("该企业没有命中任何笔记，建议更换关键词或扩大时间窗口")
    if not interview_posts:
        gaps.append("没有识别到面经类笔记")
    if interview_posts and stage_counter.get("一面", 0) == 0:
        gaps.append("缺少一面相关信息")
    if interview_posts and sum(stage_counter.get(name, 0) for name in ("OC", "offer")) == 0:
        gaps.append("缺少最终结果相关信息，无法判断通过节奏")

    confidence = "low"
    if len(interview_posts) >= 3 and stage_counter.get("一面", 0) >= 3:
        confidence = "high"
    elif len(interview_posts) >= 2:
        confidence = "medium"

    return OrderedDict(
        [
            ("name", group["name"]),
            ("aliases", group["aliases"]),
            ("confidence", confidence),
            ("post_count", len(owned)),
            ("interview_posts", len(interview_posts)),
            ("recruitment_posts", len(recruitment_posts)),
            ("date_range", {"from": min(dated) if dated else None, "to": max(dated) if dated else None}),
            ("monthly", dict(sorted(months.items()))),
            ("stage_counts", {name: stage_counter.get(name, 0) for name in STAGE_ORDER if stage_counter.get(name)}),
            ("posts", [
                {
                    "note_id": post.get("note_id"),
                    "title": post.get("title"),
                    "author": post.get("author"),
                    "published_at": post.get("published_at"),
                    "published_text": post.get("published_text"),
                    "confidence": post.get("published_at_confidence"),
                    "likes": post.get("likes"),
                    "url": post.get("url"),
                    "is_interview": post.get("is_interview"),
                    "is_recruitment": post.get("is_recruitment"),
                }
                for post in sorted(owned, key=lambda item: item.get("published_at") or "", reverse=True)
            ]),
            ("process_evidence", evidence),
            ("comment_highlights", highlights[:15]),
            ("comment_questions", questions[:10]),
            ("result_signals", results[:10]),
            ("gaps", gaps),
        ]
    )


def render_draft(dossier):
    lines = []
    lines.append("# 面经与在招情报草稿")
    lines.append("")
    lines.append("> 本文件由脚本生成事实骨架。语义结论、可信度说明与建议由 Agent 补写后再交付给用户。")
    lines.append("")
    meta = dossier["meta"]
    lines.append("样本：%d 篇笔记，%d 条评论；时间可解析率 %.0f%%。" % (
        meta["posts"], meta["comments"], meta["dated_ratio"] * 100
    ))
    lines.append("")

    for company in dossier["companies"]:
        lines.append("## %s" % company["name"])
        lines.append("")
        lines.append("- 笔记 %d 篇（面经 %d，招聘 %d），可信度档位：%s" % (
            company["post_count"], company["interview_posts"], company["recruitment_posts"], company["confidence"]
        ))
        lines.append("- 时间范围：%s ~ %s" % (
            company["date_range"]["from"] or "未知", company["date_range"]["to"] or "未知"
        ))
        lines.append("")

        lines.append("### 发布时间线")
        lines.append("")
        lines.append("| 发布时间 | 标题 | 互动 | 置信度 | 链接 |")
        lines.append("|---|---|---|---|---|")
        for post in company["posts"][:20]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                post["published_at"] or post["published_text"] or "未知",
                (post["title"] or "")[:60],
                post["likes"] if post["likes"] is not None else "-",
                post["confidence"] or "-",
                post["url"] or "-",
            ))
        lines.append("")

        lines.append("### 流程证据（原文摘录）")
        lines.append("")
        if company["process_evidence"]:
            lines.append("| 阶段 | 原文 | 日期线索 | 来源 |")
            lines.append("|---|---|---|---|")
            for item in company["process_evidence"][:30]:
                lines.append("| %s | %s | %s | %s |" % (
                    item["stage"],
                    item["sentence"].replace("|", " ")[:120],
                    item["date_hint"] or "-",
                    item["note_id"] or "-",
                ))
        else:
            lines.append("未抽取到流程描述。")
        lines.append("")

        lines.append("### 评论区高互动内容")
        lines.append("")
        if company["comment_highlights"]:
            for row in company["comment_highlights"][:10]:
                lines.append("- [%s赞] %s：%s" % (
                    row["likes"], row["author"] or "匿名", row["content"][:160]
                ))
        else:
            lines.append("评论区没有可用内容（可能未加载或被过滤）。")
        lines.append("")

        if company["gaps"]:
            lines.append("### 数据缺口")
            lines.append("")
            for gap in company["gaps"]:
                lines.append("- %s" % gap)
            lines.append("")

    if dossier["unassigned"]:
        lines.append("## 未归类笔记")
        lines.append("")
        for post in dossier["unassigned"][:20]:
            lines.append("- %s（%s）" % (post["title"] or "无标题", post["published_at"] or "时间未知"))
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="按企业聚合面经与时间线")
    parser.add_argument("--normalized", required=True, help="normalized.json")
    parser.add_argument("--companies", help="企业列表，逗号分隔，竖线分隔别名；留空则只输出候选企业")
    parser.add_argument("--role", help="岗位方向关键词，用于标注但不强制过滤")
    parser.add_argument("--out-dir", required=True, help="输出目录")
    args = parser.parse_args()

    normalized_path = Path(args.normalized)
    if not normalized_path.is_file():
        log("找不到归一化数据: %s" % normalized_path)
        sys.exit(5)

    with open(normalized_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    posts = data.get("posts") or []
    groups = parse_companies(args.companies)
    candidates = extract_company_candidates(posts)

    if not groups and not candidates:
        log("没有指定 --companies，且未能从标题中识别候选企业。请提供企业名后重跑。")
        sys.exit(2)

    if not groups:
        log("未指定 --companies，已从采集结果中提取候选企业：")
        for item in candidates[:10]:
            log("  %s（出现 %d 次）" % (item["name"], item["count"]))
        log("请选择真实存在的企业名，用 --companies \"名称|别名,名称2\" 重新运行。")

    for post in posts:
        company, also = match_company(post, groups)
        post["_company"] = company
        post["_also"] = also

    companies = [build_company(group, posts) for group in groups]
    unassigned = [
        {
            "note_id": post.get("note_id"),
            "title": post.get("title"),
            "published_at": post.get("published_at"),
            "url": post.get("url"),
        }
        for post in posts
        if not post.get("_company")
    ]

    meta = data.get("meta") or {}
    dossier = OrderedDict(
        [
            (
                "meta",
                {
                    "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "source": str(normalized_path),
                    "posts": len(posts),
                    "comments": sum(len(post.get("comments") or []) for post in posts),
                    "dated_ratio": meta.get("dated_ratio", 0),
                    "role_filter": args.role,
                },
            ),
            ("companies", companies),
            ("candidate_companies", candidates),
            ("unassigned", unassigned),
        ]
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dossier_path = out_dir / "dossier.json"
    draft_path = out_dir / "report-draft.md"

    with open(dossier_path, "w", encoding="utf-8") as handle:
        json.dump(dossier, handle, ensure_ascii=False, indent=2)
    with open(draft_path, "w", encoding="utf-8") as handle:
        handle.write(render_draft(dossier))

    log("企业 %d 个，未归类 %d 篇" % (len(companies), len(unassigned)))
    for company in companies:
        log("  %s: %d 篇（面经 %d）" % (company["name"], company["post_count"], company["interview_posts"]))

    print(
        json.dumps(
            {
                "status": "ok",
                "dossier": str(dossier_path),
                "draft": str(draft_path),
                "companies": len(companies),
                "unassigned": len(unassigned),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()