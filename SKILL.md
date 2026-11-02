---
name: shutu-jobpilot
description: |
  从小红书采集真实面经帖与评论区内容，按企业与岗位聚合成可读的面试时间线，识别当前在招岗位，并结合用户简历生成可解释的匹配结果与投递话术。
  当用户想找工作或实习、想了解某公司的真实面试流程、想看某岗位的在招信息、想投递简历时使用。
  触发词：小红书面经、面试经验、实习内推、在招岗位、投简历、求职、校招、社招、offer、避雷、面试时间线。
license: MIT
allowed-tools: Bash(python:*) Bash(node:*) Bash(npm:*) Bash(playwright-cli:*) read write edit glob grep
---

# 薯途 JobPilot · 小红书求职情报与投递助手

> 把散落的小红书面经、评论区和在招帖，整理成一份能直接照着准备的求职作战包。

下文用 `<skill>` 表示本 Skill 的安装目录。命令在**用户工作目录**执行，采集数据落在当前目录的 `.jobpilot/`（不要写进 Skill 安装目录）。

## 能力边界（先读）

**能做**：用用户已登录的真实浏览器采集公开可见的笔记与评论；归一化相对时间；按企业/岗位聚合时间线；识别在招帖；按简历做可解释匹配；生成投递清单与话术草稿。

**不能做**：

- 不绕过登录、验证码、风控，不破解接口签名。
- 不保证招聘帖真实（内容可能过期或引流），所有岗位一律标注"待核实"。
- **不做批量自动投递**。招聘帖基本都留邮箱，本 Skill 的默认路径是生成邮件草稿（收件人/主题/正文/附件提醒），由你确认后发送；没留邮箱的帖子才走站内私信兜底（只预填，不自动发送）。批量自动发信违反平台规范且高概率封号。
- 不内置任何企业、岗位、薪资、面试题数据。所有事实只来自本次采集结果或用户输入。

## 输入

| 必填 | 说明 |
|---|---|
| 目标企业 | 1-5 个，全名或常用简称；不确定时先走"探索模式"（见 Phase 2.5） |
| 目标岗位/方向 | 如"后端开发""数据分析""产品实习" |
| 求职类型 | 校招 / 社招 / 实习 |
| 简历路径 | 需要投递时必填，`.md` / `.txt`（PDF 先用工具转文本） |

可选：时间窗口（默认近 60 天）、笔记上限（默认 30）、每篇评论上限（默认 40）。

## 工作流

### Phase 0 环境与登录

```bash
python "<skill>/scripts/check_env.py"
node "<skill>/scripts/collect.mjs" login
```

`check_env.py` 返回 `ok / degraded / blocked`。缺少 `playwright-cli` 时按提示安装 `npm install -g @playwright/cli@latest`，并执行 `playwright-cli install` 初始化 workspace。`collect.mjs` 会优先用系统 Chrome/Edge（`--browser chrome`），不需要额外下载。不要改用 requests/urllib 抓取，静态请求拿不到登录态、展开正文和评论区。

`login` 打开浏览器会话，用户扫码一次；会话由 CLI 保持，不要执行 `close`。返回 `logged_in=false` 或 `confidence=low` 时，请用户确认窗口右上角已显示头像，再继续。

**探索模式（用户只说"我学计算机，帮我看看有哪些实习"）**：先不要编企业名，用 `<岗位方向> <求职类型> 面经/在招` 这类泛化关键词跑一轮 `search`，再进 Phase 2/2.5；`build_timeline` 留空 `--companies` 会输出候选企业，交用户确认后再按企业精搜。

### Phase 1 采集

为每个企业生成 3-5 个搜索词（企业+面经、企业+岗位+面经、企业+实习/校招+面经、企业+内推/招聘），串行执行：

```bash
node "<skill>/scripts/collect.mjs" search --keyword "字节跳动 后端 面经" --out .jobpilot/raw --max-scroll 4
node "<skill>/scripts/collect.mjs" run --keyword "字节跳动 实习 内推" --out .jobpilot/raw --max-notes 8 --max-comments 40
```

`run` = 搜索 + 打开前 N 篇 + 抓正文与评论。脚本内置 3-6 秒随机间隔；会话级笔记预算 30 篇（跨多次 `run` 累计）。风控/验证码：`search` 返回 `status=error`、`reason=blocked`；`run` 中途发现则立即停止并返回 `status=partial`、`blocked=<reason>`，两者诊断都在 `diagnostics.blocked`。

**先冒烟再全量**：第一次接入时用 `--max-notes 1 --max-comments 10` 跑一次，检查输出 JSON 的 `cards_found` 与 `comments_loaded`。为 0 或 `blocked` 非空时，按 `references/collection-playbook.md` 的失败分类处理，不要直接全量采集。

### Phase 2 归一化

```bash
python "<skill>/scripts/normalize_posts.py" --raw .jobpilot/raw --out .jobpilot/normalized.json
```

完成时间归一化（含"编辑于 3天前 广东"这类带地区后缀的文本）、去重、招聘/面经/噪声信号标记。`published_at=null` 的条目会单独列出。

### Phase 2.5 企业聚合

```bash
python "<skill>/scripts/build_timeline.py" --normalized .jobpilot/normalized.json \
  --companies "字节跳动|字节|bytedance,腾讯|tencent" --role 后端 --out-dir .jobpilot
```

产出 `dossier.json`（阶段统计、流程证据原文、评论高互动、数据缺口）与 `report-draft.md`（事实骨架）。Phase 3 的语义判断以 `dossier.json` 为依据，不要手工重算时间与统计。

**探索模式（用户没指定企业时）**：先留空 `--companies` 运行，脚本会输出 `candidate_companies`（从采集结果标题中统计的候选，不是内置企业库）。把候选交用户确认后，再用真实企业名重跑；不要自己编造企业名。

### Phase 3 语义分析（Agent 主导）

读 `references/analysis-method.md`，基于 `dossier.json` 对每家企业完成：

- 面经流程时间线（投递→笔试→面试→OC，标注间隔天数与样本量）
- 面试问题聚类（按轮次/主题），只引用原文，不补写笔记里没出现的问题
- 评论区洞察：补充问题、通过/挂掉信号、避雷点、薪资口径，剔除引流与广告
- 可信度：`单帖` / `多帖一致` / `互相矛盾`，逐条标注

### Phase 4 在招岗位与匹配

```bash
python "<skill>/scripts/plan_outreach.py" --normalized .jobpilot/normalized.json \
  --resume <简历路径> --out .jobpilot/outreach --days 60
```

输出 `plan.json` 与 `messages.md`：岗位清单（含城市、匹配分、命中/缺失技能）、投递邮箱、邮件主题与正文、时间未知的招聘帖单列、无邮箱时的私信话术。

### Phase 5 投递（邮箱优先，人工确认）

小红书招聘帖绝大多数会直接留投递邮箱，**默认走邮件**：

1. `plan_outreach.py` 已从正文与评论区提取邮箱（含 `hr(at)xx(dot)com` 这类写法），并为每个岗位生成 `mail-<id>.md`：收件人、主题、正文、附件命名建议、`mailto:` 一键起草链接。
2. 用户核对收件人与附件简历后，用邮件客户端发送；`messages.md` 的岗位表直接列出投递邮箱。
3. 帖子没留邮箱时才走私信兜底，先问邮箱再发简历：

```bash
node "<skill>/scripts/collect.mjs" message --url "<帖子链接>" --text-file .jobpilot/outreach/msg-<id>.txt
```

该命令只打开对话并预填文本，**不自动点击发送**。不要批量自动发邮件或私信。

### Phase 6 报告

按 `assets/report-template.md` 输出 `.jobpilot/report.md`：结论摘要 → 企业卡片（时间线 + 高频问题 + 评论区洞察）→ 在招岗位表 → 投递清单 → 数据与方法（采集时间、样本量、覆盖率、无法核实项）。

## 决策规则

| 情况 | 处理 |
|---|---|
| 正文被"展开"折叠 | 先点击展开，仍失败则标 `partial`，只用标题与评论 |
| 评论加载失败 | 保留已有评论，标 `comments_partial` |
| 相对时间无法解析 | 标 `published_at=null`，按采集顺序排列，不猜日期；不放进"在招清单"，单列待确认 |
| 招聘帖无联系方式 | 标"需官网/内推核实"，不编造 HR 信息 |
| 用户没给简历但要投递 | 只输出岗位清单，不生成话术 |
| 采集被风控 | 立即停止（`reason=blocked`），说明已采集量，建议降低频率或改为手动粘贴 |

## 资源导航

| 资源 | 用途 |
|---|---|
| `references/collection-playbook.md` | 登录、采集、字段契约、失败分类、人工回填格式 |
| `references/analysis-method.md` | 时间线构建、评论噪声识别、报告结构 |
| `references/outreach-policy.md` | 招聘帖信号、匹配算法、话术规则、合规红线 |
| `scripts/build_timeline.py` | 企业聚合、阶段统计、报告事实骨架 |
| `assets/report-template.md` | 报告模板 |
| `assets/resume-profile.example.md` | 简历画像填写模板 |

## 数据与隐私

- 采集数据只落当前工作目录的 `.jobpilot/`，不写入 Skill 目录，不上传第三方。
- 简历只在本地解析；话术中不写入身份证、住址、完整手机号。
- 引用评论默认保留昵称，可用 `--anonymize` 打码。