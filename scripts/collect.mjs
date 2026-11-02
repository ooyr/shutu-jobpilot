#!/usr/bin/env node
/**
 * collect.mjs — 小红书采集编排（通过 playwright-cli 驱动真实浏览器）
 *
 * 子命令：
 *   login                                   打开持久化浏览器并检查登录态
 *   search  --keyword <词> --out <目录>       采集搜索结果卡片
 *   note    --url <链接> --out <目录>          采集单篇笔记正文与评论
 *   run     --keyword <词> --out <目录>       search + 打开前 N 篇
 *   message --url <链接> --text-file <文件>    打开对话并预填文本（不点击发送）
 *
 * 通用参数：
 *   --max-scroll <n>      搜索页滚动次数，默认 4
 *   --max-notes <n>       run 模式最多打开笔记数，默认 8
 *   --max-comments <n>    每篇最多评论数，默认 40
 *   --delay-min / --delay-max  请求间隔毫秒，默认 3000 / 6000
 *   --timeout <秒>        login 等待扫码时间，默认 180
 *
 * 输出：stdout 单个 JSON；进度写 stderr。
 * 退出码：0 成功｜2 参数错误｜3 环境或 CLI 错误｜4 被登录墙/验证码阻断｜5 采集失败
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_DELAY_MIN = 3000;
const DEFAULT_DELAY_MAX = 6000;
const HARD_NOTE_LIMIT = 30;
const MAX_EXPRESSION_BYTES = 30000;

function log(message) {
  process.stderr.write(String(message) + "\n");
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

function quote(value) {
  const text = String(value).replace(/"/g, "'");
  return '"' + text + '"';
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith("--")) {
      const key = token.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        out[key] = true;
      } else {
        out[key] = next;
        i += 1;
      }
    } else {
      out._.push(token);
    }
  }
  return out;
}

function toInt(value, fallback) {
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function emit(payload, code) {
  process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
  process.exit(code);
}

function fail(code, reason, detail, extra) {
  emit(
    Object.assign({ status: "error", reason: reason, detail: detail || null }, extra || {}),
    code
  );
}

const KEYWORD_PATTERN = /^[\u4e00-\u9fa5a-zA-Z0-9 \-_/·,.+#()[\]]+$/;

let cliCommand = null;

/** 解析可用的 playwright-cli 命令；PATH 缺失时回退到 npm 全局目录。 */
function getCliCommand() {
  if (cliCommand) return cliCommand;
  if (process.env.PLAYWRIGHT_CLI_BIN) {
    cliCommand = quote(process.env.PLAYWRIGHT_CLI_BIN);
    return cliCommand;
  }

  const probe = spawnSync("playwright-cli --version", {
    shell: true,
    encoding: "utf8",
    timeout: 20000,
    windowsHide: true
  });
  if (probe.status === 0) {
    cliCommand = "playwright-cli";
    return cliCommand;
  }

  const prefix = spawnSync("npm prefix -g", {
    shell: true,
    encoding: "utf8",
    timeout: 30000,
    windowsHide: true
  });
  if (prefix.status === 0 && prefix.stdout) {
    const dir = prefix.stdout.trim();
    const bin = join(dir, process.platform === "win32" ? "playwright-cli.cmd" : "playwright-cli");
    if (existsSync(bin)) {
      cliCommand = quote(bin);
      return cliCommand;
    }
  }
  return null;
}

function assertKeyword(keyword) {
  if (!keyword || typeof keyword !== "string") {
    fail(2, "missing_keyword", "search/run 需要 --keyword");
  }
  if (keyword.length > 60) {
    fail(2, "keyword_too_long", "关键词不要超过 60 字");
  }
  if (!KEYWORD_PATTERN.test(keyword)) {
    fail(
      2,
      "keyword_invalid",
      "关键词只允许中英文、数字、空格和常见标点 - _ / · , . + # ( ) [ ]"
    );
  }
}

function assertXhsUrl(url) {
  if (!url || typeof url !== "string") {
    fail(2, "missing_url", "需要 --url");
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch (err) {
    fail(2, "invalid_url", "URL 无法解析: " + url);
  }
  const host = parsed.hostname.toLowerCase();
  if (!host.endsWith("xiaohongshu.com")) {
    fail(2, "invalid_url", "只接受 xiaohongshu.com 链接，收到: " + host);
  }
  return parsed.href;
}

function runCli(args, timeoutMs) {
  const cli = getCliCommand();
  if (!cli) {
    return {
      code: -1,
      out: "",
      err: "playwright-cli 不在 PATH，也未在 npm 全局目录中找到",
      failedToStart: true
    };
  }
  const line = [cli].concat(args.map(quote)).join(" ");
  const result = spawnSync(line, {
    shell: true,
    encoding: "utf8",
    timeout: timeoutMs || 90000,
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true
  });
  if (result.error) {
    return {
      code: -1,
      out: "",
      err: String(result.error.message || result.error),
      failedToStart: true
    };
  }
  return {
    code: typeof result.status === "number" ? result.status : -1,
    out: (result.stdout || "").trim(),
    err: (result.stderr || "").trim()
  };
}

let cliReady = false;

function ensureCli() {
  if (cliReady) return;
  const result = runCli(["--help"], 30000);
  if (result.failedToStart || result.code !== 0) {
    fail(3, "cli_missing", "playwright-cli 不可用。先执行: npm install -g @playwright/cli@latest", {
      stderr: result.err.slice(0, 500)
    });
  }
  const text = (result.out + " " + result.err).toLowerCase();
  const missing = ["open", "eval", "close"].filter((cmd) => text.indexOf(cmd) < 0);
  if (missing.length > 0) {
    fail(3, "cli_incompatible", "playwright-cli 缺少子命令: " + missing.join(", "));
  }
  if (text.indexOf("--json") < 0) {
    fail(3, "cli_incompatible", "playwright-cli 缺少 --json 输出选项，请升级到最新版");
  }
  cliReady = true;
}

/** 只检查 CLI 会话能力；浏览器下载交给用户按 check_env 提示处理。 */
function ensureWorkspace() {
  const listed = runCli(["list"], 30000);
  if (listed.code === 0) return true;
  log("playwright-cli list 失败，请先执行 playwright-cli install 初始化 workspace");
  return false;
}

/**
 * 把任意 JS 源码编码为可安全通过命令行的 eval 表达式。
 * base64 只含 [A-Za-z0-9+/=]，不触发 shell 元字符；中文经 TextDecoder 还原。
 */
function encodeExpression(source) {
  const base64 = Buffer.from(String(source), "utf8").toString("base64");
  if (base64.length > MAX_EXPRESSION_BYTES) {
    throw new Error("脚本过大，压缩后 " + base64.length + " 字符");
  }
  return (
    "(function(){const u=Uint8Array.from(atob('" +
    base64 +
    "'),function(c){return c.charCodeAt(0);});return eval(new TextDecoder().decode(u));})()"
  );
}

function parseLooseJson(text) {
  if (!text) return null;
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed);
  } catch (err) {
    // 继续尝试截取
  }
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(trimmed.slice(start, end + 1));
    } catch (err) {
      return null;
    }
  }
  return null;
}

function unwrapPayload(payload) {
  if (payload === null || payload === undefined) return payload;
  if (typeof payload === "string") {
    const parsed = parseLooseJson(payload);
    return parsed === null ? payload : parsed;
  }
  if (typeof payload !== "object" || Array.isArray(payload)) return payload;
  if (Object.prototype.hasOwnProperty.call(payload, "result")) return payload.result;
  if (Array.isArray(payload.content)) {
    const text = payload.content.map((item) => (item && item.text) || "").join("");
    const parsed = parseLooseJson(text);
    return parsed === null ? text : parsed;
  }
  if (Object.prototype.hasOwnProperty.call(payload, "output")) return payload.output;
  return payload;
}

/**
 * playwright-cli 0.1.x 提供 `eval <func> [target]`，全局选项 --json / --raw。
 * 这里直接读 stdout，避免依赖 --filename 在不同版本上的差异。
 */
function evalRaw(source, timeoutMs) {
  const expression = encodeExpression(source);

  const jsonResult = runCli(["eval", expression, "--json"], timeoutMs || 60000);
  if (jsonResult.code === 0 && jsonResult.out) {
    const payload = parseLooseJson(jsonResult.out);
    if (payload !== null) return { ok: true, data: unwrapPayload(payload) };
  }

  const rawResult = runCli(["eval", expression, "--raw"], timeoutMs || 60000);
  if (rawResult.code === 0 && rawResult.out) {
    const parsed = parseLooseJson(rawResult.out);
    return { ok: true, data: parsed === null ? rawResult.out : unwrapPayload(parsed) };
  }

  return {
    ok: false,
    reason: "eval_failed",
    detail: (
      jsonResult.err ||
      rawResult.err ||
      jsonResult.out ||
      rawResult.out ||
      "eval 未返回可解析结果"
    ).slice(0, 600),
    exit: rawResult.code
  };
}

function evalFile(name, timeoutMs) {
  const file = join(SCRIPT_DIR, name);
  if (!existsSync(file)) {
    return { ok: false, reason: "missing_script", detail: file };
  }
  return evalRaw(readFileSync(file, "utf8"), timeoutMs);
}

let hasSession = false;

/**
 * 优先复用已有会话（goto）；没有会话时按 系统 Chrome → Edge → 默认浏览器 打开。
 * 使用 --headed 是因为小红书扫码登录需要可见窗口。
 */
function openOrGoto(url) {
  if (hasSession) {
    const reuse = runCli(["goto", url], 60000);
    if (reuse.code === 0) return { ok: true, how: "goto-reuse" };
    log("复用会话失败，重新打开浏览器: " + reuse.err.slice(0, 200));
    hasSession = false;
  }

  const variants = [
    ["open", url, "--browser", "chrome", "--persistent", "--headed"],
    ["open", url, "--browser", "msedge", "--persistent", "--headed"],
    ["open", url, "--persistent"],
    ["open", url]
  ];

  let lastError = "";
  for (let i = 0; i < variants.length; i += 1) {
    const attempt = runCli(variants[i], 90000);
    if (attempt.code === 0) {
      hasSession = true;
      return { ok: true, how: variants[i].join(" ") };
    }
    lastError = attempt.err || attempt.out || lastError;
    log("打开浏览器失败（" + variants[i].slice(2).join(" ") + "）: " + lastError.slice(0, 200));
    if (attempt.failedToStart) break;
  }

  return { ok: false, detail: lastError.slice(0, 600) };
}

function currentUrl() {
  const result = evalRaw("location.href", 20000);
  return result.ok && typeof result.data === "string" ? result.data : null;
}

const SCROLL_BOTTOM =
  "(() => { window.scrollTo(0, document.body.scrollHeight); return { y: window.scrollY }; })()";

const SCROLL_COMMENTS =
  "(() => { const nodes = Array.from(document.querySelectorAll('[class*=comment], .comments-container, [class*=note-scroller]')); let target = null; for (let i = 0; i < nodes.length; i += 1) { const n = nodes[i]; if (n.scrollHeight - n.clientHeight > 80) target = n; } if (target) { target.scrollTop = target.scrollHeight; return { scrolled: true }; } window.scrollTo(0, document.body.scrollHeight); return { scrolled: false }; })()";

const CLICK_EXPAND =
  "(() => { const nodes = Array.from(document.querySelectorAll('span, [class*=expand], [class*=more]')); let clicked = 0; for (let i = 0; i < nodes.length; i += 1) { const t = (nodes[i].textContent || '').trim(); if (t === '展开' || t === '全文' || t === '展开全文' || t === '更多') { nodes[i].click(); clicked += 1; if (clicked >= 2) break; } } return { clicked: clicked }; })()";

const LOGIN_STATE =
  "(() => { const body = document.body ? document.body.innerText : ''; const wall = /扫码登录|请先登录|手机号登录|登录后查看/.test(body); const avatar = !!document.querySelector('[class*=avatar], [class*=user-info]'); const loginButton = !!document.querySelector('[class*=login-btn], [class*=loginBtn], button[class*=login]'); return { logged_in: avatar && !wall && !loginButton && body.length > 200, login_wall: wall, has_avatar: avatar, has_login_button: loginButton, confidence: avatar && !wall && !loginButton ? 'medium' : 'low', url: location.href }; })()";

const CLICK_MESSAGE =
  "(() => { const nodes = Array.from(document.querySelectorAll('button, a, [role=button]')); let hit = false; for (let i = 0; i < nodes.length; i += 1) { const t = (nodes[i].textContent || '').trim(); if (t.indexOf('私信') >= 0 || t.indexOf('发消息') >= 0) { nodes[i].click(); hit = true; break; } } return { clicked: hit }; })()";

function fillExpression(text) {
  const base64 = Buffer.from(text, "utf8").toString("base64");
  return (
    "(() => { const bytes = Uint8Array.from(atob('" +
    base64 +
    "'), (c) => c.charCodeAt(0)); const s = new TextDecoder().decode(bytes); const el = document.querySelector('textarea, [contenteditable=true]'); if (!el) return { filled: false, reason: 'no-input' }; el.focus(); if (el.tagName === 'TEXTAREA') { el.value = s; } else { el.textContent = s; } el.dispatchEvent(new Event('input', { bubbles: true })); return { filled: true, tag: el.tagName }; })()"
  );
}

function loginState() {
  const result = evalRaw(LOGIN_STATE, 30000);
  if (!result.ok) {
    return { logged_in: false, login_wall: true, error: result.detail };
  }
  return result.data;
}

function mergeByKey(existing, incoming, keyFn) {
  const map = new Map();
  for (let i = 0; i < existing.length; i += 1) map.set(keyFn(existing[i]), existing[i]);
  let added = 0;
  for (let i = 0; i < incoming.length; i += 1) {
    const key = keyFn(incoming[i]);
    if (!key) continue;
    if (!map.has(key)) {
      map.set(key, incoming[i]);
      added += 1;
    }
  }
  return { items: Array.from(map.values()), added: added };
}

function ensureDir(dir) {
  mkdirSync(dir, { recursive: true });
}

function writeJson(file, data) {
  ensureDir(dirname(file));
  writeFileSync(file, JSON.stringify(data, null, 2), "utf8");
}

function keywordHash(keyword) {
  return createHash("sha1").update(keyword).digest("hex").slice(0, 8);
}

/** 会话级笔记预算：跨多次 run 调用累计，避免 5 企业 × 4 关键词突破 30 篇。 */
function readSessionCount(outDir) {
  const file = join(outDir, ".session-notes.json");
  if (!existsSync(file)) return 0;
  try {
    const data = JSON.parse(readFileSync(file, "utf8"));
    return Number(data.count) || 0;
  } catch (err) {
    return 0;
  }
}

function writeSessionCount(outDir, count) {
  writeJson(join(outDir, ".session-notes.json"), {
    count: count,
    updated_at: new Date().toISOString()
  });
}

async function doLogin(opts) {
  ensureCli();
  if (!ensureWorkspace()) {
    fail(3, "workspace_not_ready", "执行 playwright-cli install 失败，请手动执行后重试");
  }
  const timeoutSec = toInt(opts.timeout, 180);
  const opened = openOrGoto("https://www.xiaohongshu.com/explore");
  if (!opened.ok) fail(4, "open_failed", opened.detail);
  await sleep(3500);

  let state = loginState();
  const deadline = Date.now() + timeoutSec * 1000;
  while (!state.logged_in && Date.now() < deadline) {
    log("等待扫码登录，剩余 " + Math.max(0, Math.round((deadline - Date.now()) / 1000)) + " 秒");
    await sleep(6000);
    state = loginState();
  }

  emit(
    {
      status: state.logged_in ? "ok" : "blocked",
      logged_in: Boolean(state.logged_in),
      login_wall: Boolean(state.login_wall),
      confidence: state.confidence || "low",
      url: state.url || currentUrl(),
      hint: state.logged_in
        ? "登录态可用，可以开始 search/run"
        : "未检测到登录态。请在已打开的窗口扫码登录后重新执行 login，或改用 --manual 回填"
    },
    state.logged_in ? 0 : 4
  );
}

async function collectSearch(keyword, outDir, maxScroll) {
  const url = "https://www.xiaohongshu.com/search_result?keyword=" + encodeURIComponent(keyword);
  log("打开搜索页: " + keyword);
  const opened = openOrGoto(url);
  if (!opened.ok) return { ok: false, reason: "open_failed", detail: opened.detail };
  await sleep(3200);

  let items = [];
  let diagnostics = null;

  for (let round = 0; round <= maxScroll; round += 1) {
    const result = evalFile("extract-search.js");
    if (!result.ok) return { ok: false, reason: result.reason, detail: result.detail };

    diagnostics = result.data.diagnostics || diagnostics;
    const merged = mergeByKey(items, result.data.items || [], (item) => item.note_id);
    items = merged.items;

    if (diagnostics.blocked) {
      return { ok: false, reason: "blocked", detail: "captcha", items: items, diagnostics: diagnostics };
    }
    if (diagnostics.login_wall) {
      return {
        ok: false,
        reason: "login_required",
        detail: "login wall detected",
        items: items,
        diagnostics: diagnostics
      };
    }
    if (merged.added === 0 && round > 0) break;

    if (round < maxScroll) {
      evalRaw(SCROLL_BOTTOM, 30000);
      await sleep(1500 + Math.floor(Math.random() * 1200));
    }
  }

  const file = join(outDir, "search-" + keywordHash(keyword) + ".json");
  writeJson(file, {
    meta: {
      keyword: keyword,
      collected_at: new Date().toISOString(),
      source: "xiaohongshu",
      logged_in: true,
      scrolls: maxScroll
    },
    items: items,
    diagnostics: diagnostics
  });

  return { ok: true, file: file, count: items.length, diagnostics: diagnostics };
}

async function collectNote(url, outDir, maxComments, maxScroll) {
  log("打开笔记: " + url);
  const opened = openOrGoto(url);
  if (!opened.ok) return { ok: false, reason: "open_failed", detail: opened.detail };
  await sleep(2800);
  evalRaw(CLICK_EXPAND, 20000);
  await sleep(700);

  const first = evalFile("extract-note.js");
  if (!first.ok) return { ok: false, reason: first.reason, detail: first.detail };

  const note = first.data;
  let comments = (note.comments || []).slice(0, maxComments);
  let partial = Boolean(note.diagnostics && note.diagnostics.comments_partial);
  let blockedReason = (note.diagnostics && note.diagnostics.blocked) || null;

  for (let round = 0; round < maxScroll && comments.length < maxComments; round += 1) {
    evalRaw(SCROLL_COMMENTS, 25000);
    await sleep(1200 + Math.floor(Math.random() * 900));

    const next = evalFile("extract-note.js");
    if (!next.ok) break;
    if (next.data.diagnostics && next.data.diagnostics.blocked) {
      partial = true;
      blockedReason = next.data.diagnostics.blocked;
      break;
    }
    const merged = mergeByKey(
      comments,
      next.data.comments || [],
      (c) => c.cid || c.author + "|" + String(c.content).slice(0, 40)
    );
    comments = merged.items.slice(0, maxComments);
    if (merged.added === 0) break;
  }

  note.comments = comments;
  note.comments_count = Math.max(Number(note.comments_count) || 0, comments.length);
  note.diagnostics = Object.assign({}, note.diagnostics, {
    comments_loaded: comments.length > 0,
    comments_partial: partial
  });

  const noteId = note.note_id || "unknown";
  const file = join(outDir, "note-" + noteId + ".json");
  writeJson(file, note);

  return {
    ok: true,
    file: file,
    note_id: noteId,
    comments: comments.length,
    diagnostics: note.diagnostics,
    blocked: blockedReason
  };
}

async function doSearch(opts) {
  ensureCli();
  if (!ensureWorkspace()) {
    fail(3, "workspace_not_ready", "执行 playwright-cli install 失败，请手动执行后重试");
  }
  const keyword = opts.keyword;
  assertKeyword(keyword);
  const outDir = resolve(String(opts.out || ".jobpilot/raw"));
  ensureDir(outDir);
  const maxScroll = Math.min(toInt(opts["max-scroll"], 4), 12);

  const result = await collectSearch(keyword, outDir, maxScroll);
  if (!result.ok) {
    fail(result.reason === "blocked" || result.reason === "login_required" ? 4 : 5, result.reason, result.detail, {
      collected: (result.items || []).length,
      diagnostics: result.diagnostics || null
    });
  }
  emit(
    {
      status: "ok",
      mode: "search",
      keyword: keyword,
      out: result.file,
      count: result.count,
      diagnostics: result.diagnostics
    },
    0
  );
}

async function doNote(opts) {
  ensureCli();
  if (!ensureWorkspace()) {
    fail(3, "workspace_not_ready", "执行 playwright-cli install 失败，请手动执行后重试");
  }
  const url = assertXhsUrl(opts.url);
  const outDir = resolve(String(opts.out || ".jobpilot/raw"));
  ensureDir(outDir);
  const maxComments = Math.min(toInt(opts["max-comments"], 40), 120);
  const maxScroll = Math.min(toInt(opts["max-scroll"], 6), 10);

  const result = await collectNote(url, outDir, maxComments, maxScroll);
  if (!result.ok) fail(5, result.reason, result.detail);
  emit(
    {
      status: "ok",
      mode: "note",
      out: result.file,
      note_id: result.note_id,
      comments: result.comments,
      diagnostics: result.diagnostics
    },
    0
  );
}

async function doRun(opts) {
  ensureCli();
  if (!ensureWorkspace()) {
    fail(3, "workspace_not_ready", "执行 playwright-cli install 失败，请手动执行后重试");
  }
  const keyword = opts.keyword;
  assertKeyword(keyword);
  const outDir = resolve(String(opts.out || ".jobpilot/raw"));
  ensureDir(outDir);
  const maxScroll = Math.min(toInt(opts["max-scroll"], 4), 12);
  const usedNotes = readSessionCount(outDir);
  const remaining = Math.max(0, HARD_NOTE_LIMIT - usedNotes);
  if (remaining === 0) {
    fail(
      4,
      "session_budget_exhausted",
      "本次会话已采集 " + usedNotes + " 篇，达到 30 篇上限；建议休息一段时间或换会话再采"
    );
  }
  const maxNotes = Math.min(toInt(opts["max-notes"], 8), remaining);
  const maxComments = Math.min(toInt(opts["max-comments"], 40), 120);
  const delayMin = toInt(opts["delay-min"], DEFAULT_DELAY_MIN);
  const delayMax = Math.max(delayMin, toInt(opts["delay-max"], DEFAULT_DELAY_MAX));

  const search = await collectSearch(keyword, outDir, maxScroll);
  if (!search.ok) {
    fail(
      search.reason === "blocked" || search.reason === "login_required" ? 4 : 5,
      search.reason,
      search.detail,
      { collected: (search.items || []).length }
    );
  }

  const keywordsFile = join(outDir, "search-" + keywordHash(keyword) + ".json");
  const stored = JSON.parse(readFileSync(keywordsFile, "utf8"));
  const targets = (stored.items || []).slice(0, maxNotes);

  const results = [];
  let blocked = null;

  for (let i = 0; i < targets.length; i += 1) {
    const item = targets[i];
    if (!item.url) continue;
    const result = await collectNote(item.url, outDir, maxComments, 8);
    if (!result.ok) {
      results.push({ note_id: item.note_id, status: "failed", reason: result.reason });
      blocked = result.reason;
      break;
    }
    results.push({
      note_id: result.note_id,
      status: "ok",
      comments: result.comments,
      out: result.file
    });
    if (result.blocked) {
      blocked = result.blocked;
      log("检测到 " + result.blocked + "，立即停止本次批量采集");
      break;
    }
    if (i < targets.length - 1) {
      const wait = delayMin + Math.floor(Math.random() * (delayMax - delayMin));
      log("间隔 " + wait + " 毫秒");
      await sleep(wait);
    }
  }

  const collectedCount = results.filter((r) => r.status === "ok").length;
  writeSessionCount(outDir, usedNotes + collectedCount);

  emit(
    {
      status: blocked ? "partial" : "ok",
      mode: "run",
      keyword: keyword,
      search_out: keywordsFile,
      found: targets.length,
      collected: collectedCount,
      session_notes: usedNotes + collectedCount,
      blocked: blocked,
      notes: results
    },
    blocked ? 4 : 0
  );
}

async function doMessage(opts) {
  ensureCli();
  if (!ensureWorkspace()) {
    fail(3, "workspace_not_ready", "执行 playwright-cli install 失败，请手动执行后重试");
  }
  const url = assertXhsUrl(opts.url);
  const textFile = resolve(String(opts["text-file"] || ""));
  if (!existsSync(textFile)) fail(2, "missing_text_file", "找不到话术文件: " + textFile);

  const raw = readFileSync(textFile, "utf8").replace(/\r\n/g, "\n").trim();
  if (!raw) fail(2, "empty_text", "话术文件为空");
  if (raw.length > 600) fail(2, "text_too_long", "私信文本超过 600 字，请精简");

  const opened = openOrGoto(url);
  if (!opened.ok) fail(4, "open_failed", opened.detail);
  await sleep(2800);

  const click = evalRaw(CLICK_MESSAGE, 25000);
  await sleep(2200);

  const filled = evalRaw(fillExpression(raw), 25000);
  if (!filled.ok || !filled.data || !filled.data.filled) {
    emit(
      {
        status: "blocked",
        filled: false,
        clicked_message_button: Boolean(click.ok && click.data && click.data.clicked),
        reason: filled.ok ? filled.data.reason : "eval_failed",
        hint:
          "找不到私信输入框（对方可能关闭私信或页面改版）。请从 messages.md 复制文本，手动发送。"
      },
      4
    );
  }

  emit(
    {
      status: "ok",
      filled: true,
      clicked_message_button: Boolean(click.ok && click.data && click.data.clicked),
      url: currentUrl(),
      hint: "文本已填入输入框，未发送。请核对内容、附上简历后手动点击发送。"
    },
    0
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  if (!command) {
    fail(2, "missing_command", "用法: node collect.mjs <login|search|note|run|message> [参数]");
  }
  if (command === "login") return doLogin(args);
  if (command === "search") return doSearch(args);
  if (command === "note") return doNote(args);
  if (command === "run") return doRun(args);
  if (command === "message") return doMessage(args);
  fail(2, "unknown_command", "未知子命令: " + command);
}

main().catch((err) => {
  fail(5, "unexpected_error", String(err && err.stack ? err.stack : err));
});