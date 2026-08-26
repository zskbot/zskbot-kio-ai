"""
server.py
---------
Backend thật cho KIO.ai — chạy bằng Flask.

Chạy:
    pip install -r requirements.txt
    cp .env.example .env      # rồi điền ANTHROPIC_API_KEY, AGENT_TOKEN...
    python server.py

Mặc định lắng nghe tại http://localhost:8000 và phục vụ luôn index.html
(giao diện có sẵn khung chat) tại "/".

Các API chính:
    POST /api/chat        -> chat với agent (router chọn skill/tool + gọi Claude)
    GET  /api/scan         -> quét workspace, liệt kê file
    POST /api/tool/<name>  -> gọi trực tiếp 1 tool (git/github/http/terminal/...)
    GET  /api/health       -> kiểm tra server sống

LƯU Ý AN TOÀN:
    - Endpoint terminal chỉ cho phép các lệnh nằm trong ALLOWLIST bên dưới,
      và bắt buộc phải có header "X-Agent-Token" khớp với AGENT_TOKEN trong .env.
    - KHÔNG deploy public mà không đặt AGENT_TOKEN + giới hạn CORS phù hợp,
      vì đây là một cửa thực thi lệnh trên máy chủ.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import List

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

import agent_router

load_dotenv()

APP_DIR = Path(__file__).parent.resolve()
WORKSPACE = Path(os.getenv("KIO_WORKSPACE", APP_DIR)).resolve()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")  # bắt buộc cho các tool nhạy cảm

IGNORE_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".env", "venv"}
PRIORITY_FILES = ["index.html", "style.css", "app.js", "server.py", "agent_router.py"]

# Chỉ những lệnh này được phép chạy qua tool "terminal" — không cho phép
# bất kỳ chuỗi lệnh tự do nào để tránh RCE khi server bị public.
TERMINAL_ALLOWLIST = {
    "pwd": ["pwd"],
    "ls": ["ls", "-la"],
    "git status": ["git", "status"],
    "git log": ["git", "log", "--oneline", "-n", "20"],
    "python version": ["python3", "--version"],
    "pip list": ["pip", "list"],
}

app = Flask(__name__, static_folder=None)
CORS(app)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def require_agent_token():
    """Trả về True nếu request có token hợp lệ (khi AGENT_TOKEN được cấu hình)."""
    if not AGENT_TOKEN:
        # Chưa cấu hình token -> chặn luôn các tool nhạy cảm để an toàn mặc định
        return False
    return request.headers.get("X-Agent-Token") == AGENT_TOKEN


def scan_workspace(root: Path) -> dict:
    files: List[dict] = []
    for path in root.rglob("*"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.is_file():
            rel = str(path.relative_to(root))
            files.append({
                "path": rel,
                "size": path.stat().st_size,
                "priority": Path(rel).name in PRIORITY_FILES,
            })
    files.sort(key=lambda f: (not f["priority"], f["path"]))
    return {"root": str(root), "count": len(files), "files": files}


def code_search(root: Path, query: str, max_hits: int = 50) -> List[dict]:
    hits = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    for path in root.rglob("*"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.stat().st_size > 500_000:
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append({
                    "file": str(path.relative_to(root)),
                    "line": i,
                    "text": line.strip()[:200],
                })
                if len(hits) >= max_hits:
                    return hits
    return hits


def git_tool(action: str) -> dict:
    safe_actions = {
        "status": ["git", "status", "--short", "--branch"],
        "log": ["git", "log", "--oneline", "-n", "20"],
        "branch": ["git", "branch", "-a"],
        "diff": ["git", "diff", "--stat"],
    }
    if action not in safe_actions:
        return {"error": f"Hành động git không được hỗ trợ: {action}"}
    try:
        out = subprocess.run(
            safe_actions[action], cwd=WORKSPACE, capture_output=True,
            text=True, timeout=15,
        )
        return {"ok": out.returncode == 0, "stdout": out.stdout, "stderr": out.stderr}
    except FileNotFoundError:
        return {"error": "git chưa được cài trên máy chủ."}
    except subprocess.TimeoutExpired:
        return {"error": "Lệnh git chạy quá lâu, đã huỷ."}


def github_tool(action: str, payload: dict) -> dict:
    if not GITHUB_TOKEN:
        return {"error": "Chưa cấu hình GITHUB_TOKEN trong .env."}
    import requests

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    base = "https://api.github.com"

    try:
        if action == "list_repos":
            r = requests.get(f"{base}/user/repos", headers=headers, timeout=15,
                              params={"per_page": 20, "sort": "updated"})
        elif action == "repo_info":
            owner, repo = payload["owner"], payload["repo"]
            r = requests.get(f"{base}/repos/{owner}/{repo}", headers=headers, timeout=15)
        elif action == "create_issue":
            owner, repo = payload["owner"], payload["repo"]
            r = requests.post(
                f"{base}/repos/{owner}/{repo}/issues", headers=headers, timeout=15,
                json={"title": payload["title"], "body": payload.get("body", "")},
            )
        else:
            return {"error": f"Hành động github không được hỗ trợ: {action}"}
        return {"ok": r.ok, "status": r.status_code, "data": r.json()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def http_tool(url: str) -> dict:
    import requests
    try:
        r = requests.get(url, timeout=10)
        return {"ok": r.ok, "status": r.status_code, "text": r.text[:5000]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def terminal_tool(command_key: str) -> dict:
    if command_key not in TERMINAL_ALLOWLIST:
        return {"error": f"Lệnh '{command_key}' không nằm trong allowlist."}
    try:
        out = subprocess.run(
            TERMINAL_ALLOWLIST[command_key], cwd=WORKSPACE,
            capture_output=True, text=True, timeout=15,
        )
        return {"ok": out.returncode == 0, "stdout": out.stdout, "stderr": out.stderr}
    except subprocess.TimeoutExpired:
        return {"error": "Lệnh chạy quá lâu, đã huỷ."}


def call_claude(system_prompt: str, user_message: str) -> str:
    if not ANTHROPIC_API_KEY:
        return (
            "[Chưa cấu hình ANTHROPIC_API_KEY] Đây là phản hồi giả lập.\n\n"
            f"Router đã chọn skill/tool phù hợp cho yêu cầu của bạn — "
            f"điền ANTHROPIC_API_KEY vào .env để bật trả lời AI thật."
        )
    import requests
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts) if parts else "(không có phản hồi text)"


# ----------------------------------------------------------------------
# Routes — giao diện tĩnh
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(APP_DIR, filename)


# ----------------------------------------------------------------------
# Routes — API
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "time": time.time(), "workspace": str(WORKSPACE)})


@app.get("/api/scan")
def api_scan():
    return jsonify(scan_workspace(WORKSPACE))


@app.post("/api/chat")
def api_chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Thiếu 'message'"}), 400

    route_result = agent_router.route(message)
    plan = agent_router.make_plan(message, route_result)
    ws_summary = scan_workspace(WORKSPACE)

    system_prompt = (
        "Bạn là KIO.ai, một AI coding agent. Bạn vừa chọn các skill sau: "
        f"{', '.join(route_result.skills)} và các tool: {', '.join(route_result.tools)}. "
        f"Workspace hiện có {ws_summary['count']} file. "
        "Trả lời ngắn gọn, rõ ràng, bằng tiếng Việt, tập trung vào việc giúp người dùng "
        "với yêu cầu lập trình của họ. Nếu cần chạy lệnh/tool, hãy nói rõ bước tiếp theo."
    )
    reply = call_claude(system_prompt, message)

    return jsonify({
        "reply": reply,
        "route": route_result.to_dict(),
        "plan": plan,
    })


@app.post("/api/tool/<name>")
def api_tool(name):
    body = request.get_json(force=True, silent=True) or {}

    if name == "code-search":
        query = body.get("query", "")
        if not query:
            return jsonify({"error": "Thiếu 'query'"}), 400
        return jsonify({"hits": code_search(WORKSPACE, query)})

    if name == "file-manager":
        rel = body.get("path", "")
        target = (WORKSPACE / rel).resolve()
        if WORKSPACE not in target.parents and target != WORKSPACE:
            return jsonify({"error": "Đường dẫn ngoài workspace, từ chối."}), 403
        if target.is_dir():
            return jsonify(scan_workspace(target))
        if target.is_file() and target.stat().st_size < 200_000:
            return jsonify({"path": rel, "content": target.read_text(errors="ignore")})
        return jsonify({"error": "File không tồn tại hoặc quá lớn."}), 404

    # Các tool nhạy cảm dưới đây bắt buộc có AGENT_TOKEN hợp lệ
    if name in {"git", "github", "http", "terminal"} and not require_agent_token():
        return jsonify({"error": "Thiếu hoặc sai X-Agent-Token cho tool nhạy cảm này."}), 401

    if name == "git":
        return jsonify(git_tool(body.get("action", "status")))
    if name == "github":
        return jsonify(github_tool(body.get("action", ""), body))
    if name == "http":
        url = body.get("url", "")
        if not url:
            return jsonify({"error": "Thiếu 'url'"}), 400
        return jsonify(http_tool(url))
    if name == "terminal":
        return jsonify(terminal_tool(body.get("command", "")))

    return jsonify({"error": f"Tool '{name}' không tồn tại.",
                     "available": ["file-manager", "code-search", "git", "github", "http", "terminal"]}), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"KIO.ai backend đang chạy tại http://localhost:{port}  (workspace: {WORKSPACE})")
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG", "0") == "1")