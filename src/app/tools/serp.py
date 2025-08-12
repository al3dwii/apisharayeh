import httpx, os, asyncio
from tenacity import retry, wait_fixed, stop_after_attempt
from app.tools.net import get_semaphore_for_host

_SERP_HOST = "serpapi.com"

@retry(wait=wait_fixed(1), stop=stop_after_attempt(4))
async def serp_search(query: str, k: int = 10) -> list[str]:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        return []
    sem = get_semaphore_for_host(_SERP_HOST)
    async with sem:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get("https://serpapi.com/search", params={"q": query, "api_key": key})
            r.raise_for_status()
            data = r.json().get("organic_results", [])[:k]
            return [item.get("link") for item in data if "link" in item]
