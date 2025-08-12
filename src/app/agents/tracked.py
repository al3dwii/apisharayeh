from typing import Any
from opentelemetry import trace
from app.services.quotas import add_usage
from app.services.costs import estimate_cost_cents

tracer = trace.get_tracer(__name__)

class TrackedLLM:
    def __init__(self, llm: Any, tenant_id: str, model_name: str):
        self._llm = llm
        self._tenant_id = tenant_id
        self._model = model_name

    async def ainvoke(self, *args, **kwargs):
        with tracer.start_as_current_span("llm.invoke") as span:
            span.set_attribute("ai.model", self._model)
            span.set_attribute("tenant.id", self._tenant_id)
            resp = await self._llm.ainvoke(*args, **kwargs)
            in_tokens = 0
            out_tokens = 0
            md = getattr(resp, "usage_metadata", None) or getattr(resp, "response_metadata", {})
            if md:
                in_tokens = md.get("input_tokens", md.get("prompt_tokens", 0)) or 0
                out_tokens = md.get("output_tokens", md.get("completion_tokens", 0)) or 0
            if not (in_tokens or out_tokens):
                content = getattr(resp, "content", "") or ""
                out_tokens = max(1, len(str(content)) // 4)
            span.set_attribute("ai.tokens.in", in_tokens)
            span.set_attribute("ai.tokens.out", out_tokens)
            cost_cents = estimate_cost_cents(self._model, in_tokens, out_tokens)
            span.set_attribute("ai.cost.cents", cost_cents)
            try:
                await add_usage(self._tenant_id, in_tokens + out_tokens, cost_cents)
            except Exception:
                pass
            return resp

    def bind_tools(self, tools):
        return TrackedLLM(self._llm.bind_tools(tools), self._tenant_id, self._model)
