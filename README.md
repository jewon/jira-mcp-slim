# jira-mcp-slim

Jira Server/Data Center 보드의 이슈를 AI 에이전트가 조회·관리할 수 있도록 하는 얇은 MCP 서버.

## 지원 Jira 버전

`/rest/api/2` 및 `/rest/agile/1.0` API를 사용합니다.

- **Jira Software Server / Data Center 7.0 ~ 9.x** — 지원
- Jira 6.x 이하 — 미지원 (Agile API 경로가 다름)
- Jira Cloud — 인증 방식이 다르므로 미지원

## 요구 사항

- Python 3.10+
- `mcp >= 1.20`, `requests >= 2.28`, `python-dotenv >= 1.0`

```bash
pip install mcp requests python-dotenv
```

## 설정

### 1. 환경 변수 (`.env`)

`.env.example`을 복사하여 `.env`를 만들고 값을 입력:

```bash
cp .env.example .env
# .env 파일을 편집하여 실제 값 입력
```

| 변수 | 필수 | 설명 |
|---|---|---|
| `JIRA_URL` | O | Jira 서버 URL |
| `JIRA_BOARD_ID` | O | 대상 보드 ID |
| `JIRA_USER` | O | Jira 사용자명 |
| `JIRA_PASS` | O | Jira 비밀번호 |
| `JIRA_WRITE_ENABLED` | | `true`로 설정 시 쓰기 tool 활성화 (기본: `false`) |

### 2. 커스텀 필드 (`custom_fields.json`)

이슈 유형별 커스텀 필드를 JSON으로 관리합니다.

```bash
cp custom_fields.example.json custom_fields.json
# custom_fields.json 을 편집하여 실제 필드 ID와 메타데이터 입력
```

파일 구조:

```json
{
  "이슈유형명": {
    "필드키": {
      "field_id": "customfield_XXXXX",
      "label": "화면 표시명",
      "type": "text",
      "required": true,
      "description": "필드 설명"
    }
  }
}
```

- `required: true`인 필드는 `create_issue` 호출 시 반드시 포함해야 합니다.
- 선택 필드는 `required` 키를 생략합니다.
- 실제 `field_id`는 `GET /rest/api/2/field` 또는 `createmeta` API로 확인할 수 있습니다.

## 테스트

```bash
# MCP Inspector로 확인
mcp dev server.py
```

## 에이전트 설정

### Claude Code (`settings.local.json`)

```json
{
  "mcpServers": {
    "jira": {
      "command": "python",
      "args": ["/path/to/jira-mcp/server.py"]
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "jira": {
      "command": "python",
      "args": ["/path/to/jira-mcp/server.py"]
    }
  }
}
```

> 인증 정보는 `jira-mcp/.env`에서 자동으로 로드됩니다. MCP config에 별도로 넣을 필요 없습니다.

## 제공하는 Tools

### 읽기 (항상 활성)

| Tool | 설명 |
|---|---|
| `search_issues` | JQL로 이슈 검색 |
| `get_issue` | 이슈 상세 조회 (설명, 댓글, 가능한 전환) |
| `get_sprint_issues` | 스프린트 이슈 목록 (기본: 활성 스프린트) |
| `get_board_sprints` | 보드 스프린트 목록 |
| `list_custom_fields` | 이슈 유형별 커스텀 필드 목록 조회 |

### 쓰기 (`JIRA_WRITE_ENABLED=true` 일 때만 활성)

| Tool | 설명 |
|---|---|
| `create_issue` | 새 이슈 생성 (커스텀 필드 포함, 필수 필드 자동 검증) |
| `update_issue_description` | 이슈 본문 수정 |
| `transition_issue` | 이슈 상태 변경 |
| `add_comment` | 이슈에 코멘트 추가 |

### 이슈 생성 흐름

```
1. list_custom_fields()           → 지원하는 이슈 유형 목록 확인
2. list_custom_fields("작업")      → 해당 유형의 필수/선택 필드 확인
3. create_issue(project_key=..., issuetype="작업", custom_fields={...})
```

> `create_issue` 및 `add_comment`, `update_issue_description`으로 작성된 내용에는 LLM이 자동 생성했음을 나타내는 prefix가 앞에 붙습니다.

## 제공하는 Resources

| URI | 설명 |
|---|---|
| `jira://board/summary` | 보드 개요 + 활성 스프린트 |
| `jira://sprint/active/issues` | 활성 스프린트 이슈 요약 |
