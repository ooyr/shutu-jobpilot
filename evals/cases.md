# 验证用例与结果

> 本目录下的 fixtures 全部标记 `TEST DATA ONLY`，只用于验证脚本行为，不进入生产路径，也不代表任何真实企业、真实候选人。

## 1. 已验证（本机实际执行）

### Case A：数据管道端到端

```bash
python scripts/normalize_posts.py --raw evals/fixtures/raw --out /tmp/jobhunt/normalized.json --now 2026-11-01T10:00:00
python scripts/build_timeline.py --normalized /tmp/jobhunt/normalized.json \
  --companies "示例科技|示例" --role 后端 --out-dir /tmp/jobhunt
python scripts/plan_outreach.py --normalized /tmp/jobhunt/normalized.json \
  --resume evals/fixtures/resume-test.md --out /tmp/jobhunt/outreach \
  --days 60 --now 2026-11-01T10:00:00
```

预期：

- 3 篇笔记、6 条评论，时间可解析率 100%
- 面经 2 篇、招聘 1 篇
- 流程证据能抽出 投递/一面/二面/三面/OC 五个阶段
- 招聘候选 1 个，匹配分 90+，命中 Python/MySQL/Redis，生成 `mail-t003.md`（收件人 hr@example.com）
- 话术无未填占位

实际：全部符合。输出见 `report-draft.md` 与 `messages.md` 的结构。

### Case B：招聘帖误判回归

第一版规则把"实习 + 投递"当成招聘信号，导致两篇面经被误判为招聘帖（候选 2 个）。
改为"强招聘信号 + 弱信号组合，且面试信号占主导时不算招聘"后，候选降为 1 个，`is_recruitment` 正确。

### Case C：时间归一化

覆盖：`刚刚`、`3天前`、`昨天 12:30`、`2026-09-20`、`09-20`（推断年份）、`编辑于 3天前`、非法文本。
结果：相对时间按基准时间换算，缺年份标 `low`，无法解析返回 `null` 且不猜日期。

### Case D：无简历的 list_only

```bash
python scripts/plan_outreach.py --normalized ... --out .../outreach-list --days 60
```

预期：`mode=list_only`，岗位清单存在，`message` 为空，状态 `list_only`。
实际：符合，`messages.md` 不生成话术段落。

### Case E：参数与安全问题

| 输入 | 预期 | 实际 |
|---|---|---|
| `--keyword "a\`b"`（含反引号） | 退出码 2，`keyword_invalid` | 符合 |
| `--url "https://example.com/x"` | 退出码 2，`invalid_url` | 符合 |
| 缺少 `--keyword` | 退出码 2，`missing_keyword` | 符合 |

### Case F：环境自检

`python scripts/check_env.py` 在装有 Python 3.12 / Node 24 / playwright-cli 0.1.23 / Chrome 的机器上返回 `status=ok`，退出码 0。
未安装 CLI 时返回 `degraded`，退出码 1，并给出安装与降级建议。

### Case G：CLI 参数契约核对

实际执行 `playwright-cli --help`（0.1.23）核对：

- 有 `open [url]`、`goto`、`eval <func> [target]`、`fill`、`click`、`list`、`state-save/load`
- 全局选项是 `--json` / `--raw`；`eval` 另外支持 `--filename`，本 Skill 选择直接读 stdout，减少临时文件与版本差异
- `open` 支持 `--browser chrome|msedge|firefox|webkit`、`--persistent`、`--headed`、`--profile`
- `install` 会初始化 workspace 并安装浏览器

`collect.mjs` 已按真实契约实现：`eval ... --json` → 解析失败回退 `--raw`；`open` 按 Chrome → Edge → 默认浏览器回退；会话复用用 `goto`。

## 2. 回归用例（评审后新增）

| 用例 | 输入 | 期望 | 实际 |
|---|---|---|---|
| 词边界 | `负责 Django 后端开发，熟悉 JavaScript` | 命中 `django`、`javascript`，不命中 `java`、`go` | 已通过 |
| 短词误命中 | 简历含 `QPS 提升 3 倍` | 不命中 `ps` | 已通过 |
| 版本号写法 | `熟悉 Python3、Vue3、MySQL8，了解 SpringBoot` | 命中 python/vue/mysql/spring | 已通过 |
| 同义词去重 | `node.js` | 只计一次 `node` | 已通过 |
| 模板简历 | `--resume assets/resume-profile.example.md` | 不给分、不生成话术、`status=needs_resume` | 已通过 |
| 列表格式姓名 | `- 姓名：张三` | 解析出 `张三` | 已通过 |
| 带地区时间 | `3天前 广东` / `2026-09-20 广东` | 解析成功，分别 `medium` / `high` | 已通过 |
| 直招帖 | `【北京】招测试开发工程师 1 名，月薪 15-20k，有意私信` | `is_recruitment=True` | 已通过 |
| 邮箱变体 | `简历发至 hr(at)example(dot)com` | 提取 `hr@example.com` | 已通过 |
| 多级域名邮箱 | `hr@example.com.cn` | 完整提取，不截断为 `.com` | 已通过 |
| 评论中的邮箱 | 正文无邮箱，评论写 `投递 xxx@yy.com` | `contacts.emails` 含该邮箱 | 已通过 |
| 邮件草稿 | 招聘帖带邮箱 + 提供简历 | 生成 `mail-<id>.md`（收件人/主题/mailto/附件命名），`messages.md` 列出投递邮箱 | 已通过 |
| 无邮箱兜底 | 招聘帖无邮箱 | 生成 `msg-<id>.txt`，提示先询问投递邮箱 | 已通过 |
| 面经含直招 | `面经 + 我是直招进去的 + 9.12投递/9.18一面/10.8 OC` | `is_recruitment=False` | 已通过 |
| 未知时间招聘帖 | 招聘帖 `published_at=null` | 进 `undated_jobs`，不进主清单 | 已通过 |
| 同分排序 | 同分、发布 3 天与 6 天（同 freshness 档） | 3 天的排前面 | 已通过 |
| 城市不匹配 | 简历期望上海，岗位 base 北京 | `warnings` 提示地点不一致，分数扣减 | 已通过 |
| 会话预算 | 同一 `--out` 连续 `run` 累计超过 30 篇 | 返回 `session_budget_exhausted` | 代码路径已实现，需真机跑满 30 篇补验 |

## 3. 未验证（环境限制，需在用户桌面环境补验）

| 项 | 原因 | 补验方法 |
|---|---|---|
| 真实浏览器打开与采集 | 本机无交互桌面会话，`playwright-cli open --headed` 挂起超时 | 在有桌面的机器执行 `collect.mjs login`，再 `run --max-notes 1 --max-comments 10` |
| 小红书页面选择器命中率 | 同上，无法加载真实页面 | 冒烟采集后检查 `cards_found`、`comments_loaded`；为 0 时按 playbook 失败分类处理 |
| `eval --json` 的返回结构 | 无浏览器会话，无法执行 eval | 首次冒烟时观察 collect.mjs 是否报 `eval_failed`；如有，用 `--raw` 输出对照调整 `unwrapPayload` |
| 私信预填 | 需要真实登录态与对方可私信 | 用 `message --url` 对一条自己的笔记测试，确认 `filled=true` 且未发送 |

## 4. 验收清单（用户环境）

- [ ] `check_env.py` 返回 ok
- [ ] `login` 返回 `logged_in=true`
- [ ] 冒烟 `run --max-notes 1` 返回 `cards_found > 0` 且 `comments > 0`
- [ ] `normalize_posts.py` 的 `dated_ratio` 大于 0.8
- [ ] `plan_outreach.py` 生成的 `messages.md` 无 `[请补充` 或 `<...>` 占位（或已由用户补全）
- [ ] `message` 只预填、不发送