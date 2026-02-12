from dataclasses import dataclass
from typing import Any, Callable, Dict, List


class ToolNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ToolSchema:
    tool_type: str
    properties: Dict[str, Dict[str, Any]]
    required: List[str]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: ToolSchema
    output_schema: ToolSchema
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


def run_echo_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    # echo 도구 실행 함수
    text = payload.get("text", "")
    return {"text": f"Echo: {text}"}


def run_uppercase_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    # uppercase 도구 실행 함수
    text = payload.get("text", "")
    return {"text": str(text).upper()}


def run_concat_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    # concat 도구 실행 함수
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = [items]
    text = "".join(str(item) for item in items)
    return {"text": text}


TOOL_REGISTRY: Dict[str, ToolDefinition] = {
    # 새 도구는 여기에 추가
    "echo": ToolDefinition(
        name="echo",
        description="Echo back the input text.",
        input_schema=ToolSchema(
            tool_type="object",
            properties={"text": {"type": "string", "description": "Text to echo"}},
            required=["text"],
        ),
        output_schema=ToolSchema(
            tool_type="object",
            properties={"text": {"type": "string", "description": "Echoed text"}},
            required=["text"],
        ),
        handler=run_echo_tool,
    ),
    "uppercase": ToolDefinition(
        name="uppercase",
        description="Uppercase the input text.",
        input_schema=ToolSchema(
            tool_type="object",
            properties={"text": {"type": "string", "description": "Text to uppercase"}},
            required=["text"],
        ),
        output_schema=ToolSchema(
            tool_type="object",
            properties={"text": {"type": "string", "description": "Uppercased text"}},
            required=["text"],
        ),
        handler=run_uppercase_tool,
    ),
    "concat": ToolDefinition(
        name="concat",
        description="Concatenate a list of items into a string.",
        input_schema=ToolSchema(
            tool_type="object",
            properties={
                "items": {"type": "array", "description": "Items to concatenate"}
            },
            required=["items"],
        ),
        output_schema=ToolSchema(
            tool_type="object",
            properties={"text": {"type": "string", "description": "Concatenated text"}},
            required=["text"],
        ),
        handler=run_concat_tool,
    ),
}


def list_tool_definitions() -> List[ToolDefinition]:
    return list(TOOL_REGISTRY.values())


def execute_tool(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # 도구 이름에 따라 실행 함수 라우팅
    tool_def = TOOL_REGISTRY.get(tool)
    if tool_def is not None:
        return tool_def.handler(payload)
    raise ToolNotFoundError(f"tool_not_found: {tool}")
