from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any

app = FastAPI(title="tool-registry")

class Tool(BaseModel):
    id: str
    schema_in: Dict[str, Any] = Field(default_factory=dict)
    schema_out: Dict[str, Any] = Field(default_factory=dict)
    retryable: bool = True
    cost_hint: Dict[str, Any] = Field(default_factory=dict)

TOOLS: Dict[str, Tool] = {
    "echo": Tool(
        id="echo",
        schema_in={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    )
}

@app.get("/tools/{tool_id}", response_model=Tool)
def get_tool(tool_id: str):
    if tool_id not in TOOLS:
        raise HTTPException(404, "unknown tool")
    return TOOLS[tool_id]
