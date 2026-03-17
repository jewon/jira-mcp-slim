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

### 쓰기 (`JIRA_WRITE_ENABLED=true` 일 때만 활성)

| Tool | 설명 |
|---|---|
| `transition_issue` | 이슈 상태 변경 |
| `add_comment` | 이슈에 코멘트 추가 |

## 제공하는 Resources

| URI | 설명 |
|---|---|
| `jira://board/summary` | 보드 개요 + 활성 스프린트 |
| `jira://sprint/active/issues` | 활성 스프린트 이슈 요약 |
