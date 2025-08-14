import yaml

def _ref(v, state):
    if isinstance(v, str) and v.startswith("@"):
        path = v[1:]
        if "." in path:
            node, key = path.split(".", 1)
            return state[node][key]
        return state[path]
    if isinstance(v, str) and "{{" in v:
        for k, val in state.get("inputs", {}).items():
            v = v.replace(f"{{{{inputs.{k}}}}}", str(val))
    return v

def run_dsl_flow(flow_path: str, inputs: dict, ctx):
    flow = yaml.safe_load(open(flow_path, "r", encoding="utf-8"))
    state = {"inputs": inputs}
    for node in flow.get("nodes", []):
        op = node["op"]
        args = {k: _ref(v, state) for k, v in (node.get("in") or {}).items()}
        handler = ctx.tools.resolve(op)
        await_emit = getattr(ctx.events, "emit", None)
        if await_emit:
            ctx.events.emit(ctx.tenant_id, ctx.job_id, "node", "started", {"id": node["id"], "op": op})
        out = handler(**args)
        state[node["id"]] = out
        if await_emit:
            ctx.events.emit(ctx.tenant_id, ctx.job_id, "node", "finished", {"id": node["id"]})
    outputs = {}
    for k, v in (flow.get("outputs") or {}).items():
        outputs[k] = _ref(v, state)
    return outputs
