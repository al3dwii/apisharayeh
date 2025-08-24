from __future__ import annotations

def echo(ctx, text: str) -> dict:
    """
    Minimal narration op used by the authoring flow.
    Emits a 'partial' event with {type:'narration', text}.
    """
    ctx.emit("partial", {"type": "narration", "text": str(text)})
    return {"ok": True}

echo.required_permissions = set()
