from app.agents.loop import build_loop
from app.agents.tracked import TrackedLLM
from app.services.llm import get_chat
from app.tools.doc2deck import load_pdf_text, build_pptx
from app.core.config import settings


def _should_retry(exc: Exception, attempt: int) -> bool:
    """Retry up to 2 times on transient errors."""
    transient_names = {
        "TimeoutError", "APIConnectionError", "ServiceUnavailableError",
        "RateLimitError", "ReadTimeout", "ConnectTimeout",
    }
    return attempt < 3 and (
        isinstance(exc, (TimeoutError, ConnectionError))
        or exc.__class__.__name__ in transient_names
    )

DOC2DECK_SYSTEM_HINT = (
    "You are a precise slide generator. "
    "Goal: convert a PDF into a concise presentation. "
    "ALWAYS call tools in this order: (1) load_pdf_text -> (2) build_pptx. "
    "After load_pdf_text, create a JSON outline with up to `max_slides` slides. "
    "Outline format: [{\"title\":\"...\",\"bullets\":[\"...\",\"...\"]}], bullets <= 12 words. "
    "Then call build_pptx(outline_json=STRINGIFIED_JSON, title?, theme?, output_key?)."
)

class Converter:
    """
    Agent name: 'converter'
    Usage (jobs): pack='doc2deck', agent='converter', payload={...}
    """
    @classmethod
    def build(cls, redis, tenant_id: str):
        # If OPENAI_API_KEY is missing, get_chat(..., required=False) will return None.
        # TrackedLLM should be able to handle a None llm (only tool-calls then).
        llm = get_chat("gpt-4o-mini", temperature=0.1, required=False) if settings.OPENAI_API_KEY else None
        actor = TrackedLLM(llm, tenant_id, "gpt-4o-mini")
        tools = [load_pdf_text, build_pptx]

        # Prefer the signature that supports 'system='. If not available, fall back.
        try:
            loop = build_loop(
                actor,
                tools,
                should_retry=_should_retry,
                system=DOC2DECK_SYSTEM_HINT,
            )
        except TypeError:
            loop = build_loop(
                actor,
                tools,
                should_retry=_should_retry,
            )

        return loop
