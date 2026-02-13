import asyncio
import json
import logging
import os
import secrets
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("mcp_server")
_SUBMCP_ENV_PATH = os.path.join("config", "submcp.env")
_LOG_DIR = "log"
_LOG_DATE_FORMAT = "%Y%m%d"


class MCPRequest(BaseModel):
    # JSON-RPC 2.0 요청 스키마
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    method: str
    params: Optional[dict] = Field(default_factory=dict)


class ToolSchema(BaseModel):
    # 도구의 입력/출력 스키마 정의
    type: str
    properties: dict
    required: list[str] = Field(default_factory=list)


class ToolModel(BaseModel):
    # 도구 메타데이터 모델
    name: str
    description: str
    inputSchema: ToolSchema
    outputSchema: ToolSchema


class ToolExecuteRequest(BaseModel):
    # 도구 실행 요청 스키마
    tool: str
    input: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class SubMCPConfig:
    name: str
    base_url: str


app = FastAPI(title="MCP Gateway (Python)")


class _DateRollingFileHandler(logging.Handler):
    def __init__(self, log_dir: str, date_format: str, formatter: logging.Formatter) -> None:
        super().__init__()
        self._log_dir = log_dir
        self._date_format = date_format
        self._formatter = formatter
        self._current_date: str | None = None
        self._handler: logging.FileHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            current_date = datetime.now().strftime(self._date_format)
            if self._handler is None or self._current_date != current_date:
                self._rotate_handler(current_date)
            if self._handler is None:
                return
            self._handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._handler:
            self._handler.close()
            self._handler = None
        super().close()

    def _rotate_handler(self, current_date: str) -> None:
        os.makedirs(self._log_dir, exist_ok=True)
        filename = os.path.join(self._log_dir, f"{current_date}.log")
        if self._handler:
            self._handler.close()
        self._handler = logging.FileHandler(filename, mode="a", encoding="utf-8")
        self._handler.setFormatter(self._formatter)
        self._current_date = current_date


def configure_gateway_logging() -> None:
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    if not any(isinstance(handler, _DateRollingFileHandler) for handler in root_logger.handlers):
        file_handler = _DateRollingFileHandler(_LOG_DIR, _LOG_DATE_FORMAT, formatter)
        root_logger.addHandler(file_handler)


def _parse_submcp_registry(raw: str) -> list[SubMCPConfig]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[MCP] Invalid SUB_MCP_REGISTRY JSON: %s", exc)
        return []
    if not isinstance(parsed, list):
        logger.error("[MCP] SUB_MCP_REGISTRY must be a list")
        return []
    configs: list[SubMCPConfig] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        base_url = entry.get("base_url") or entry.get("baseUrl")
        base_url = str(base_url).strip()
        if not name or not base_url:
            continue
        configs.append(SubMCPConfig(name=name, base_url=base_url))
    return configs


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"')
                if not key:
                    continue
                if key in os.environ:
                    continue
                os.environ[key] = value
    except OSError as exc:
        logger.warning("[MCP] Failed to read %s: %s", path, exc)


def _read_env_file_values(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"')
                if not key:
                    continue
                values[key] = value
    except OSError as exc:
        logger.warning("[MCP] Failed to read %s: %s", path, exc)
    return values


def _load_submcp_configs() -> list[SubMCPConfig]:
    _load_env_file(_SUBMCP_ENV_PATH)
    raw_registry = os.getenv("SUB_MCP_REGISTRY", "").strip()
    configs = _parse_submcp_registry(raw_registry)
    if configs:
        return configs

    base_url = os.getenv("SUB_MCP_BASE_URL", "").strip()
    if base_url:
        name = os.getenv("SUB_MCP_NAME", "sub").strip() or "sub"
        return [SubMCPConfig(name=name, base_url=base_url)]

    logger.warning("[MCP] No SubMCP configured. Set SUB_MCP_REGISTRY or SUB_MCP_BASE_URL.")
    return []


def _parse_gateway_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    if not raw:
        return keys
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            label, _, value = entry.partition(":")
        elif "=" in entry:
            label, _, value = entry.partition("=")
        else:
            continue
        label = label.strip()
        value = value.strip()
        if not label or not value:
            continue
        keys[label] = value
    return keys


def _get_gateway_api_keys() -> dict[str, str]:
    _load_env_file(_SUBMCP_ENV_PATH)
    raw_keys = os.getenv("MCP_API_KEYS", "").strip()
    parsed = _parse_gateway_keys(raw_keys)
    if parsed:
        return parsed
    single = os.getenv("MCP_API_KEY", "").strip()
    if single:
        return {"default": single}
    return {}


def _reload_gateway_auth_from_env_file() -> dict[str, str]:
    env_values = _read_env_file_values(_SUBMCP_ENV_PATH)
    managed_keys = {"MCP_API_KEYS", "MCP_API_KEY"}
    for key in managed_keys:
        if key in env_values:
            os.environ[key] = env_values[key]
        else:
            os.environ.pop(key, None)
    return _get_gateway_api_keys()


async def _require_gateway_auth(authorization: Optional[str] = Header(None)) -> None:
    expected_keys = _get_gateway_api_keys()
    logger.info("[MCP] Auth check: expected_key_count=%d", len(expected_keys))
    if not expected_keys:
        logger.error("[MCP] MCP_API_KEY(S) not configured")
        raise HTTPException(status_code=500, detail={"error": "server_misconfigured"})
    if not authorization:
        logger.warning("[MCP] Auth failed: missing Authorization header")
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    scheme, _, token = authorization.partition(" ")
    logger.info("[MCP] Auth header received: scheme=%s, token_len=%d", scheme, len(token) if token else 0)
    if scheme.lower() != "bearer" or not token:
        logger.warning("[MCP] Auth failed: invalid Authorization header format (expected 'Bearer <token>')")
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    token = token.strip()
    matched = any(secrets.compare_digest(token, expected) for expected in expected_keys.values())
    if not matched:
        logger.warning("[MCP] Auth failed: token mismatch (received_len=%d)", len(token))
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    logger.info("[MCP] Auth success")


def _split_tool_name(tool_name: str) -> Optional[tuple[str, str]]:
    if "." not in tool_name:
        return None
    sub_name, actual_tool = tool_name.split(".", 1)
    if not sub_name or not actual_tool:
        return None
    return sub_name, actual_tool


def _prefix_tool_name(sub_name: str, tool_name: str) -> str:
    return f"{sub_name}.{tool_name}"


def _submcp_timeout() -> float:
    try:
        return float(os.getenv("SUB_MCP_TIMEOUT", "10"))
    except ValueError:
        return 10.0


def _is_valid_log_date(value: str) -> bool:
    if len(value) != 8 or not value.isdigit():
        return False
    try:
        datetime.strptime(value, _LOG_DATE_FORMAT)
    except ValueError:
        return False
    return True


async def _call_submcp(sub: SubMCPConfig, method: str, params: Optional[dict], request_id: Optional[str]) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    url = f"{sub.base_url.rstrip('/')}/mcp"
    logger.info("[MCP] Calling SubMCP: sub_name=%s, url=%s, method=%s", sub.name, url, method)
    try:
        async with httpx.AsyncClient(timeout=_submcp_timeout()) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("[MCP] SubMCP response: sub_name=%s, status=%d", sub.name, resp.status_code)
            return resp.json()
    except httpx.RequestError as exc:
        logger.error("[MCP] SubMCP connection error: sub_name=%s, url=%s, error=%s", sub.name, url, exc)
        raise
    except httpx.HTTPStatusError as exc:
        logger.error("[MCP] SubMCP HTTP error: sub_name=%s, status=%d, error=%s", sub.name, exc.response.status_code, exc)
        raise
    except Exception as exc:
        logger.error("[MCP] SubMCP unexpected error: sub_name=%s, error=%s", sub.name, exc)
        raise


async def _fetch_submcp_tools(sub: SubMCPConfig, request_id: Optional[str]) -> list[dict]:
    try:
        data = await _call_submcp(sub, "tools/list", {}, request_id)
    except Exception as exc:
        logger.warning("[MCP] SubMCP tools/list failed: name=%s error=%s", sub.name, exc)
        return []
    if "error" in data:
        logger.warning("[MCP] SubMCP tools/list error: name=%s error=%s", sub.name, data.get("error"))
        return []
    tools = data.get("result", {}).get("tools", [])
    if not isinstance(tools, list):
        return []
    prefixed: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = tool.get("name")
        if not tool_name:
            continue
        updated = dict(tool)
        updated["name"] = _prefix_tool_name(sub.name, str(tool_name))
        prefixed.append(updated)
    return prefixed


async def _fetch_submcp_tool_models(sub: SubMCPConfig) -> list[dict]:
    url = f"{sub.base_url.rstrip('/')}/mcp/tools"
    try:
        async with httpx.AsyncClient(timeout=_submcp_timeout()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("[MCP] SubMCP /mcp/tools failed: name=%s error=%s", sub.name, exc)
        return []
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        return []
    prefixed: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = tool.get("name")
        if not tool_name:
            continue
        updated = dict(tool)
        updated["name"] = _prefix_tool_name(sub.name, str(tool_name))
        prefixed.append(updated)
    return prefixed


@app.on_event("startup")
async def on_startup():
    configure_gateway_logging()
    # 서버 시작 시 로그 출력
    logger.info("[MCP] Server starting up")


@app.get("/health")
async def health():
    # 상태 확인 요청 처리
    logger.info("[MCP] Health check requested")
    return {"status": "ok"}


@app.get("/mcp/RenewAuthKey")
async def renew_auth_key():
    keys = _reload_gateway_auth_from_env_file()
    logger.info("[MCP] Auth keys reloaded: key_count=%d", len(keys))
    return {"status": "ok", "keyCount": len(keys)}


@app.get("/mcp/log/{log_date}")
async def get_log(log_date: str):
    if not _is_valid_log_date(log_date):
        raise HTTPException(status_code=400, detail={"error": "invalid_log_date"})
    log_path = os.path.join(_LOG_DIR, f"{log_date}.log")
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail={"error": "log_not_found"})
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        logger.warning("[MCP] Failed to read log file: %s", exc)
        raise HTTPException(status_code=500, detail={"error": "log_read_failed"})
    return PlainTextResponse(content)


@app.post("/mcp")
async def mcp(req: MCPRequest, _: None = Depends(_require_gateway_auth)):
    # JSON-RPC 2.0 요청 수신/처리
    logger.info("[MCP] /mcp endpoint reached")
    logger.info("[MCP] Request body: %s", req.model_dump())
    logger.info("[MCP] Request received: id=%s, method=%s", req.id, req.method)
    
    try:
        if req.method == "initialize":
            logger.info("[MCP] Initialize requested")
            protocol_version = req.params.get("protocolVersion", "2024-11-05")
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "result": {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "mcp-server-py",
                        "version": "0.1.0"
                    }
                }
            }
        
        elif req.method == "notifications/initialized":
            logger.info("[MCP] Client initialized notification received")
            # Notification은 응답이 필요 없음 (id가 None)
            if req.id is None:
                return {"jsonrpc": "2.0"}
            else:
                return {"jsonrpc": "2.0", "id": req.id, "result": {}}
        
        elif req.method == "tools/list":
            logger.info("[MCP] Copilot이 도구 목록을 요청했습니다.")
            submcps = _load_submcp_configs()
            tasks = [_fetch_submcp_tools(sub, req.id) for sub in submcps]
            tool_sets = await asyncio.gather(*tasks) if tasks else []
            tools: list[dict] = []
            for tool_set in tool_sets:
                tools.extend(tool_set)
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "result": {"tools": tools}
            }
        
        elif req.method == "tools/call":
            tool_name = req.params.get("name")
            tool_arguments = req.params.get("arguments", {})
            logger.info("[MCP] Tool execute requested: tool=%s", tool_name)
            if not tool_name:
                return {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params",
                        "data": {"reason": "tool name missing"}
                    }
                }

            split = _split_tool_name(str(tool_name))
            if not split:
                return {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "error": {
                        "code": -32601,
                        "message": "Tool not found",
                        "data": {"tool": tool_name}
                    }
                }
            sub_name, actual_tool = split
            submcps = {sub.name: sub for sub in _load_submcp_configs()}
            sub = submcps.get(sub_name)
            if not sub:
                return {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "error": {
                        "code": -32601,
                        "message": "SubMCP not found",
                        "data": {"sub": sub_name}
                    }
                }

            try:
                data = await _call_submcp(
                    sub,
                    "tools/call",
                    {"name": actual_tool, "arguments": tool_arguments},
                    req.id,
                )
            except Exception as exc:
                logger.warning("[MCP] SubMCP call failed: name=%s error=%s", sub.name, exc)
                return {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "error": {
                        "code": -32603,
                        "message": "SubMCP call failed",
                        "data": {"sub": sub.name}
                    }
                }

            if "error" in data:
                return {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "error": data.get("error")
                }
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "result": data.get("result", {})
            }
        
        else:
            logger.warning("[MCP] Unknown method: %s", req.method)
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": {"method": req.method}
                }
            }
    
    except Exception as e:
        logger.error("[MCP] Error processing request: %s", str(e))
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": {"error": str(e)}
            }
        }


@app.get("/mcp/tools")
async def list_tools(_: None = Depends(_require_gateway_auth)):
    # 도구 목록 샘플 제공
    logger.info("[MCP] Tool list requested")
    submcps = _load_submcp_configs()
    tasks = [_fetch_submcp_tool_models(sub) for sub in submcps]
    tool_sets = await asyncio.gather(*tasks) if tasks else []
    tools: list[dict] = []
    for tool_set in tool_sets:
        tools.extend(tool_set)
    logger.info("[MCP] Tool list returned: count=%d", len(tools))
    return {"tools": tools}


@app.post("/mcp/tool/execute")
async def execute_tool_handler(req: ToolExecuteRequest, _: None = Depends(_require_gateway_auth)):
    # 도구 실행 요청 처리
    logger.info("[MCP] Tool execute requested: tool=%s", req.tool)
    split = _split_tool_name(req.tool)
    if not split:
        logger.warning("[MCP] Tool not found: tool=%s", req.tool)
        raise HTTPException(status_code=404, detail={"error": "tool_not_found", "tool": req.tool})
    sub_name, actual_tool = split
    submcps = {sub.name: sub for sub in _load_submcp_configs()}
    sub = submcps.get(sub_name)
    if not sub:
        logger.warning("[MCP] SubMCP not found: sub=%s", sub_name)
        raise HTTPException(status_code=404, detail={"error": "submcp_not_found", "sub": sub_name})
    try:
        data = await _call_submcp(
            sub,
            "tools/call",
            {"name": actual_tool, "arguments": req.input},
            None,
        )
    except Exception as exc:
        logger.warning("[MCP] SubMCP call failed: name=%s error=%s", sub.name, exc)
        raise HTTPException(status_code=502, detail={"error": "submcp_call_failed", "sub": sub.name})
    if "error" in data:
        raise HTTPException(status_code=502, detail={"error": "submcp_error", "detail": data.get("error")})
    logger.info("[MCP] Tool executed: tool=%s", req.tool)
    return {"tool": req.tool, "output": data.get("result", {})}
