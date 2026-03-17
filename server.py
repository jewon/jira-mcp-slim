"""Jira Board MCP Server — Jira Server 7.13.5 REST API 기반."""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from mcp.server.fastmcp import FastMCP

# ── 설정 (.env → 환경변수) ──────────────────────

load_dotenv(Path(__file__).parent / ".env")

JIRA_URL = os.environ["JIRA_URL"]
BOARD_ID = int(os.environ["JIRA_BOARD_ID"])
WRITE_ENABLED = os.environ.get("JIRA_WRITE_ENABLED", "false").lower() == "true"

auth = HTTPBasicAuth(
    os.environ["JIRA_USER"],
    os.environ["JIRA_PASS"],
)

API2 = f"{JIRA_URL}/rest/api/2"
AGILE = f"{JIRA_URL}/rest/agile/1.0"

TIMEOUT = 15

_mode = "읽기/쓰기" if WRITE_ENABLED else "읽기 전용"
mcp = FastMCP(
    "Jira Board MCP",
    instructions=(
        f"Jira Server 보드의 이슈를 조회하고 관리하는 MCP 서버입니다. (현재 모드: {_mode}) "
        "이슈 검색, 상세 조회 등을 지원합니다. "
        "이슈 내용을 기반으로 내부 데이터를 조회하려면 별도의 내부 데이터 MCP를 함께 사용하세요."
    ),
)


# ── 헬퍼 ────────────────────────────────────────


def _get(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, auth=auth, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(url: str, json: dict | None = None) -> requests.Response:
    resp = requests.post(url, auth=auth, json=json, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def _active_sprint_id() -> int | None:
    sprints = _get(
        f"{AGILE}/board/{BOARD_ID}/sprint", {"state": "active"}
    ).get("values", [])
    return sprints[0]["id"] if sprints else None


def _slim_issue(issue: dict) -> dict:
    """이슈 JSON에서 에이전트에게 유용한 필드만 추출."""
    fields = issue.get("fields", {})
    return {
        "key": issue["key"],
        "summary": fields.get("summary"),
        "status": (fields.get("status") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "priority": (fields.get("priority") or {}).get("name"),
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "updated": fields.get("updated"),
    }


# ── Tools ───────────────────────────────────────


@mcp.tool()
def search_issues(jql: str, max_results: int = 20) -> list[dict]:
    """JQL 쿼리로 이슈를 검색합니다.

    예시 jql:
      - project = TA2018MPSMDS AND status != Done
      - assignee = currentUser() ORDER BY updated DESC
      - status = "In Progress" AND sprint in openSprints()
    """
    data = _get(f"{API2}/search", {
        "jql": jql,
        "maxResults": max_results,
        "fields": "summary,status,assignee,priority,issuetype,updated",
    })
    return [_slim_issue(i) for i in data.get("issues", [])]


@mcp.tool()
def get_issue(issue_key: str) -> dict:
    """이슈의 상세 정보를 조회합니다 (설명, 댓글, 가능한 전환 등).

    issue_key 예시: TA2018MPSMDS-123
    """
    data = _get(f"{API2}/issue/{issue_key}", {
        "fields": "summary,description,status,assignee,reporter,"
                  "priority,comment,created,updated,issuetype,labels,components",
        "expand": "transitions",
    })
    fields = data.get("fields", {})
    comments = fields.get("comment", {}).get("comments", [])
    transitions = data.get("transitions", [])

    return {
        "key": data["key"],
        "summary": fields.get("summary"),
        "description": fields.get("description"),
        "status": (fields.get("status") or {}).get("name"),
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "reporter": (fields.get("reporter") or {}).get("displayName"),
        "labels": fields.get("labels", []),
        "components": [c["name"] for c in fields.get("components", [])],
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "comments": [
            {
                "author": c.get("author", {}).get("displayName"),
                "body": c.get("body"),
                "created": c.get("created"),
            }
            for c in comments[-10:]  # 최근 10개만
        ],
        "available_transitions": [
            {"id": t["id"], "name": t["name"]} for t in transitions
        ],
    }


@mcp.tool()
def get_sprint_issues(
    sprint_id: int | None = None, max_results: int = 50
) -> dict:
    """스프린트의 이슈 목록을 조회합니다. sprint_id를 생략하면 현재 활성 스프린트를 사용합니다."""
    if sprint_id is None:
        sprint_id = _active_sprint_id()
        if sprint_id is None:
            return {"error": "활성 스프린트가 없습니다."}

    data = _get(f"{AGILE}/sprint/{sprint_id}/issue", {
        "maxResults": max_results,
        "fields": "summary,status,assignee,priority,issuetype,updated",
    })
    return {
        "sprint_id": sprint_id,
        "total": data.get("total", 0),
        "issues": [_slim_issue(i) for i in data.get("issues", [])],
    }


@mcp.tool()
def get_board_sprints(state: str = "active") -> list[dict]:
    """보드의 스프린트 목록을 조회합니다.

    state: active, closed, future (쉼표로 복수 가능: "active,future")
    """
    data = _get(f"{AGILE}/board/{BOARD_ID}/sprint", {
        "state": state,
        "maxResults": 20,
    })
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "state": s["state"],
            "startDate": s.get("startDate"),
            "endDate": s.get("endDate"),
        }
        for s in data.get("values", [])
    ]


# ── 쓰기 Tools (JIRA_WRITE_ENABLED=true 일 때만 노출) ──


if WRITE_ENABLED:

    @mcp.tool()
    def transition_issue(issue_key: str, transition_name: str) -> str:
        """이슈의 상태를 변경합니다.

        get_issue로 available_transitions를 먼저 확인한 뒤 transition_name을 지정하세요.
        예: transition_name="In Progress"
        """
        transitions = _get(f"{API2}/issue/{issue_key}/transitions")["transitions"]
        match = next(
            (t for t in transitions if t["name"].lower() == transition_name.lower()),
            None,
        )
        if not match:
            available = [t["name"] for t in transitions]
            return f"'{transition_name}' 전환을 찾을 수 없습니다. 가능한 전환: {available}"

        _post(
            f"{API2}/issue/{issue_key}/transitions",
            {"transition": {"id": match["id"]}},
        )
        return f"{issue_key} → {match['name']} 완료"

    @mcp.tool()
    def add_comment(issue_key: str, body: str) -> str:
        """이슈에 코멘트를 추가합니다. body에 해결 가이드나 분석 결과를 작성하세요."""
        _post(f"{API2}/issue/{issue_key}/comment", {"body": body})
        return f"{issue_key}에 코멘트 추가 완료"


# ── Resources ───────────────────────────────────


@mcp.resource("jira://board/summary")
def board_summary() -> str:
    """현재 보드 개요와 활성 스프린트 정보를 반환합니다."""
    board = _get(f"{AGILE}/board/{BOARD_ID}")
    sprints = _get(
        f"{AGILE}/board/{BOARD_ID}/sprint", {"state": "active"}
    ).get("values", [])

    lines = [
        f"# {board['name']}",
        f"- Board ID: {BOARD_ID}",
        f"- Type: {board.get('type', 'N/A')}",
        "",
    ]
    if sprints:
        s = sprints[0]
        lines += [
            f"## 활성 스프린트: {s['name']}",
            f"- ID: {s['id']}",
            f"- 시작: {s.get('startDate', 'N/A')}",
            f"- 종료: {s.get('endDate', 'N/A')}",
        ]
    else:
        lines.append("활성 스프린트 없음")

    return "\n".join(lines)


@mcp.resource("jira://sprint/active/issues")
def active_sprint_issues_summary() -> str:
    """활성 스프린트의 이슈 요약 목록을 반환합니다."""
    sprint_id = _active_sprint_id()
    if sprint_id is None:
        return "활성 스프린트가 없습니다."

    data = _get(f"{AGILE}/sprint/{sprint_id}/issue", {
        "maxResults": 100,
        "fields": "summary,status,assignee,priority",
    })

    lines = [f"# 활성 스프린트 이슈 (총 {data.get('total', 0)}건)", ""]
    for issue in data.get("issues", []):
        f = issue.get("fields", {})
        status = (f.get("status") or {}).get("name", "?")
        assignee = (f.get("assignee") or {}).get("displayName", "미배정")
        priority = (f.get("priority") or {}).get("name", "?")
        lines.append(
            f"- **{issue['key']}** [{status}] {f.get('summary')} "
            f"(담당: {assignee}, 우선순위: {priority})"
        )

    return "\n".join(lines)


# ── Entry point ─────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
