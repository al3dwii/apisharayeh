from tenacity import retry, wait_exponential, stop_after_attempt
from app.services.llm import get_chat

@retry(wait=wait_exponential(min=1, max=20), stop=stop_after_attempt(5))
async def chat(messages, model="default", temperature=0.2):
    llm = get_chat(model, temperature=temperature)
    return await llm.ainvoke(messages)
