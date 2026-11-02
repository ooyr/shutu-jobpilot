# 采集手册（collection-playbook）

本文件说明如何用真实浏览器从小红书采集面经与招聘帖，以及采不到时如何诚实降级。

## 1. 前置条件

| 条件 | 检查方式 | 缺失时 |
|---|---|---|
| Python 3.9+ | `python --version` | 阻塞，告知用户 |
| Node.js 18+ | `node --version` | 阻塞（`collect.mjs` 需要） |
| playwright-cli | `playwright-cli --version` | 执行 `npm install -g @playwright/cli@latest`，再 `playwright-cli install` 初始化 workspace |
| 浏览器 | 系统 Chrome / Edge | 优先复用；确实没有才 `playwright-cli install-browser` |
| 小红书登录态 | `collect.mjs login` 返回 `loggedIn` | 用户在打开的窗口扫码，一次即可 |

**禁止**用 `requests` / `urllib` / 静态 HTML 替代浏览器。小红书搜索页、正文展开与评论区都依赖 JS 渲染和登录态，静态请求拿不到目标内容，属于能力降级伪装。

## 2. 浏览器会话策略

按优先级：

1. CLI 会话内复用：`collect.mjs login` 打开浏览器后不要 `close`，后续 `search` / `note` 继续复用同一会话。
2. 登录态文件：扫码成功后可用 `playwright-cli state-save <文件>` 备份，环境重置后用 `state-load` 恢复。
3. `playwright-cli attach <name>` 可接入已在运行的 playwright 浏览器会话（需先有该会话）。

一个任务内只开一个会话、串行采集。不要并发开多个浏览器。

## 3. 采集策略

### 3.1 搜索词矩阵

每个企业生成 3-5 个搜索词，覆盖不同意图：

```text
<企业> 面经
<企业> <岗位> 面试
<企业> 实习 面经
<企业> 校招/社招 流程
<企业> 内推 / 招聘 / 在招
```

搜索词越具体，噪声越低。避免只用一个宽泛词（如"互联网 面经"）。

### 3.2 数量与节奏

| 项 | 默认 | 说明 |
|---|---|---|
| 单次会话笔记总数 | 30 | 超出后易触发风控，且报告边际收益低 |
| 每篇评论 | 40 | 只取主楼 + 高赞回复，楼中楼按需 |
| 搜索页滚动 | 4 次 | 每次滚动后等待 1.5-2.5s |
| 请求间隔 | 3-6s 随机 | 由脚本内置，不要手工并发 |
| 单关键词笔记上限 | 12 | `--max-notes` |

### 3.3 优先采哪些笔记

1. 有明确流程描述（时间线、轮次、结果）的笔记
2. 点赞/收藏较高、发布时间在窗口内的笔记
3. 标题含"已 OC / 已 offer / 凉经 / 面经合集"的笔记
4. 招聘帖：正文含岗位、地点、投递方式、HC、时间要求

跳过：纯情绪宣泄无信息、明显广告、搬运重复内容。

## 4. 字段契约

### 4.1 搜索卡片 `raw/search-<hash>.json`

```json
{
  "meta": {
    "keyword": "字节跳动 后端 面经",
    "collected_at": "2026-11-01T13:20:11+08:00",
    "source": "xiaohongshu",
    "logged_in": true,
    "scrolls": 4
  },
  "items": [
    {
      "note_id": "65f0c1a2b3c4d5e6f7a8b9c0",
      "url": "https://www.xiaohongshu.com/explore/65f0...?xsec_token=...",
      "title": "字节后端三面面经",
      "author": "某用户",
      "published_text": "3天前",
      "likes": 128,
      "cover": "https://..."
    }
  ],
  "diagnostics": {
    "cards_found": 12,
    "blocked": null,
    "login_wall": false
  }
}
```

`url` 必须带 `xsec_token`，否则详情页打不开。脚本从卡片 `href` 原样保留。

### 4.2 笔记详情 `raw/note-<note_id>.json`

```json
{
  "note_id": "65f0...",
  "url": "https://www.xiaohongshu.com/explore/65f0...?xsec_token=...",
  "title": "字节后端三面面经",
  "desc": "9.12 投递……10.8 OC",
  "author": "某用户",
  "published_text": "3天前",
  "likes": 128,
  "collects": 45,
  "comments_count": 23,
  "comments": [
    {
      "cid": "abc123",
      "author": "路人甲",
      "content": "二面问了手写 LRU",
      "likes": 12,
      "time_text": "2天前",
      "is_reply": false
    }
  ],
  "diagnostics": {
    "expanded": true,
    "comments_loaded": true,
    "comments_partial": false,
    "blocked": null
  }
}
```

字段缺失时写 `null`，不要用 `0` 或空字符串冒充"确实为零"。

## 5. 失败分类与处理

| 分类 | 表现 | 处理 |
|---|---|---|
| CLI 缺失 | `playwright-cli` 非零 / 找不到命令 | 安装 CLI，不使用其他抓取方式 |
| 登录缺失 | 页面出现登录弹窗 / `login_wall=true` | 停止，请用户扫码，不重复尝试 |
| 验证码 / 风控 | 脚本返回 `status=error`、`reason=blocked`、`diagnostics.blocked=captcha` | 立即停止本次采集，保留已采数据，建议降低频率或隔日再采 |
| 频率限制 | 连续空结果 / 429 | 停止 10 分钟后最多重试 1 次 |
| 选择器漂移 | `cards_found=0` 但页面有内容 | 抽样截图，报告"选择器失效"，改用手动回填 |
| 懒加载 | 评论数远小于页面显示 | 增加滚动次数（最多 10 次），仍不足标 `comments_partial` |
| 网络问题 | DNS / TLS / 超时 | 检查代理与 VPN，分类后重试 1 次 |

选择器失效时**不要**反复改正则硬试。抽取脚本已使用"语义锚点 + 结构启发式"（`a[href*="/explore/"]`、文本特征），漂移概率较低；仍失败就进入回填。

## 6. 手动回填（最后手段）

用户无法登录、或环境被风控时，可以让用户手动复制内容，仍能得到大部分分析价值：

```bash
# 用户把笔记正文粘贴进 .jobpilot/manual/notes.md，按分隔线分块
python "${SKILL_DIR}/scripts/normalize_posts.py" \
  --raw .jobpilot/raw \
  --manual .jobpilot/manual/notes.md \
  --out .jobpilot/normalized.json
```

`notes.md` 格式：

```markdown
## 字节跳动 后端 三面
url: https://www.xiaohongshu.com/explore/xxxx
author: 某用户
published: 2026-09-20
likes: 128

9.12 投递，9.18 一面……

### 评论
- 路人甲: 二面问了手写 LRU
- 路人乙: 我 10 月投的还没消息
```

回填条目标 `source=manual`，报告里必须注明"部分数据来自用户手动提供，时间与互动数未经平台校验"。

## 7. 目录与清理

```text
.jobpilot/
├── raw/            # 原始采集，保留用于复核
├── manual/         # 手动回填
├── normalized.json
├── outreach/
└── report.md
```

`.jobpilot/` 属于用户工作目录，不要写进 Skill 安装目录。交付前不要在 Skill 里残留采集数据。