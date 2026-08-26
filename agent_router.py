"""
agent_router.py
----------------
Bộ định tuyến (Router) cho KIO.ai.

Nhận một câu lệnh/task của người dùng, chấm điểm theo từ khoá để chọn ra
tối đa 4 "skill" phù hợp nhất, rồi từ skill suy ra bộ "tool" cần dùng.
Nếu không skill nào ăn điểm, agent luôn rơi về mặc định `project-analysis`
để đảm bảo luôn có một hướng xử lý an toàn (đúng như mô tả trên trang chủ).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


# ----------------------------------------------------------------------
# Định nghĩa Skill: mỗi skill có danh sách từ khoá kích hoạt + tool đi kèm
# ----------------------------------------------------------------------
SKILLS: Dict[str, Dict] = {
    "frontend": {
        "keywords": [
            "html", "css", "javascript", "js", "react", "ui", "giao diện",
            "component", "style", "layout", "responsive", "trang web",
        ],
        "tools": ["file-manager", "code-search", "build"],
    },
    "backend": {
        "keywords": [
            "api", "server", "backend", "database", "sql", "endpoint",
            "route", "flask", "django", "fastapi", "python", "node",
            "authentication", "xác thực",
        ],
        "tools": ["file-manager", "code-search", "terminal", "build"],
    },
    "debugging": {
        "keywords": [
            "lỗi", "bug", "error", "exception", "crash", "sửa lỗi",
            "traceback", "fix", "debug", "không chạy",
        ],
        "tools": ["code-search", "file-manager", "terminal", "test"],
    },
    "testing": {
        "keywords": [
            "test", "kiểm thử", "unittest", "pytest", "coverage",
            "assert", "qa", "kiểm tra",
        ],
        "tools": ["test", "terminal", "code-search"],
    },
    "git-workflow": {
        "keywords": [
            "git", "commit", "branch", "merge", "pull request", "pr",
            "rebase", "checkout", "clone", "github",
        ],
        "tools": ["git", "github", "terminal"],
    },
    "deployment": {
        "keywords": [
            "deploy", "triển khai", "ci/cd", "pipeline", "docker",
            "release", "production", "host", "domain",
        ],
        "tools": ["deploy", "terminal", "build", "github"],
    },
    "project-analysis": {
        # Skill mặc định — luôn có sẵn để agent không bao giờ "đứng hình"
        "keywords": [
            "phân tích", "tổng quan", "cấu trúc", "overview", "structure",
            "workspace", "project",
        ],
        "tools": ["file-manager", "code-search", "build"],
    },
}

DEFAULT_SKILL = "project-analysis"
MAX_SKILLS = 4


@dataclass
class RouteResult:
    task: str
    scores: Dict[str, int] = field(default_factory=dict)
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    used_default: bool = False

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "scores": self.scores,
            "skills": self.skills,
            "tools": self.tools,
            "used_default": self.used_default,
        }


def _score_task(text: str) -> Dict[str, int]:
    """Chấm điểm mỗi skill dựa trên số từ khoá xuất hiện trong text."""
    text_low = text.lower()
    scores: Dict[str, int] = {}
    for skill, cfg in SKILLS.items():
        hits = sum(1 for kw in cfg["keywords"] if kw in text_low)
        if hits:
            scores[skill] = hits
    return scores


def route(task: str) -> RouteResult:
    """
    Điểm vào chính của Router.
    1) Chấm điểm từ khoá.
    2) Chọn tối đa MAX_SKILLS skill có điểm cao nhất.
    3) Nếu không skill nào ăn điểm -> dùng DEFAULT_SKILL.
    4) Gộp tool tương ứng, loại trùng nhưng giữ thứ tự xuất hiện.
    """
    scores = _score_task(task)
    result = RouteResult(task=task, scores=scores)

    if not scores:
        result.skills = [DEFAULT_SKILL]
        result.used_default = True
    else:
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        result.skills = [name for name, _ in ranked[:MAX_SKILLS]]

    seen = set()
    tools: List[str] = []
    for skill in result.skills:
        for tool in SKILLS[skill]["tools"]:
            if tool not in seen:
                seen.add(tool)
                tools.append(tool)
    result.tools = tools
    return result


def make_plan(task: str, route_result: RouteResult) -> List[Dict[str, str]]:
    """
    Sinh kế hoạch từng bước theo đúng kiến trúc trên trang chủ:
    Task -> Router · score -> Scan workspace -> Plan -> Report
    """
    return [
        {"step": "Phân tích task", "detail": f'Đọc yêu cầu: "{task}"'},
        {
            "step": "Router chấm điểm",
            "detail": f"Chọn skill: {', '.join(route_result.skills)}"
            + (" (mặc định, không khớp từ khoá)" if route_result.used_default else ""),
        },
        {
            "step": "Chọn tool",
            "detail": f"Tool sẽ dùng: {', '.join(route_result.tools)}",
        },
        {"step": "Quét workspace", "detail": "Liệt kê file, bỏ qua .git/node_modules/.venv/.env"},
        {"step": "Thực thi", "detail": "Chạy tool đã chọn theo kế hoạch"},
        {"step": "Validate", "detail": "Kiểm tra kết quả (test/build nếu có)"},
        {"step": "Báo cáo", "detail": "Tổng hợp kết quả trả về cho người dùng"},
    ]