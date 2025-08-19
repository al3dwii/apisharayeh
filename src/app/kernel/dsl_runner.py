from __future__ import annotations
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Callable, Optional
from .errors import ProblemDetails

# Patterns
_REF_RE = re.compile(r"^@([A-Za-z_][A-Za-z0-9_]*)$")          # whole-string @ref
_MUSTACHE_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")              # {{ expr }}
_AT_IN_EXPR_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")      # @ref inside expr (rough)

# ---------- helpers ----------

def _random_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"

def _truthy(v: Any) -> bool:
    return not (v is None or v is False or v == "" or v == 0)

class _Dot:
    """
    Small wrapper to allow attribute access (obj.key) over dicts/lists recursively.
    Missing keys return None.
    """
    __slots__ = ("_v",)

    def __init__(self, v: Any):
        self._v = v

    def __getattr__(self, name: str) -> Any:
        v = self._v
        if isinstance(v, dict):
            return _dot(v.get(name))
        raise AttributeError(name)

    def __getitem__(self, k):
        v = self._v
        if isinstance(v, (dict, list, tuple)):
            try:
                return _dot(v[k])
            except Exception:
                return None
        return None

    def __iter__(self):
        v = self._v
        if isinstance(v, (list, tuple)):
            for x in v:
                yield _dot(x)
        elif isinstance(v, dict):
            for k in v:
                yield k

    def __repr__(self):
        return f"_Dot({self._v!r})"

def _dot(v: Any) -> Any:
    if isinstance(v, dict):
        return _Dot({k: v[k] for k in v})
    if isinstance(v, (list, tuple)):
        return _Dot(list(v))
    return v

# ---------- eval/interpolation ----------

@dataclass
class EvalContext:
    inputs: Dict[str, Any]
    vars: Dict[str, Any]
    scope: Dict[str, Any]
    helpers: Dict[str, Callable[..., Any]]

def _safe_eval(expr: str, ev: EvalContext) -> Any:
    # Replace @name with __ref("name")
    expr2 = _AT_IN_EXPR_RE.sub(r'__ref("\1")', expr)

    def __ref(name: str) -> Any:
        return ev.scope.get(name)

    env = {
        "__builtins__": {},
        # Wrap dicts so `inputs.project_id` works
        "inputs": _dot(ev.inputs),
        "vars": _dot(ev.vars),
        "env": {},
        "random_id": _random_id,
        "url": ev.helpers.get("url", lambda x: x),
        "__ref": __ref,
        "len": len,
        "int": int,
        "str": str,
        "bool": bool,
        "True": True,
        "False": False,
        "None": None,
    }
    try:
        return eval(expr2, env, {})
    except Exception as e:
        raise ProblemDetails(
            title="DSL eval error",
            detail=f"expr={expr!r} -> {e}",
            code="E_DSL_EVAL",
            status=400,
        )

def _apply_filters(value: Any, filters: List[str], ev: EvalContext) -> Any:
    for f in filters:
        f = f.strip()
        if not f:
            continue
        if f.startswith("default(") and f.endswith(")"):
            arg_expr = f[len("default("):-1].strip()
            if value is None or (isinstance(value, str) and value == ""):
                value = _safe_eval(arg_expr, ev)
        else:
            # Unknown filter ignored
            pass
    return value

def _eval_mustache(expr: str, ev: EvalContext) -> Any:
    parts = [p.strip() for p in expr.split("|", 1)]
    base = parts[0]
    filters = []
    if len(parts) == 2:
        filters = [parts[1]]
    val = _safe_eval(base, ev)
    if filters:
        val = _apply_filters(val, filters, ev)
    return val

def interpolate(value: Any, ev: EvalContext) -> Any:
    try:
        if isinstance(value, str):
            m = _REF_RE.match(value)
            if m:
                return ev.scope.get(m.group(1))
            if "{{" in value and "}}" in value:
                def repl(match):
                    inner = match.group(1)
                    res = _eval_mustache(inner, ev)
                    return "" if res is None else str(res)
                return _MUSTACHE_RE.sub(repl, value)
            return value
        if isinstance(value, list):
            return [interpolate(v, ev) for v in value]
        if isinstance(value, dict):
            return {k: interpolate(v, ev) for k, v in value.items()}
        return value
    except ProblemDetails:
        raise
    except Exception as e:
        raise ProblemDetails(
            title="DSL interpolate error",
            detail=str(e),
            code="E_DSL_INTERP",
            status=400,
        )

# ---------- Runner ----------

@dataclass
class NodeResult:
    id: str
    outputs: Dict[str, Any] = field(default_factory=dict)

class DSLRunner:
    def __init__(self, toolrouter, ctx):
        self.tr = toolrouter
        self.ctx = ctx
        self.scope: Dict[str, Any] = {}
        self.vars: Dict[str, Any] = {}
        self.inputs: Dict[str, Any] = {}
        self.node_status: Dict[str, str] = {}

    def _helper_url(self, path_or_paths: Any) -> Any:
        if isinstance(path_or_paths, list):
            return [self.ctx.url_for_item(p) if hasattr(self.ctx, "url_for_item") else self.ctx.url_for(p) for p in path_or_paths]
        return self.ctx.url_for(path_or_paths)

    def compute_vars(self, raw_vars: Dict[str, Any], inputs: Dict[str, Any]):
        self.inputs = inputs
        ev = EvalContext(inputs=inputs, vars=self.vars, scope=self.scope, helpers={"url": self._helper_url})
        try:
            for k, v in (raw_vars or {}).items():
                self.vars[k] = interpolate(v, ev)
        except ProblemDetails:
            raise
        except Exception as e:
            raise ProblemDetails(
                title="DSL vars error",
                detail=str(e),
                code="E_DSL_VARS",
                status=400,
            )

    def _assign_out(self, out_spec: Dict[str, Any], result: Dict[str, Any]):
        for res_key, refname in (out_spec or {}).items():
            if isinstance(refname, str) and refname.startswith("@"):
                scope_name = refname[1:]
                self.scope[scope_name] = result.get(res_key)

    def _ensure_needs(self, node: Dict[str, Any]):
        needs = node.get("needs") or []
        for nid in needs:
            if self.node_status.get(nid) != "succeeded":
                raise ProblemDetails(
                    title="Dependency not satisfied",
                    detail=f"Node '{node.get('id')}' needs '{nid}'",
                    code="E_NEEDS",
                    status=400,
                )

    def _exec_op_node(self, node: Dict[str, Any]) -> NodeResult:
        nid = node.get("id") or f"node_{len(self.node_status)+1}"
        self._ensure_needs(node)
        raw_in = node.get("in") or {}
        ev = EvalContext(inputs=self.inputs, vars=self.vars, scope=self.scope, helpers={"url": self._helper_url})
        resolved_in = interpolate(raw_in, ev)

        op = node.get("op")
        if not isinstance(op, str):
            raise ProblemDetails(title="Invalid node", detail="op must be string", code="E_NODE", status=400)
        try:
            result = self.tr.call(op, self.ctx, **resolved_in) or {}
        except ProblemDetails:
            raise
        except Exception as e:
            raise ProblemDetails(
                title="Op execution error",
                detail=f"op={op} node={nid} -> {e}",
                code="E_OP_EXEC",
                status=500,
            )

        self._assign_out(node.get("out") or {}, result)
        self.node_status[nid] = "succeeded"
        return NodeResult(id=nid, outputs=result)

    def _exec_sequence(self, steps: List[Dict[str, Any]]):
        for sub in steps:
            self.exec_node(sub)

    def _exec_switch(self, when: Dict[str, Any]):
        ev = EvalContext(inputs=self.inputs, vars=self.vars, scope=self.scope, helpers={"url": self._helper_url})
        matched = False
        for cond, block in (when or {}).items():
            if cond.strip().startswith("{{"):
                expr = cond.strip()[2:-2]
            else:
                expr = cond
            ok = _truthy(_eval_mustache(expr, ev))
            if ok:
                matched = True
                if isinstance(block, dict) and "do" in block and block.get("op") == "sequence":
                    self._exec_sequence(block["do"])
                elif isinstance(block, dict):
                    self.exec_node(block)
                else:
                    raise ProblemDetails(title="Invalid switch block", detail=str(block), code="E_SWITCH", status=400)
                break
        # no match => no-op

    def exec_node(self, node: Dict[str, Any]) -> Optional[NodeResult]:
        if node.get("op") == "switch":
            self._exec_switch(node.get("when") or {})
            nid = node.get("id") or f"node_{len(self.node_status)+1}"
            self.node_status[nid] = "succeeded"
            return None
        if node.get("op") == "sequence":
            self._exec_sequence(node.get("do") or [])
            nid = node.get("id") or f"node_{len(self.node_status)+1}"
            self.node_status[nid] = "succeeded"
            return None
        return self._exec_op_node(node)

    def run(self, flow: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.compute_vars(flow.get("vars") or {}, inputs)
            for node in flow.get("nodes") or []:
                self.exec_node(node)
            ev = EvalContext(inputs=self.inputs, vars=self.vars, scope=self.scope, helpers={"url": self._helper_url})
            out_spec = flow.get("outputs") or {}
            resolved = {}
            for k, v in out_spec.items():
                resolved[k] = interpolate(v, ev)
            return resolved
        except ProblemDetails:
            raise
        except Exception as e:
            raise ProblemDetails(
                title="DSL execution error",
                detail=str(e),
                code="E_DSL",
                status=500,
            )
