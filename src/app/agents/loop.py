from typing import Any, Dict, List, Callable, Optional, Tuple
import json, inspect, uuid
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from app.services.events import emit_event

def _tool_name(t: Any) -> str:
    return getattr(t, "name", None) or getattr(t, "__name__", None) or t.__class__.__name__

def _safe_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except TypeError:
        return json.dumps(str(obj), ensure_ascii=False)

def _extract_toolcall(tc: Any) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    Normalize tool call structures coming from OpenAI/LangChain:
    returns (id, name, args_dict)
    """
    # dict-like (OpenAI)
    if isinstance(tc, dict):
        name = tc.get("name") or (tc.get("function") or {}).get("name")
        raw = tc.get("args") or (tc.get("function") or {}).get("arguments") or "{}"
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except Exception:
                args = {}
        else:
            args = raw or {}
        return tc.get("id"), name, args

    # object-like (LangChain ToolCall)
    name = getattr(tc, "name", None) or getattr(getattr(tc, "function", None), "name", None)
    raw = getattr(tc, "args", None) or getattr(getattr(tc, "function", None), "arguments", None) or "{}"
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except Exception:
            args = {}
    else:
        args = raw or {}
    tc_id = getattr(tc, "id", None) or str(uuid.uuid4())
    return tc_id, name, args

async def _call_tool(fn: Any, args: Dict[str, Any]) -> Any:
    # LangChain BaseTool variants
    if hasattr(fn, "ainvoke"):
        return await fn.ainvoke(args)
    if hasattr(fn, "invoke"):
        return fn.invoke(args)
    # Legacy BaseTool run/arun
    if hasattr(fn, "arun"):
        return await fn.arun(**args)
    if hasattr(fn, "run"):
        return fn.run(**args)
    # Plain async/sync callables
    res = fn(**args)
    return (await res) if inspect.isawaitable(res) else res

def build_loop(
    llm,
    tools: List[Any],
    should_retry: Optional[Callable[[Exception, int], bool]] = None,
    system: Optional[str] = None,
    max_steps: int = 12,
):
    tool_map: Dict[str, Any] = {_tool_name(t): t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    class Runner:
        async def arun(self, state: dict):
            tenant_id = state.get("tenant_id", "unknown")
            job_id = state.get("job_id", "ad-hoc")

            msgs = []
            if system:
                msgs.append(SystemMessage(content=system))

            user_payload = {k: v for k, v in state.items() if k not in ("tenant_id", "job_id")}
            msgs.append(HumanMessage(content=f"Input:\n{json.dumps(user_payload, ensure_ascii=False)}"))

            attempt = 0
            last_tool_result = None

            for step in range(1, max_steps + 1):
                # PLAN
                await emit_event(tenant_id, job_id, "plan", "started", {"step": step})

                try:
                    ai = await llm_with_tools.ainvoke(msgs)
                except Exception as exc:
                    attempt += 1
                    if should_retry and should_retry(exc, attempt):
                        continue
                    raise

                # Append the model turn before handling tool calls
                msgs.append(ai)

                tool_calls = getattr(ai, "tool_calls", None)
                if tool_calls is None and hasattr(ai, "additional_kwargs"):
                    tool_calls = ai.additional_kwargs.get("tool_calls")

                await emit_event(
                    tenant_id,
                    job_id,
                    "plan",
                    "finished",
                    {
                        "step": step,
                        "assistant": getattr(ai, "content", "") or "",
                        "tool_calls": tool_calls,
                    },
                )

                if tool_calls:
                    await emit_event(tenant_id, job_id, "act", "started", {"step": step, "tool_calls": tool_calls})
                    # ACT
                    for tc in tool_calls:
                        tc_id, name, args = _extract_toolcall(tc)
                        fn = tool_map.get(name or "")

                        if not fn:
                            err = {"error": f"Unknown tool '{name}'"}
                            msgs.append(ToolMessage(content=_safe_dumps(err), tool_call_id=tc_id or "", name=name or "tool"))
                            await emit_event(tenant_id, job_id, "act", "progress", {"step": step, "tool": name, "error": err})
                            continue

                        try:
                            res = await _call_tool(fn, args)
                        except Exception as e:
                            res = {"error": str(e)}

                        last_tool_result = res
                        msgs.append(ToolMessage(content=_safe_dumps(res), tool_call_id=tc_id or "", name=name))
                        await emit_event(
                            tenant_id,
                            job_id,
                            "act",
                            "progress",
                            {"step": step, "tool": name, "args": args, "result": res},
                        )

                    # Let the model react to tool outputs
                    continue

                # No tool calls => final answer
                final = last_tool_result if last_tool_result is not None else (getattr(ai, "content", "") or "")
                await emit_event(tenant_id, job_id, "act", "finished", {"step": step, "result": final})
                return final

            # Max steps exceeded
            err = {"error": "max_steps_exceeded", "max_steps": max_steps}
            await emit_event(tenant_id, job_id, "act", "finished", err)
            return err

    return Runner()
