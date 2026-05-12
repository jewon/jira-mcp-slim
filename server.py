"""Jira Board MCP Server — Jira Server 7.13.5 REST API 기반."""

import json
import os
import re
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
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

# 커스텀 필드 설정 (custom_fields.json)
# 구조: { "<이슈유형>": { "<key>": { "field_id", "label", "type", "required", "description", "allowed_values"? } } }
_CF_PATH = Path(__file__).parent / "custom_fields.json"
CUSTOM_FIELDS: dict[str, dict[str, dict]] = json.loads(_CF_PATH.read_text(encoding="utf-8")) if _CF_PATH.exists() else {}

_mode = "읽기/쓰기" if WRITE_ENABLED else "읽기 전용"
mcp = FastMCP(
    "Jira Board MCP",
    instructions=(
        f"Jira Server 보드의 이슈를 조회하고 관리하는 MCP 서버입니다. (현재 모드: {_mode}) "
        "이슈 검색, 상세 조회, 이슈 생성, 댓글 작성 등을 지원합니다. "
        "이슈 내용을 기반으로 내부 데이터를 조회하려면 별도의 내부 데이터 MCP를 함께 사용하세요."
    ),
)

LLM_PREFIX = "(이 이슈/댓글은 LLM이 작성하여 MCP(JIRA MCP)를 통해 자동 등록되었습니다.)"


# ── 헬퍼 ────────────────────────────────────────


def _get(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, auth=auth, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _put(url: str, json: dict | None = None) -> requests.Response:
    resp = requests.put(url, auth=auth, json=json, timeout=TIMEOUT)
    if not resp.ok:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise requests.HTTPError(
            f"Jira API error {resp.status_code}: {body}",
            response=resp,
        )
    return resp


def _post(url: str, json: dict | None = None) -> requests.Response:
    resp = requests.post(url, auth=auth, json=json, timeout=TIMEOUT)
    if not resp.ok:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise requests.HTTPError(
            f"Jira API error {resp.status_code}: {body}",
            response=resp,
        )
    return resp


def _attachment_summary(attachment: dict) -> dict:
    return {
        "id": attachment.get("id"),
        "filename": attachment.get("filename"),
        "mimeType": attachment.get("mimeType"),
        "size": attachment.get("size"),
        "author": (attachment.get("author") or {}).get("displayName"),
        "created": attachment.get("created"),
        "content": attachment.get("content"),
        "thumbnail": attachment.get("thumbnail"),
    }


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name or "attachment"


def _download_attachment_content(url: str, path: Path) -> requests.Response:
    with requests.get(url, auth=auth, stream=True, timeout=TIMEOUT) as resp:
        if not resp.ok:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise requests.HTTPError(
                f"Jira API error {resp.status_code}: {body}",
                response=resp,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
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
                  "priority,comment,attachment,created,updated,issuetype,labels,components",
        "expand": "transitions",
    })
    fields = data.get("fields", {})
    comments = fields.get("comment", {}).get("comments", [])
    attachments = fields.get("attachment", [])
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
        "attachments": [_attachment_summary(a) for a in attachments],
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
def download_attachment(
    issue_key: str,
    attachment_id: str | None = None,
    filename: str | None = None,
) -> dict:
    """Download one Jira issue attachment to a local file.

    Specify either attachment_id or filename. If the issue has exactly one
    attachment, both can be omitted. By default files are saved under
    downloads/<issue_key>/.
    """
    data = _get(f"{API2}/issue/{issue_key}", {"fields": "attachment"})
    attachments = data.get("fields", {}).get("attachment", [])

    if not attachments:
        return {"error": f"{issue_key} has no attachments."}

    matches = attachments
    if attachment_id:
        matches = [a for a in attachments if str(a.get("id")) == str(attachment_id)]
    if filename:
        matches = [a for a in matches if a.get("filename") == filename]

    if not attachment_id and not filename and len(matches) > 1:
        return {
            "error": "Multiple attachments found. Specify attachment_id or filename.",
            "attachments": [_attachment_summary(a) for a in attachments],
        }
    if not matches:
        return {
            "error": "Attachment not found.",
            "attachments": [_attachment_summary(a) for a in attachments],
        }
    if len(matches) > 1:
        return {
            "error": "Multiple attachments matched. Specify attachment_id.",
            "attachments": [_attachment_summary(a) for a in matches],
        }

    attachment = matches[0]
    content_url = attachment.get("content")
    if not content_url:
        return {"error": "Attachment has no content URL.", "attachment": _attachment_summary(attachment)}

    base_dir = (DOWNLOAD_DIR / issue_key).resolve()
    path = base_dir / _safe_filename(attachment.get("filename") or f"attachment-{attachment.get('id')}")

    try:
        _download_attachment_content(content_url, path)
    except requests.HTTPError as e:
        return {"error": str(e), "attachment": _attachment_summary(attachment)}

    size = path.stat().st_size
    return {
        "issue_key": issue_key,
        "attachment": _attachment_summary(attachment),
        "path": str(path),
        "downloaded_size": size,
        "size_matches": attachment.get("size") in (None, size),
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


@mcp.tool()
def list_custom_fields(issuetype: str | None = None) -> dict:
    """이슈 유형별 커스텀 필드 목록을 반환합니다.

    issuetype을 지정하면 해당 유형의 필드만 반환하고,
    생략하면 전체 이슈 유형 목록과 각 유형의 필드 요약을 반환합니다.

    create_issue 호출 전에 이 툴로 필수 필드를 먼저 확인하세요.
    """
    if issuetype:
        cfg = CUSTOM_FIELDS.get(issuetype)
        if not cfg:
            return {"error": f"'{issuetype}' 이슈 유형을 찾을 수 없습니다.", "available": list(CUSTOM_FIELDS.keys())}
        return {
            "issuetype": issuetype,
            "fields": [
                {
                    "key": key,
                    "label": f.get("label", key),
                    "type": f.get("type", "text"),
                    "required": f.get("required", False),
                    "description": f.get("description", ""),
                    "allowed_values": f.get("allowed_values"),
                }
                for key, f in cfg.items()
            ],
        }
    return {
        "issuetypes": [
            {
                "name": it,
                "required_fields": [k for k, f in fields.items() if f.get("required")],
                "optional_fields": [k for k, f in fields.items() if not f.get("required")],
            }
            for it, fields in CUSTOM_FIELDS.items()
        ]
    }


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

        try:
            _post(
                f"{API2}/issue/{issue_key}/transitions",
                {"transition": {"id": match["id"]}},
            )
            return f"{issue_key} → {match['name']} 완료"
        except requests.HTTPError as e:
            return f"전환 실패: {e}"

    @mcp.tool()
    def create_issue(
        project_key: str,
        summary: str,
        description: str = "",
        issuetype: str = "작업",
        priority: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        custom_fields: dict[str, object] | None = None,
    ) -> dict:
        """새 이슈를 생성합니다.

        project_key: 프로젝트 키 (예: TA2018MPSMDS)
        summary: 이슈 제목
        description: 이슈 본문 (LLM prefix가 자동으로 앞에 붙습니다)
        issuetype: 이슈 유형. list_custom_fields()로 지원 유형 확인 가능
        priority: 우선순위 (Highest, High, Medium, Low, Lowest)
        assignee: 담당자 username
        labels: 레이블 목록
        custom_fields: 커스텀 필드 값 딕셔너리.
            이슈 유형마다 필수 필드가 다르므로 list_custom_fields(issuetype=...) 로 먼저 확인하세요.
            예: {"due_date": "2026-05-01", "related_layer": "user_table"}
        """
        issuetype_cfg = CUSTOM_FIELDS.get(issuetype, {})
        custom_fields = custom_fields or {}

        # 필수 필드 누락 검사
        missing = [
            f"{key} ({cfg['label']})"
            for key, cfg in issuetype_cfg.items()
            if cfg.get("required") and key not in custom_fields
        ]
        if missing:
            return {"error": f"'{issuetype}' 이슈 유형에 필수 필드가 누락되었습니다: {missing}"}

        prefixed_description = f"{LLM_PREFIX}\n\n{description}" if description else LLM_PREFIX

        fields: dict = {
            "project": {"key": project_key},
            "summary": summary,
            "description": prefixed_description,
            "issuetype": {"name": issuetype},
        }
        if priority:
            fields["priority"] = {"name": priority}
        if assignee:
            fields["assignee"] = {"name": assignee}
        if labels:
            fields["labels"] = labels

        for key, value in custom_fields.items():
            cfg = issuetype_cfg.get(key)
            if not cfg:
                return {"error": f"'{issuetype}' 이슈 유형에 없는 필드: '{key}'. list_custom_fields(issuetype='{issuetype}')로 확인하세요."}
            allowed = cfg.get("allowed_values")
            if allowed and value not in allowed:
                return {"error": f"'{key}' 필드의 허용값: {allowed}. 입력값: '{value}'"}
            fields[cfg["field_id"]] = value

        try:
            resp = _post(f"{API2}/issue", {"fields": fields})
            data = resp.json()
            return {
                "key": data.get("key"),
                "id": data.get("id"),
                "self": data.get("self"),
            }
        except requests.HTTPError as e:
            return {"error": str(e)}

    @mcp.tool()
    def update_issue_description(issue_key: str, description: str) -> str:
        """이슈의 본문(description)을 수정합니다.

        issue_key: 수정할 이슈 키 (예: TA2018MPSMDS-123)
        description: 새 본문 내용 (LLM prefix가 자동으로 앞에 붙습니다)
        """
        prefixed_description = f"{LLM_PREFIX}\n\n{description}"
        try:
            _put(
                f"{API2}/issue/{issue_key}",
                {"fields": {"description": prefixed_description}},
            )
            return f"{issue_key} 본문 수정 완료"
        except requests.HTTPError as e:
            return f"본문 수정 실패: {e}"

    @mcp.tool()
    def add_comment(issue_key: str, body: str) -> str:
        """이슈에 코멘트를 추가합니다. body에 해결 가이드나 분석 결과를 작성하세요.

        코멘트 본문 앞에 LLM prefix가 자동으로 붙습니다.
        """
        prefixed_body = f"{LLM_PREFIX}\n\n{body}"
        try:
            _post(f"{API2}/issue/{issue_key}/comment", {"body": prefixed_body})
            return f"{issue_key}에 코멘트 추가 완료"
        except requests.HTTPError as e:
            return f"코멘트 추가 실패: {e}"


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
