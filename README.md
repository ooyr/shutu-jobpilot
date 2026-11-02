# 薯途 JobPilot

**小红书求职情报与投递助手 · 从面经到投递，让求职信息变成行动。**

「薯」取自小红书，「途」代表求职之路。薯途 JobPilot 把散落的小红书面经、评论区和在招帖，整理成一份能直接照着准备的求职作战包。

本项目是 **2026 华为开发者大赛·小红书 Skill 创新赛** 的参赛作品。
[查看比赛页面](https://developer.huaweicloud.com/competition/information/1300000350/html3)

## 它解决什么问题

找工作时，搜到信息只是起点，还需要回答三个问题：

1. 这家公司、这个岗位最近怎么面？面试轮次、间隔和重点是什么？
2. 我可能被问到什么？有哪些硬门槛和需要核实的风险？
3. 哪些岗位正在招聘？与我的简历匹配在哪里，接下来怎么投递？

这个 Skill 用真实浏览器采集公开可见的小红书笔记与评论，把相对时间归一化，按企业聚合出面试时间线，识别在招岗位，并结合简历生成可解释的匹配结果和邮件草稿。

## 六个环节

| 环节 | 产出 |
|---|---|
| 采集 | 笔记正文、作者、发布时间、点赞、评论区 |
| 归一化 | 绝对时间、去重、招聘 / 面经 / 噪声信号 |
| 聚合 | `dossier.json`：阶段统计、流程证据原文、评论高互动、数据缺口 |
| 分析 | 流程时间线、高频问题聚类、评论区洞察、可信度分级 |
| 匹配 | 岗位清单、匹配分、命中与缺失技能、城市匹配 |
| 投递 | 邮件收件人、主题、正文、附件命名建议和 `mailto` 链接 |

脚本负责确定性处理；问题聚类、评论解读和最终报告由 Agent 依据采集证据完成。匹配分是规则计算的参考值，不代表录用概率。

## 环境与安装

- Python 3.9+，数据处理脚本只使用标准库。
- Node.js 18+。
- `@playwright/cli` 和系统 Chrome 或 Edge。
- 用户自己的小红书登录态，以及支持 `SKILL.md` 的 Agent 环境。

```bash
git clone https://github.com/ooyr/shutu-jobpilot.git
cd shutu-jobpilot
npm install -g @playwright/cli@latest
playwright-cli install
```

本仓库是 Agent Skill。按所用 Agent 的安装方式添加整个目录，以 `SKILL.md` 为入口；也可在仓库根目录直接执行下方脚本。用户数据默认写入当前工作目录的 `.jobpilot/`。

## 快速开始

以下命令在仓库根目录执行。先把真实简历整理成 `resume.md`，可参考 `assets/resume-profile.example.md`；模板中的占位内容需要替换为自己的信息。

```bash
# 1. 环境自检
python scripts/check_env.py

# 2. 登录小红书，用户扫码后复用浏览器会话
node scripts/collect.mjs login

# 3. 先采集一篇，确认当前页面结构可用
node scripts/collect.mjs run --keyword "字节跳动 后端 面经" --out .jobpilot/raw --max-notes 1 --max-comments 10

# 4. 归一化、企业聚合和简历匹配
python scripts/normalize_posts.py --raw .jobpilot/raw --out .jobpilot/normalized.json
python scripts/build_timeline.py --normalized .jobpilot/normalized.json --companies "字节跳动|字节" --out-dir .jobpilot
python scripts/plan_outreach.py --normalized .jobpilot/normalized.json --resume resume.md --out .jobpilot/outreach
```

只想查看岗位时，可以省略 `--resume resume.md`，此时不生成投递话术。完整的输入要求、工作流与报告规范见 [SKILL.md](SKILL.md)。

## 无需登录的离线演示

仓库内的 fixtures 是明确标注的虚构测试数据，不代表真实企业、岗位或候选人。可以先用它们检查数据处理链路：

```bash
python scripts/normalize_posts.py --raw evals/fixtures/raw --out .jobpilot/demo/normalized.json --now 2026-11-01T10:00:00
python scripts/build_timeline.py --normalized .jobpilot/demo/normalized.json --companies "示例科技|示例" --role 后端 --out-dir .jobpilot/demo
python scripts/plan_outreach.py --normalized .jobpilot/demo/normalized.json --resume evals/fixtures/resume-test.md --out .jobpilot/demo/outreach --days 60 --now 2026-11-01T10:00:00
```

查看 `.jobpilot/demo/report-draft.md` 和 `.jobpilot/demo/outreach/messages.md`，了解报告骨架和投递清单的结构。验证用例与浏览器采集待验证项见 [evals/cases.md](evals/cases.md)。

## 边界、隐私与限制

- 不绕过登录、验证码或风控，不破解接口签名。
- 不保证招聘帖真实，所有岗位均标注“待核实”。
- 不做批量自动投递：邮件草稿由用户核对后发送；无邮箱时只预填站内私信，不自动发送。
- 不内置真实企业、岗位、薪资或面试题数据；结论来自当次采集和用户输入。
- 图片中的邮箱需要人工查看。
- 真实浏览器采集、页面选择器命中率和私信预填仍需在用户桌面环境补验。首次使用先运行一篇冒烟采集，确认 `cards_found > 0`。
- 简历、采集结果和浏览器会话属于本地数据，不应提交到公开仓库。`.gitignore` 已排除常用输出目录、简历文件和登录态文件。

## 目录

```text
SKILL.md                          Skill 入口与完整工作流
references/                       采集手册、分析方法、投递策略
scripts/                          采集、抽取、归一化、聚合、匹配
assets/                           报告模板、简历画像模板
evals/                            验证用例与虚构测试数据
```

## 许可证

[MIT License](LICENSE)。
