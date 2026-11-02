#!/usr/bin/env python3
"""check_env.py — 小红书求职情报 Skill 的环境自检

用法:
    python check_env.py [--install] [--json]

输出:
    stdout: 单个 JSON 对象
    stderr: 人读提示

退出码:
    0 = ok        核心路径可用
    1 = degraded  可运行但有降级（如缺 playwright-cli，只能手动回填）
    2 = blocked   缺少 Python/Node 等硬依赖

--install 会在缺少 playwright-cli 时执行 npm install -g @playwright/cli@latest。
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 9)
MIN_NODE = 18
CLI_PACKAGE = "@playwright/cli@latest"


def _run(cmd, use_shell=False):
    """运行命令并返回 (ok, text)。不抛异常。"""
    try:
        proc = subprocess.run(
            cmd,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode == 0, (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _find_cli():
    """优先 PATH，其次 npm 全局目录（Windows 上 .cmd 常不在 PATH）。"""
    exe = shutil.which("playwright-cli")
    if exe:
        return exe
    npm = shutil.which("npm")
    if not npm:
        return None
    ok, prefix = _run([npm, "prefix", "-g"], use_shell=(os.name == "nt"))
    if not ok or not prefix:
        return None
    name = "playwright-cli.cmd" if os.name == "nt" else "playwright-cli"
    candidate = Path(prefix.strip()) / name
    return str(candidate) if candidate.is_file() else None


def _parse_version(text, prefix=""):
    raw = text.strip()
    if prefix and raw.lower().startswith(prefix):
        raw = raw[len(prefix):]
    raw = raw.lstrip("vV")
    parts = []
    for chunk in raw.split(".")[:3]:
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def _browser_candidates():
    """返回 [(name, [path...])]，只列出常见安装位置，不保证存在。"""
    system = platform.system()
    if system == "Windows":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rel = {
            "chrome": [
                r"Google\Chrome\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
            ],
            "msedge": [r"Microsoft\Edge\Application\msedge.exe"],
        }
        out = []
        for name, subs in rel.items():
            paths = []
            for root in roots:
                if not root:
                    continue
                for sub in subs:
                    paths.append(os.path.join(root, sub))
            out.append((name, paths))
        return out
    if system == "Darwin":
        return [
            ("chrome", ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]),
            ("msedge", ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]),
        ]
    return [
        ("chrome", ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]),
        ("chromium", ["/usr/bin/chromium", "/usr/bin/chromium-browser"]),
        ("msedge", ["/usr/bin/microsoft-edge"]),
    ]


def detect_browsers():
    found = []
    for name, paths in _browser_candidates():
        for candidate in paths:
            if candidate and Path(candidate).is_file():
                found.append({"name": name, "path": candidate})
                break
    for name in ("chrome", "msedge", "chromium"):
        exe = shutil.which(name)
        if exe and not any(b["name"] == name for b in found):
            found.append({"name": name, "path": exe})
    return found


def build_report(do_install):
    checks = []
    actions = []

    def add(name, status, detail, fix=None):
        item = {"name": name, "status": status, "detail": detail}
        if fix:
            item["fix"] = fix
        checks.append(item)

    py_ver = platform.python_version()
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    add(
        "python",
        "ok" if py_ok else "missing",
        py_ver,
        None if py_ok else "需要 Python %d.%d+" % MIN_PYTHON,
    )
    if not py_ok:
        actions.append("升级 Python 到 %d.%d+" % MIN_PYTHON)

    node_exe = shutil.which("node")
    if node_exe:
        ok, out = _run([node_exe, "--version"])
        ver = _parse_version(out)
        node_ok = ok and ver and ver >= (MIN_NODE,)
        add(
            "node",
            "ok" if node_ok else "missing",
            out or node_exe,
            None if node_ok else "需要 Node.js %d+" % MIN_NODE,
        )
        if not node_ok:
            actions.append("升级 Node.js 到 %d+" % MIN_NODE)
    else:
        node_ok = False
        add("node", "missing", "未找到 node 命令", "安装 Node.js %d+" % MIN_NODE)
        actions.append("安装 Node.js %d+" % MIN_NODE)

    npm_exe = shutil.which("npm")
    add(
        "npm",
        "ok" if npm_exe else "missing",
        npm_exe or "未找到 npm 命令",
        None if npm_exe else "随 Node.js 一起安装",
    )

    cli_exe = _find_cli()
    cli_ok = False
    cli_detail = "未找到 playwright-cli"
    if cli_exe:
        use_shell = os.name == "nt"
        ok, out = _run([cli_exe, "--version"], use_shell=use_shell)
        cli_ok = ok
        cli_detail = out if ok else "playwright-cli 存在但 --version 失败"
        if ok:
            help_ok, help_text = _run([cli_exe, "--help"], use_shell=use_shell)
            if help_ok:
                lowered = help_text.lower()
                missing = [flag for flag in ("open", "eval", "close", "--json") if flag not in lowered]
                if missing:
                    cli_ok = False
                    cli_detail = "playwright-cli 缺少: %s" % ", ".join(missing)

    if not cli_ok and do_install and npm_exe:
        print("正在安装 %s ..." % CLI_PACKAGE, file=sys.stderr)
        installed = False
        try:
            subprocess.run(
                [npm_exe, "install", "-g", CLI_PACKAGE],
                shell=(os.name == "nt"),
                timeout=600,
                check=False,
            )
            installed = bool(_find_cli())
        except (OSError, subprocess.SubprocessError) as exc:
            print("安装失败: %s" % exc, file=sys.stderr)
        if installed:
            cli_exe = _find_cli()
            ok, out = _run([cli_exe, "--version"], use_shell=(os.name == "nt"))
            cli_ok = ok
            cli_detail = out if ok else "安装后仍无法执行"

    add(
        "playwright_cli",
        "ok" if cli_ok else "missing",
        cli_detail,
        None if cli_ok else "npm install -g %s，再执行 playwright-cli install 初始化 workspace" % CLI_PACKAGE,
    )
    if not cli_ok:
        actions.append("npm install -g %s" % CLI_PACKAGE)

    browsers = detect_browsers()
    add(
        "browser",
        "ok" if browsers else "missing",
        ", ".join(sorted({b["name"] for b in browsers})) or "未检测到 Chrome/Edge/Chromium",
        None if browsers else "安装系统 Chrome 或 Edge；确实没有则 playwright-cli install-browser",
    )
    if not browsers:
        actions.append("安装系统 Chrome/Edge，或执行 playwright-cli install-browser")

    skill_dir = Path(__file__).resolve().parent.parent
    add("skill_dir", "ok", str(skill_dir))

    if not py_ok or not node_ok:
        status = "blocked"
    elif not cli_ok or not browsers:
        status = "degraded"
    else:
        status = "ok"

    if status == "degraded":
        actions.append("降级路径：可让用户手动粘贴笔记内容，用 normalize_posts.py --manual 继续分析")

    return {
        "status": status,
        "platform": platform.system(),
        "python": py_ver,
        "skill_dir": str(skill_dir),
        "checks": checks,
        "browsers": browsers,
        "next_actions": actions,
    }


def main():
    parser = argparse.ArgumentParser(description="小红书求职情报 Skill 环境自检")
    parser.add_argument("--install", action="store_true", help="缺少 playwright-cli 时自动安装")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON（默认已是 JSON）")
    args = parser.parse_args()

    report = build_report(args.install)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    for check in report["checks"]:
        if check["status"] != "ok":
            print("[%s] %s: %s" % (check["status"], check["name"], check["detail"]), file=sys.stderr)
    for action in report["next_actions"]:
        print("-> %s" % action, file=sys.stderr)

    code = {"ok": 0, "degraded": 1, "blocked": 2}[report["status"]]
    sys.exit(code)


if __name__ == "__main__":
    main()