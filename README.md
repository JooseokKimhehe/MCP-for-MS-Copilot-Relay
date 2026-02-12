# MCP Gateway

Copilot과 연결되는 중계 MCP(Gateway) 프로젝트입니다. SubMCP는 별도 프로젝트로 구성합니다.

Endpoints:
- `GET /health` - 상태 확인
- `POST /mcp` - Copilot 요청 수신 후 SubMCP로 프록시
- `GET /mcp/tools` - SubMCP 도구 목록 집계

---

## Python 3.12.10

간단한 FastAPI 기반 MCP Gateway 서버가 추가되었습니다.

설치 및 실행 (Windows PowerShell):

```powershell
# 가상환경 생성
python -m venv .venv
# 가상환경 활성화
.\.venv\Scripts\Activate.ps1
# 의존성 설치
pip install -r requirements.txt
# 서버 실행
python app.py
```

또는 개발 모드로 직접 uvicorn 실행:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn src.mcp_server:app --reload --port 3000
```

포트는 환경변수 `PORT`로 변경 가능합니다.

Gateway 인증 설정 (Copilot -> Gateway):
- `MCP_API_KEY`: Gateway 요청에 사용할 API 키
- 요청 헤더: `Authorization: Bearer <MCP_API_KEY>`
- 키는 환경변수 또는 `config/submcp.env`에 넣어도 됩니다 (환경변수 우선).

SubMCP 연결 설정 (환경변수):
- `SUB_MCP_REGISTRY` (권장): JSON 배열
	예) [{"name":"sub1","base_url":"http://localhost:3001"}]
- `SUB_MCP_BASE_URL`: 단일 SubMCP URL
- `SUB_MCP_NAME`: 단일 SubMCP 이름 (기본값: sub)
- `SUB_MCP_TIMEOUT`: SubMCP 호출 타임아웃(초), 기본값 10

SubMCP 연결 설정 (파일):
- `config/submcp.env` 파일을 자동으로 읽습니다.
- 파일 값은 환경변수가 없을 때만 적용됩니다 (환경변수 우선).
- 샘플 파일은 `config/submcp.env.example` 참고

로컬에서 Gateway와 SubMCP를 동시에 실행한다면 포트 충돌에 유의하세요.
