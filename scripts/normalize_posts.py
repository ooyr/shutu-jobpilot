#!/usr/bin/env python3
"""normalize_posts.py — 把原始采集结果归一化为可分析的数据集

用法:
    python normalize_posts.py --raw .jobpilot/raw --out .jobpilot/normalized.json
    python normalize_posts.py --raw .jobpilot/raw --manual .jobpilot/manual/notes.md --out out.json

输入:
    --raw     目录（含 search-*.json / note-*.json）或单个 JSON 文件
    --manual  可选，人工回填的 Markdown

输出:
    stdout: 单个 JSON 对象（含 posts 数组与 diagnostics）
    stderr: 统计与警告

退出码:
    0 成功（允许空结果）
    2 参数或输入错误
    5 解析失败
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

RECRUITMENT_STRONG = [
    "招聘", "在招", "急招", "招人", "内推", "hc", "招募", "补录", "扩招",
    "名额", "jd", "投递邮箱", "简历发", "邮箱投递", "薪资", "日薪", "月薪",
]

RECRUITMENT_WEAK = [
    "实习", "校招", "社招", "岗位", "简历", "投递", "base", "转正", "到岗",
]

# 正则型强信号：覆盖"招测试开发工程师 1 名""团队直招"这类自然措辞
RECRUITMENT_PATTERNS = [
    r"直招|团队招|部门招",
    r"招\s*[\u4e00-\u9fa5A-Za-z0-9]{1,10}\s*[1-9]\s*名",
    r"内推码|内推链接",
]

INTERVIEW_WORDS = [
    "面经", "面试", "一面", "二面", "三面", "四面", "hr面", "笔试", "群面",
    "终面", "oc", "offer", "凉经", "挂了", "泡池子", "测评", "反问",
]

NOISE_WORDS = [
    "加微信", "私信我", "付费", "收费", "收钱", "代写", "简历修改",
    "保offer", "保过", "包过", "培训班", "内推费", "押金", "返现", "扫码进群",
    "有偿", "接单",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
OBFUSCATED_AT_RE = re.compile(r"[\(\[（【]\s*at\s*[\)\]）】]", re.IGNORECASE)
OBFUSCATED_DOT_RE = re.compile(r"[\(\[（【]\s*dot\s*[\)\]）】]", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fa5\"'<>）)】]+")

TIME_PREFIX_RE = re.compile(r"^(?:编辑于|发布于|更新于|发表于)\s*")


def log(message):
    print(message, file=sys.stderr)


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def parse_clock(text):
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return 0, 0
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return 0, 0
    return hour, minute


def _normalize_core(text, now):
    """在"整串都是时间"的前提下解析。外层 normalize_time 负责剥离噪声后重试。"""
    if not text:
        return None, None

    if text in ("刚刚", "刚才", "现在", "今天"):
        hour, minute = parse_clock(text)
        base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if text in ("刚刚", "刚才"):
            return now.replace(second=0, microsecond=0), "medium"
        return base, "medium"

    if text.startswith("昨天"):
        hour, minute = parse_clock(text)
        base = now - timedelta(days=1)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0), "medium"

    if text.startswith("前天"):
        hour, minute = parse_clock(text)
        base = now - timedelta(days=2)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0), "medium"

    relative_units = {
        "秒前": "seconds",
        "秒钟前": "seconds",
        "分钟前": "minutes",
        "小时前": "hours",
        "天前": "days",
        "周前": "weeks",
        "个星期前": "weeks",
        "个月前": "months",
        "月前": "months",
        "年前": "years",
    }
    for suffix, unit in relative_units.items():
        pattern = r"^(\d+)\s*" + re.escape(suffix) + r"$"
        match = re.match(pattern, text)
        if not match:
            continue
        amount = int(match.group(1))
        if unit == "seconds":
            return now - timedelta(seconds=amount), "medium"
        if unit == "minutes":
            return now - timedelta(minutes=amount), "medium"
        if unit == "hours":
            return now - timedelta(hours=amount), "medium"
        if unit == "days":
            return now - timedelta(days=amount), "medium"
        if unit == "weeks":
            return now - timedelta(weeks=amount), "medium"
        if unit == "months":
            return now - timedelta(days=30 * amount), "medium"
        if unit == "years":
            return now - timedelta(days=365 * amount), "medium"

    full_patterns = [
        (r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?(?:\s*(\d{1,2}):(\d{2}))?$", "high"),
        (r"^(\d{4})(\d{2})(\d{2})$", "high"),
    ]
    for pattern, confidence in full_patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        hour = int(match.group(4)) if match.lastindex and match.lastindex >= 5 and match.group(4) else 0
        minute = int(match.group(5)) if match.lastindex and match.lastindex >= 5 and match.group(5) else 0
        try:
            return now.replace(
                year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
            ), confidence
        except ValueError:
            return None, None

    short_patterns = [
        r"^(\d{1,2})[-/月.](\d{1,2})日?(?:\s*(\d{1,2}):(\d{2}))?$",
        r"^(\d{1,2})[-/.](\d{1,2})$",
    ]
    for pattern in short_patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        month = int(match.group(1))
        day = int(match.group(2))
        hour = int(match.group(3)) if match.lastindex and match.lastindex >= 4 and match.group(3) else 0
        minute = int(match.group(4)) if match.lastindex and match.lastindex >= 4 and match.group(4) else 0
        year = now.year
        try:
            candidate = now.replace(
                year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
            )
        except ValueError:
            return None, None
        if candidate > now + timedelta(days=1):
            try:
                candidate = candidate.replace(year=year - 1)
            except ValueError:
                return None, None
        return candidate, "low"

    return None, None


def normalize_time(raw_text, now):
    """把小红书的时间描述转成 (datetime|None, confidence)。

    confidence: high 表示年月日齐全；medium 表示相对时间；low 表示缺年份推断。
    小红书详情页常带"编辑于 3天前 广东"这类前缀/尾部地区，这里逐级剥离后重试。
    """
    if not raw_text:
        return None, None

    base = str(raw_text).strip()
    if not base:
        return None, None

    stripped = TIME_PREFIX_RE.sub("", base).strip()
    without_tail = re.sub(
        r"\s*(?:[·•]\s*)?(?:来自)?[\u4e00-\u9fa5]{2,8}$", "", stripped
    ).strip()

    candidates = [base, stripped, without_tail]
    for candidate in candidates:
        if not candidate:
            continue
        value, confidence = _normalize_core(candidate, now)
        if value is not None:
            return value, confidence
    return None, None


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%S") if value else None


def hit_words(text, words):
    lowered = text.lower()
    return [word for word in words if word in lowered]


def normalize_obfuscated(text):
    """把 hr(at)company(dot)com 这类反爬写法还原成邮箱。"""
    text = OBFUSCATED_AT_RE.sub("@", text)
    text = OBFUSCATED_DOT_RE.sub(".", text)
    return text.replace("＠", "@").replace("．", ".")


def extract_contacts(text):
    normalized = normalize_obfuscated(text)
    emails = sorted(set(match.group(0) for match in EMAIL_RE.finditer(normalized)))
    links = []
    for match in URL_RE.finditer(normalized):
        url = match.group(0).rstrip(".,;:!?")
        if "xiaohongshu.com" in url:
            continue
        if url not in links:
            links.append(url)
    return {"emails": emails, "links": links[:10]}


def iter_raw_files(raw_path):
    target = Path(raw_path)
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    files = []
    for pattern in ("search-*.json", "note-*.json"):
        files.extend(sorted(target.glob(pattern)))
    return files


def parse_manual(path, now):
    """解析人工回填的 Markdown。

    格式:
        ## 标题
        url: ...
        author: ...
        published: 2026-09-20
        likes: 128
        正文...

        ### 评论
        - 昵称: 内容
    """
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.split(r"^##\s+", text, flags=re.MULTILINE)
    posts = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        title = lines[0].strip()
        body_lines = []
        comments = []
        in_comments = False
        for line in lines[1:]:
            stripped = line.strip()
            if re.match(r"^###\s*评论", stripped):
                in_comments = True
                continue
            if in_comments:
                match = re.match(r"^[-*]\s*([^:：]{1,40})[:：]\s*(.+)$", stripped)
                if match:
                    comments.append(
                        {
                            "cid": None,
                            "author": match.group(1).strip(),
                            "content": match.group(2).strip(),
                            "likes": None,
                            "time_text": None,
                            "is_reply": False,
                        }
                    )
                elif stripped:
                    body_lines.append(stripped)
                continue
            if ":" in stripped or "：" in stripped:
                key, _, value = re.split(r"[:：]", stripped, maxsplit=1)
                if key.strip().lower() in ("url", "author", "published", "likes"):
                    body_lines.append("\u0000" + key.strip().lower() + "=" + value.strip())
                    continue
            if stripped:
                body_lines.append(stripped)

        meta = {"url": None, "author": "", "published": None, "likes": None}
        body = []
        for item in body_lines:
            if item.startswith("\u0000"):
                key, _, value = item[1:].partition("=")
                if key == "likes":
                    try:
                        meta["likes"] = int(value)
                    except ValueError:
                        meta["likes"] = None
                else:
                    meta[key] = value or None
            else:
                body.append(item)

        note_id = None
        if meta["url"]:
            match = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", meta["url"])
            note_id = match.group(1) if match else None
        if not note_id:
            note_id = "manual-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]

        posts.append(
            {
                "note_id": note_id,
                "url": meta["url"],
                "title": title,
                "desc": "\n".join(body),
                "author": meta["author"] or "",
                "published_text": meta["published"],
                "likes": meta["likes"],
                "collects": None,
                "comments_count": len(comments),
                "comments": comments,
                "source": "manual",
                "diagnostics": {"comments_partial": False},
            }
        )
    return posts


def merge_records(search_items, notes):
    """搜索卡片提供发现信息，详情提供正文与评论。note 优先。"""
    merged = {}
    for item in search_items:
        key = item.get("note_id")
        if not key:
            continue
        merged[key] = {
            "note_id": key,
            "url": item.get("url"),
            "title": item.get("title") or "",
            "desc": "",
            "author": item.get("author") or "",
            "published_text": item.get("published_text"),
            "likes": item.get("likes"),
            "collects": None,
            "comments_count": 0,
            "comments": [],
            "source": "search",
            "diagnostics": {},
        }
    for note in notes:
        key = note.get("note_id")
        if not key:
            continue
        base = merged.get(key, {})
        merged[key] = {
            "note_id": key,
            "url": note.get("url") or base.get("url"),
            "title": note.get("title") or base.get("title") or "",
            "desc": note.get("desc") or base.get("desc") or "",
            "author": note.get("author") or base.get("author") or "",
            "published_text": note.get("published_text") or base.get("published_text"),
            "likes": note.get("likes") if note.get("likes") is not None else base.get("likes"),
            "collects": note.get("collects"),
            "comments_count": note.get("comments_count") or len(note.get("comments") or []),
            "comments": note.get("comments") or [],
            "source": "browser",
            "diagnostics": note.get("diagnostics") or {},
        }
    return list(merged.values())


def dedupe(posts):
    seen = {}
    order = []
    duplicates = 0
    for post in posts:
        note_id = post.get("note_id")
        if note_id:
            key = "id:" + note_id
        else:
            fingerprint = "|".join(
                [post.get("title") or "", post.get("author") or "", (post.get("desc") or "")[:200]]
            )
            key = "fp:" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
        if key in seen:
            duplicates += 1
            existing = seen[key]
            if len(post.get("desc") or "") > len(existing.get("desc") or ""):
                index = order.index(seen[key])
                order[index] = post
                seen[key] = post
            continue
        seen[key] = post
        order.append(post)
    return order, duplicates


def normalize_post(post, now):
    title = post.get("title") or ""
    desc = post.get("desc") or ""
    text = title + "\n" + desc

    published_at, confidence = normalize_time(post.get("published_text"), now)

    comments = []
    for comment in post.get("comments") or []:
        content = comment.get("content") or ""
        comment_time, comment_confidence = normalize_time(comment.get("time_text"), now)
        comments.append(
            {
                "cid": comment.get("cid"),
                "author": comment.get("author") or "",
                "content": content,
                "likes": comment.get("likes"),
                "time_text": comment.get("time_text"),
                "published_at": iso(comment_time),
                "published_at_confidence": comment_confidence,
                "is_reply": bool(comment.get("is_reply")),
                "noise_flags": hit_words(content, NOISE_WORDS),
            }
        )

    recruitment_strong = hit_words(text, RECRUITMENT_STRONG)
    for pattern in RECRUITMENT_PATTERNS:
        match = re.search(pattern, text)
        if match:
            token = match.group(0)
            if token not in recruitment_strong:
                recruitment_strong.append(token)
    recruitment_weak = hit_words(text, RECRUITMENT_WEAK)
    recruitment_signals = recruitment_strong + [
        word for word in recruitment_weak if word not in recruitment_strong
    ]
    interview_signals = hit_words(text, INTERVIEW_WORDS)
    noise_flags = hit_words(text, NOISE_WORDS)
    for comment in comments:
        for flag in comment["noise_flags"]:
            if flag not in noise_flags:
                noise_flags.append(flag)

    # 面经帖常出现"直招/内推/投递"等词，强信号 ≤1 且面试信号占主导时不算招聘帖
    is_recruitment = (
        (len(recruitment_strong) >= 1 and len(recruitment_weak) >= 1)
        or len(recruitment_strong) >= 2
    ) and not (len(interview_signals) >= 2 and len(recruitment_strong) <= 1)

    comment_text = "\n".join((comment.get("content") or "") for comment in comments)

    return {
        "note_id": post.get("note_id"),
        "url": post.get("url"),
        "title": title,
        "desc": desc,
        "author": post.get("author") or "",
        "likes": post.get("likes"),
        "collects": post.get("collects"),
        "comments_count": len(comments),
        "published_text": post.get("published_text"),
        "published_at": iso(published_at),
        "published_at_confidence": confidence,
        "source": post.get("source") or "browser",
        "is_recruitment": is_recruitment,
        "is_interview": len(interview_signals) >= 2,
        "recruitment_signals": recruitment_signals,
        "interview_signals": interview_signals,
        "noise_flags": noise_flags,
        "contacts": extract_contacts(text + "\n" + comment_text),
        "comments": comments,
        "diagnostics": post.get("diagnostics") or {},
    }


def main():
    parser = argparse.ArgumentParser(description="归一化小红书采集结果")
    parser.add_argument("--raw", required=True, help="原始采集目录或 JSON 文件")
    parser.add_argument("--manual", help="人工回填的 Markdown")
    parser.add_argument("--out", required=True, help="输出 normalized.json")
    parser.add_argument(
        "--now",
        help="用于时间换算的基准时间（ISO，默认当前时间），便于复现与测试",
    )
    args = parser.parse_args()

    if args.now:
        try:
            now = datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            log("--now 格式应为 YYYY-MM-DDTHH:MM:SS")
            sys.exit(2)
    else:
        now = datetime.now().replace(microsecond=0)

    raw_files = iter_raw_files(args.raw)
    if not raw_files and not args.manual:
        log("未找到任何原始数据：%s" % args.raw)
        sys.exit(2)

    search_items = []
    notes = []
    parse_errors = []

    for path in raw_files:
        try:
            data = load_json(path)
        except (OSError, ValueError) as exc:
            parse_errors.append({"file": str(path), "error": str(exc)})
            log("跳过无法解析的文件: %s (%s)" % (path, exc))
            continue
        if path.name.startswith("search-"):
            search_items.extend(data.get("items") or [])
        elif path.name.startswith("note-"):
            notes.append(data)

    merged = merge_records(search_items, notes)

    if args.manual:
        manual_path = Path(args.manual)
        if not manual_path.is_file():
            log("手动回填文件不存在: %s" % manual_path)
            sys.exit(2)
        merged.extend(parse_manual(manual_path, now))

    if not merged:
        log("原始数据中没有可用笔记")
        write_json(
            args.out,
            {
                "meta": {
                    "generated_at": iso(now),
                    "now": iso(now),
                    "raw_files": len(raw_files),
                    "manual": bool(args.manual),
                },
                "status": "empty",
                "posts": [],
                "diagnostics": {"parse_errors": parse_errors, "duplicates_removed": 0},
            },
        )
        print(json.dumps({"status": "empty", "out": args.out, "posts": 0}, ensure_ascii=False))
        return

    unique, duplicates_removed = dedupe(merged)
    posts = [normalize_post(post, now) for post in unique]
    posts.sort(key=lambda item: item.get("published_at") or "", reverse=True)

    undated = [post["note_id"] for post in posts if not post.get("published_at")]
    interview_count = sum(1 for post in posts if post["is_interview"])
    recruitment_count = sum(1 for post in posts if post["is_recruitment"])
    dated_ratio = round((len(posts) - len(undated)) / float(len(posts)), 3)
    comment_total = sum(len(post["comments"]) for post in posts)

    payload = {
        "meta": {
            "generated_at": iso(now),
            "now": iso(now),
            "raw_files": len(raw_files),
            "manual": bool(args.manual),
            "counts": {
                "posts": len(posts),
                "comments": comment_total,
                "interview": interview_count,
                "recruitment": recruitment_count,
                "from_manual": sum(1 for post in posts if post["source"] == "manual"),
            },
            "dated_ratio": dated_ratio,
        },
        "status": "ok",
        "posts": posts,
        "diagnostics": {
            "undated": undated,
            "duplicates_removed": duplicates_removed,
            "parse_errors": parse_errors,
        },
    }

    write_json(args.out, payload)

    log("笔记 %d 篇（去重移除 %d），评论 %d 条" % (len(posts), duplicates_removed, comment_total))
    log("时间可解析率 %.0f%%，未解析 %d 篇" % (dated_ratio * 100, len(undated)))
    if undated:
        log("未解析时间的笔记: %s" % ", ".join(undated[:10]))

    print(
        json.dumps(
            {
                "status": "ok",
                "out": args.out,
                "posts": len(posts),
                "comments": comment_total,
                "dated_ratio": dated_ratio,
                "undated": len(undated),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()