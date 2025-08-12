from app.agents.loop import build_loop
from app.tools.echo import echo
from app.services.llm import get_chat
from app.agents.tracked import TrackedLLM

class EchoAgent:
    @classmethod
    def build(cls, redis, tenant_id: str):
        base = get_chat("default", temperature=0)
        tracked = TrackedLLM(base, tenant_id, "gpt-4o-mini")
        return build_loop(tracked, [echo], should_retry=lambda s: False)
