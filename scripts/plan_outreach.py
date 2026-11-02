#!/usr/bin/env python3
"""plan_outreach.py — 筛在招岗位、按简历匹配、生成投递话术草稿

用法:
    python plan_outreach.py --normalized .jobpilot/normalized.json \
        --resume resume.md --out .jobpilot/outreach --days 60

    # 只出岗位清单，不生成话术
    python plan_outreach.py --normalized .jobpilot/normalized.json \
        --out .jobpilot/outreach --days 60

输出:
    <out>/plan.json
    <out>/messages.md
    <out>/msg-<note_id>.txt   每条一份，供 collect.mjs message 使用

退出码: 0 成功｜2 参数错误｜5 数据缺失
"""

import argparse
import json
import math
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

SKILL_VOCAB = [
    "python", "java", "go", "golang", "c++", "c#", "rust", "php", "ruby",
    "javascript", "typescript", "node", "node.js", "react", "vue", "angular",
    "spring", "django", "flask", "fastapi", "mysql", "postgresql", "redis",
    "mongodb", "elasticsearch", "kafka", "rabbitmq", "docker", "kubernetes",
    "k8s", "linux", "git", "nginx", "grpc", "http", "tcp", "算法", "数据结构",
    "机器学习", "深度学习", "nlp", "cv", "推荐", "搜索", "大模型", "llm",
    "数据分析", "sql", "excel", "tableau", "powerbi", "bi", "ab测试", "埋点",
    "产品设计", "用户研究", "需求分析", "原型", "axure", "figma", "sketch",
    "项目管理", "运营", "增长", "商业化", "投放", "英语", "cet-6", "雅思",
    "沟通", "写作", "视频剪辑", "ps", "pr", "springboot", "nodejs",
]

# 同义词归一，避免 node 与 node.js、go 与 golang 被重复计数
SKILL_CANON = {
    "node.js": "node",
    "nodejs": "node",
    "golang": "go",
    "springboot": "spring",
}

ROLE_WORDS = [
    "后端", "前端", "客户端", "算法", "数据", "产品", "运营", "测试", "运维",
    "设计", "市场", "销售", "人力", "财务", "法务", "项目管理", "研发", "实习",
]

CITY_LIST = [
    "北京", "上海", "深圳", "广州", "杭州", "成都", "南京", "武汉", "西安",
    "苏州", "厦门", "长沙", "重庆", "天津", "合肥", "青岛", "郑州", "大连",
    "珠海", "东莞", "佛山", "宁波", "无锡", "济南", "福州", "远程",
]

SIGNAL_NOISE = {"加微信", "私信我", "付费", "收费", "代写", "保过", "包过"}

SKILL_DISPLAY = {
    "python": "Python",
    "java": "Java",
    "go": "Go",
    "golang": "Go",
    "mysql": "MySQL",
    "redis": "Redis",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "K8s",
    "linux": "Linux",
    "git": "Git",
    "sql": "SQL",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring": "Spring",
    "node": "Node.js",
    "node.js": "Node.js",
    "react": "React",
    "vue": "Vue",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "c++": "C++",
    "c#": "C#",
    "nlp": "NLP",
    "llm": "LLM",
    "bi": "BI",
    "excel": "Excel",
    "tableau": "Tableau",
    "figma": "Figma",
    "ps": "PS",
    "pr": "PR",
}

ANGLE_TAG_RE = re.compile(r"<[^<>]{1,80}>")


def display_skill(word):
    return SKILL_DISPLAY.get(word, word)


def log(message):
    print(message, file=sys.stderr)


def read_text(path):
    target = Path(path)
    if target.suffix.lower() == ".pdf":
        log("PDF 需要先转成文本：请用 extract-text 或复制为 .md/.txt 后再传 --resume")
        sys.exit(2)
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log("读取简历失败: %s" % exc)
        sys.exit(2)


def clean_line(line):
    """去掉 Markdown 标题、列表符号、引用符号。"""
    value = re.sub(r"^\s*[>#*+\-]+\s*", "", line)
    return value.strip()


def has_angle_tag(value):
    return bool(ANGLE_TAG_RE.search(value or ""))


def hit_vocab(text):
    """按词边界匹配技能词。

    边界只排除字母，不排除数字，因此 "Python3"、"Vue3"、"C++11" 仍能命中；
    "javascript" 不会命中 "java"，"qps" 不会命中 "ps"。
    """
    lowered = text.lower()
    hits = []
    for word in SKILL_VOCAB:
        needle = word.lower()
        if re.search(r"[\u4e00-\u9fa5]", needle):
            matched = needle in lowered
        else:
            pattern = r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])"
            matched = re.search(pattern, lowered) is not None
        if not matched:
            continue
        canonical = SKILL_CANON.get(needle, needle)
        if canonical not in hits:
            hits.append(canonical)
    return hits


def hit_roles(text):
    return [word for word in ROLE_WORDS if word in text]


def extract_city(text):
    for match in re.finditer(r"base\s*[:：]?\s*([\u4e00-\u9fa5]{2,8})", text, re.IGNORECASE):
        candidate = match.group(1)
        for city in CITY_LIST:
            if city in candidate:
                return city
    for city in CITY_LIST:
        if city in text:
            return city
    return None


def parse_resume(text):
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    cleaned = [clean_line(line) for line in lines]

    name = ""
    for line in cleaned:
        match = re.match(r"^(?:姓名|名字)[:：]\s*(.+)$", line)
        if match and not has_angle_tag(match.group(1)):
            name = match.group(1).strip()
            break
    if not name:
        for raw in lines[:8]:
            if raw.lstrip().startswith("#"):
                continue
            line = clean_line(raw)
            if not line or ":" in line or "：" in line:
                continue
            if has_angle_tag(line):
                continue
            if re.search(r"简历|模板|示例|Test|TEST", line):
                continue
            if 2 <= len(line) <= 20:
                name = line
                break

    education = ""
    for line in cleaned:
        if re.search(r"大学|学院|学校|University|College", line) and not has_angle_tag(line):
            education = re.sub(
                r"^(?:学校|院校|毕业院校|教育背景)[:：]\s*", "", line
            ).strip()
            break

    target_role = ""
    for line in cleaned:
        match = re.match(r"^(?:目标岗位|求职意向|意向岗位)[:：]\s*(.+)$", line)
        if match and not has_angle_tag(match.group(1)):
            target_role = match.group(1).strip()
            break

    cities = []
    for line in cleaned:
        match = re.match(r"^(?:期望城市|意向城市|工作地点|期望地点)[:：]\s*(.+)$", line)
        if match and not has_angle_tag(match.group(1)):
            cities = [
                item.strip()
                for item in re.split(r"[、,，/|]", match.group(1))
                if item.strip() and not has_angle_tag(item)
            ]
            break

    projects = []
    for raw in lines:
        if raw.startswith("### "):
            title = clean_line(raw)
            if title and not has_angle_tag(title) and not re.search(r"[<>`]", title):
                projects.append(title)
    if not projects:
        for raw in lines:
            if raw.lstrip().startswith("#"):
                continue
            line = clean_line(raw)
            if not line or re.search(r"[<>`]", line):
                continue
            if re.search(r"项目|实习|工作", line) and 4 <= len(line) <= 60:
                projects.append(line[:60])

    achievements = []
    for line in cleaned:
        if has_angle_tag(line) or re.search(r"[<>`]", line):
            continue
        if re.search(r"\d", line) and re.search(r"提升|增长|降低|达到|准确|覆盖|QPS|DAU|万|%|倍", line):
            achievements.append(line[:120])

    return {
        "name": name,
        "education": education,
        "target_role": target_role,
        "cities": cities,
        "projects": projects[:5],
        "achievements": achievements[:5],
        "skills": hit_vocab(text),
        "roles": hit_roles(text),
        "chars": len(text),
        "is_template": len(ANGLE_TAG_RE.findall(text)) >= 3,
    }


def age_days(published_at, now):
    if not published_at:
        return None
    try:
        published = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return (now - published).days


def freshness(age):
    if age is None:
        return 0.3
    if age <= 7:
        return 1.0
    if age <= 30:
        return 0.8
    if age <= 60:
        return 0.5
    return 0.2


def heat(post):
    likes = post.get("likes") or 0
    collects = post.get("collects") or 0
    return min(1.0, math.log10(likes + collects + 1) / 3.0)


def score_job(post, resume):
    text = (post.get("title") or "") + "\n" + (post.get("desc") or "")
    job_skills = hit_vocab(text)
    job_roles = hit_roles(text)
    job_city = extract_city(text)

    if job_skills:
        matched = [word for word in job_skills if word in resume["skills"]]
        missing = [word for word in job_skills if word not in resume["skills"]]
        cover = len(matched) / float(len(job_skills))
    else:
        matched = []
        missing = []
        cover = 0.5

    role_targets = resume["roles"] or ([resume["target_role"]] if resume["target_role"] else [])
    if job_roles and role_targets:
        role_hits = [word for word in job_roles if any(word in target for target in role_targets)]
        role = min(1.0, len(role_hits) / float(len(job_roles)))
    else:
        role_hits = []
        role = 0.0

    if not job_city or not resume["cities"]:
        city_match = 0.5
    elif job_city in ("远程",) or any(city in job_city or job_city in city for city in resume["cities"]):
        city_match = 1.0
    else:
        city_match = 0.0

    days = age_days(post.get("published_at"), resume["_now"])
    fresh = freshness(days)
    hot = heat(post)

    total = 100.0 * (
        0.50 * cover + 0.18 * role + 0.14 * fresh + 0.08 * hot + 0.10 * city_match
    )
    return {
        "score": int(round(total)),
        "parts": {
            "skill_cover": round(cover, 3),
            "role_match": round(role, 3),
            "freshness": fresh,
            "heat": round(hot, 3),
            "city_match": city_match,
        },
        "job_skills": job_skills,
        "matched_skills": matched,
        "missing_skills": missing,
        "role_hits": role_hits,
        "city": job_city,
        "age_days": days,
    }


def value_or_hint(value, hint):
    return value if value else "[请补充：%s]" % hint


def clean_job_title(post):
    raw_title = (post.get("title") or "该岗位").strip()
    cleaned = re.sub(r"(招聘|在招|急招|急聘|内推|内部推荐|校招|社招|长期有效)", " ", raw_title)
    cleaned = re.sub(r"base\s*[^\s，,。;；]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[\s#【\[]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or raw_title)[:40]


def build_message(post, resume, matched, channel):
    title = clean_job_title(post)
    name = value_or_hint(resume["name"], "称呼")
    education = value_or_hint(resume["education"], "学校/专业")
    project = value_or_hint(resume["projects"][0] if resume["projects"] else "", "项目名")
    achievement = value_or_hint(
        resume["achievements"][0] if resume["achievements"] else "", "可量化成果"
    )
    skills = (
        "、".join(display_skill(word) for word in matched[:3])
        if matched
        else "[请补充：与岗位最相关的 2 个技能]"
    )

    if channel == "email":
        lines = [
            "您好，",
            "我是%s，%s，想应聘%s。" % (name, education, title),
            "我在%s中负责相关模块，%s。" % (project, achievement),
            "看到岗位要求%s，和我的经历直接对应。" % skills,
            "简历见附件，期待您的回复，谢谢。",
        ]
    else:
        lines = [
            "您好，我是%s，%s，想投%s。" % (name, education, title),
            "我在%s中负责相关模块，%s。" % (project, achievement),
            "看到岗位要求%s，和我的经历直接对应。" % skills,
            "您这边方便给个投递邮箱吗？我把简历发过去，谢谢。",
        ]
    return "\n".join(lines)


def build_subject(post, resume):
    title = clean_job_title(post)
    name = resume["name"] or "[请补充：姓名]"
    education = resume["education"] or ""
    short_edu = re.split(r"[\s\-—/]", education)[0] if education else ""
    parts = ["应聘" + title, name]
    if short_edu:
        parts.append(short_edu)
    return " - ".join(parts)


def build_mail(post, resume, matched, email):
    subject = build_subject(post, resume)
    body = build_message(post, resume, matched, "email")
    mailto = "mailto:%s?subject=%s&body=%s" % (
        email,
        urllib.parse.quote(subject),
        urllib.parse.quote(body),
    )
    attachment = (resume["name"] or "姓名") + "-" + clean_job_title(post) + ".pdf"
    text = "\n".join(
        [
            "# 邮件投递草稿",
            "",
            "- 收件人：%s" % email,
            "- 主题：%s" % subject,
            "- 附件：简历 PDF（建议命名：%s）" % attachment,
            "- 一键起草：%s" % mailto,
            "",
            "---",
            "",
            body,
            "",
        ]
    )
    return {"subject": subject, "body": body, "mailto": mailto, "text": text}


def render_markdown(plan):
    lines = ["# 投递清单", ""]
    meta = plan["meta"]
    lines.append("> 生成时间：%s　窗口：近 %d 天　简历：%s" % (
        meta["generated_at"], meta["days"], meta["resume"] or "未提供"
    ))
    lines.append("")
    lines.append("| # | 岗位 | 匹配分 | 城市 | 发布时间 | 投递邮箱 | 命中技能 | 缺失技能 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for index, job in enumerate(plan["jobs"], start=1):
        lines.append("| %d | %s | %d | %s | %s | %s | %s | %s |" % (
            index,
            (job.get("title") or "")[:40].replace("|", " "),
            job["score"],
            job.get("city") or "未知",
            job.get("published_at") or "未知",
            job.get("mail_to") or "无邮箱，需私信询问",
            "、".join(display_skill(word) for word in job["matched_skills"]) or "-",
            "、".join(display_skill(word) for word in job["missing_skills"]) or "-",
        ))
    lines.append("")

    if plan.get("undated_jobs"):
        lines.append("## 时间未知的招聘帖（需人工确认是否仍在招）")
        lines.append("")
        for job in plan["undated_jobs"]:
            lines.append("- %s（%s）" % (job.get("title") or job["note_id"], job.get("url") or "-"))
        lines.append("")

    if plan.get("resume_profile"):
        lines.append("## 简历画像")
        lines.append("")
        profile = plan["resume_profile"]
        lines.append("- 姓名占位：%s" % (profile["name"] or "未识别"))
        lines.append(
            "- 技能：%s"
            % ("、".join(display_skill(word) for word in profile["skills"]) or "未识别")
        )
        lines.append("- 期望城市：%s" % ("、".join(profile["cities"]) or "未填写"))
        lines.append("- 项目：%s" % ("、".join(profile["projects"]) or "未识别"))
        lines.append("")

    lines.append("## 投递内容")
    lines.append("")
    for job in plan["jobs"]:
        if not (job.get("mail_body") or job.get("message")):
            continue
        lines.append("### %s（%d 分）" % (job.get("title") or job["note_id"], job["score"]))
        lines.append("")
        lines.append("- 链接：%s" % (job.get("url") or "-"))
        if job.get("mail_to"):
            lines.append("- 渠道：邮件（%s）" % job["mail_to"])
            lines.append("- 主题：%s" % job.get("mail_subject"))
            lines.append("- 一键起草：%s" % job.get("mailto"))
            lines.append("- 附件：简历 PDF，发送前请确认已添加")
            body = job.get("mail_body")
        else:
            lines.append("- 渠道：站内私信（帖子未留邮箱，先询问投递邮箱再发简历）")
            body = job.get("message")
        lines.append("- 状态：%s" % job["status"])
        if job.get("warnings"):
            lines.append("- 注意：%s" % "；".join(job["warnings"]))
        lines.append("")
        lines.append("```text")
        lines.append(body)
        lines.append("```")
        lines.append("")

    lines.append("## 风险提示")
    lines.append("")
    lines.append("- 本清单来自小红书公开笔记，招聘真实性未核实，投递前请通过官网/企业邮箱二次确认。")
    lines.append("- 任何要求付费、押金、培训费、提供身份证原件或银行卡的“内推”，均为高风险，建议直接放弃。")
    lines.append("- 站内私信请控制频率（建议每天不超过 5 条），避免账号被限流。")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="筛在招岗位并按简历匹配")
    parser.add_argument("--normalized", required=True, help="normalized.json")
    parser.add_argument("--resume", help="简历文本（.md/.txt），不提供则只出岗位清单")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--days", type=int, default=60, help="时间窗口天数，默认 60")
    parser.add_argument("--companies", help="只用这些企业的帖子，逗号分隔")
    parser.add_argument("--now", help="基准时间 YYYY-MM-DDTHH:MM:SS，便于复现")
    args = parser.parse_args()

    normalized_path = Path(args.normalized)
    if not normalized_path.is_file():
        log("找不到归一化数据: %s" % normalized_path)
        sys.exit(5)

    if args.now:
        try:
            now = datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            log("--now 格式应为 YYYY-MM-DDTHH:MM:SS")
            sys.exit(2)
    else:
        now = datetime.now().replace(microsecond=0)

    with open(normalized_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    posts = data.get("posts") or []

    company_filters = [item.strip() for item in (args.companies or "").split(",") if item.strip()]

    resume = None
    if args.resume:
        resume_text = read_text(args.resume)
        if len(resume_text.strip()) < 20:
            log("简历内容过短，无法匹配")
            sys.exit(2)
        resume = parse_resume(resume_text)
        resume["_now"] = now

    candidates = []
    undated = []
    skipped = {
        "not_recruitment": 0,
        "too_old": 0,
        "noise": 0,
        "company_filter": 0,
        "unknown_date": 0,
    }

    for post in posts:
        if not post.get("is_recruitment"):
            skipped["not_recruitment"] += 1
            continue
        noise = set(post.get("noise_flags") or []) & SIGNAL_NOISE
        if noise:
            skipped["noise"] += 1
            continue
        if company_filters:
            text = (post.get("title") or "") + (post.get("desc") or "")
            if not any(name.lower() in text.lower() for name in company_filters):
                skipped["company_filter"] += 1
                continue
        days = age_days(post.get("published_at"), now)
        if days is None:
            skipped["unknown_date"] += 1
            undated.append(
                {
                    "note_id": post.get("note_id"),
                    "title": post.get("title"),
                    "url": post.get("url"),
                    "published_text": post.get("published_text"),
                }
            )
            continue
        if days > args.days:
            skipped["too_old"] += 1
            continue
        candidates.append(post)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for post in candidates:
        contacts = post.get("contacts") or {}
        emails = contacts.get("emails") or []
        links = contacts.get("links") or []
        mail = None

        if resume:
            scored = score_job(post, resume)
            channel = "email" if emails else ("official_link" if links else "dm")
            status = "draft"
            warnings = []
            if resume.get("is_template"):
                scored["score"] = 0
                message = ""
                status = "needs_resume"
                warnings.append("简历疑似模板（含多个 <占位符>），未生成投递内容；请填写真实内容后重跑")
                message_path = None
            else:
                if channel == "email":
                    mail = build_mail(post, resume, scored["matched_skills"], emails[0])
                    message = ""
                    mail_file = out_dir / ("mail-" + str(post.get("note_id")) + ".md")
                    mail_file.write_text(mail["text"], encoding="utf-8")
                    message_path = str(mail_file)
                else:
                    message = build_message(post, resume, scored["matched_skills"], "dm")
                    message_file = out_dir / ("msg-" + str(post.get("note_id")) + ".txt")
                    message_file.write_text(message + "\n", encoding="utf-8")
                    message_path = str(message_file)

                text_to_check = mail["body"] if mail else message
                if "[请补充" in text_to_check or has_angle_tag(text_to_check):
                    warnings.append("投递内容含待补充占位，发送前必须补全")
                if scored["parts"].get("city_match") == 0.0:
                    warnings.append(
                        "工作地点与期望城市不一致：%s" % (scored.get("city") or "未知")
                    )
                if channel == "dm":
                    warnings.append("帖子未留邮箱，需先私信询问投递邮箱")
                if channel == "official_link":
                    warnings.append("帖子只给了官网/链接，建议走官方投递系统")
                if scored["score"] < 55:
                    warnings.append("匹配分偏低，建议先补缺口技能")
        else:
            scored = {
                "score": 0,
                "parts": {},
                "job_skills": hit_vocab(
                    (post.get("title") or "") + (post.get("desc") or "")
                ),
                "matched_skills": [],
                "missing_skills": [],
                "role_hits": [],
                "city": extract_city((post.get("title") or "") + (post.get("desc") or "")),
                "age_days": age_days(post.get("published_at"), now),
            }
            channel = "email" if emails else ("official_link" if links else "dm")
            message = ""
            status = "list_only"
            warnings = ["未提供简历，只输出岗位清单"]
            message_path = None

        jobs.append(
            {
                "note_id": post.get("note_id"),
                "title": post.get("title"),
                "url": post.get("url"),
                "author": post.get("author"),
                "published_at": post.get("published_at"),
                "published_text": post.get("published_text"),
                "age_days": scored["age_days"],
                "city": scored.get("city"),
                "score": scored["score"],
                "score_parts": scored["parts"],
                "job_skills": scored["job_skills"],
                "matched_skills": scored["matched_skills"],
                "missing_skills": scored["missing_skills"],
                "role_hits": scored["role_hits"],
                "contacts": contacts,
                "channel": channel,
                "mail_to": emails[0] if mail else None,
                "mail_subject": mail["subject"] if mail else None,
                "mail_body": mail["body"] if mail else None,
                "mailto": mail["mailto"] if mail else None,
                "signals": post.get("recruitment_signals") or [],
                "status": status,
                "message": message,
                "message_file": message_path,
                "warnings": warnings,
            }
        )

    jobs.sort(
        key=lambda item: (
            -item["score"],
            item["age_days"] if item["age_days"] is not None else 10 ** 6,
        )
    )

    resume_profile = None
    if resume:
        resume_profile = {
            key: value for key, value in resume.items() if not key.startswith("_")
        }

    plan = {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": str(normalized_path),
            "days": args.days,
            "resume": args.resume or "",
            "total_candidates": len(jobs),
            "skipped": skipped,
        },
        "resume_profile": resume_profile,
        "jobs": jobs,
        "undated_jobs": undated,
    }

    plan_path = out_dir / "plan.json"
    with open(plan_path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)

    messages_path = out_dir / "messages.md"
    messages_path.write_text(render_markdown(plan), encoding="utf-8")

    log("候选岗位 %d 个（跳过：%s）" % (len(jobs), skipped))
    if resume:
        log("简历技能命中 %d 个：%s" % (len(resume["skills"]), "、".join(resume["skills"][:12])))
        if resume["cities"]:
            log("期望城市：%s" % "、".join(resume["cities"]))

    print(
        json.dumps(
            {
                "status": "ok",
                "plan": str(plan_path),
                "messages": str(messages_path),
                "jobs": len(jobs),
                "undated": len(undated),
                "mode": "draft" if resume else "list_only",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()