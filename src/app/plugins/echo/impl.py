from typing import Dict, Any

def run(inputs: Dict[str, Any], ctx) -> Dict[str, Any]:
    # keep it boring + observable
    msg = str(inputs["message"])
    if getattr(ctx, "events", None):
        ctx.events.emit(ctx.tenant_id, ctx.job_id, "step", "debug", {"len": len(msg)})
    return {"echoed": msg}
