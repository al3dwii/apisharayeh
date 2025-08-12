from typing import Any, Dict
async def echo(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": payload}
