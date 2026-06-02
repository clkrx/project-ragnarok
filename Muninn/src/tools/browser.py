import os

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load .env so SERPER_API_KEY is available even when this module is used
# outside the web server (e.g. from the CLI).
load_dotenv()

SERPER_ENDPOINT = "https://google.serper.dev/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Cap extracted page text so a single fetch can't blow up the context window.
MAX_PAGE_CHARS = 8000


WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "Search the web via the Serper.dev Google Search API. "
        "Returns a list of result titles, links, and snippets. "
        "Use this to find current information, then optionally call "
        "'fetch_page' to read a specific result in full."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "num_results": {
                "type": "integer",
                "description": "How many results to return (default 5, max 10).",
            },
        },
        "required": ["query"],
    },
}


FETCH_PAGE_TOOL = {
    "name": "fetch_page",
    "description": (
        "Fetch a web page by URL and return its readable text content "
        "(scripts, styles, and navigation stripped out). "
        "Long pages are truncated. Use after 'web_search' to read a result, "
        "or directly when you already have a URL."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (including http:// or https://).",
            },
        },
        "required": ["url"],
    },
}


def web_search(query: str, num_results: int = 5) -> dict:
    """Run a Google search through Serper.dev and return organic results."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return {"ok": False, "error": "SERPER_API_KEY is not set in the environment."}

    num_results = max(1, min(int(num_results), 10))
    try:
        response = requests.post(
            SERPER_ENDPOINT,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num_results},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {"ok": False, "error": f"Search request failed: {e}"}

    results = []
    for item in data.get("organic", [])[:num_results]:
        results.append(
            {
                "title": item.get("title"),
                "link": item.get("link"),
                "snippet": item.get("snippet"),
            }
        )

    answer_box = data.get("answerBox", {})
    return {
        "ok": True,
        "query": query,
        "answer": answer_box.get("answer") or answer_box.get("snippet"),
        "results": results,
    }


def fetch_page(url: str) -> dict:
    """Fetch a URL and return its readable text, with markup stripped out."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        return {"ok": False, "error": f"Failed to fetch page: {e}"}

    soup = BeautifulSoup(response.text, "html.parser")

    # Drop elements that never contain readable content.
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else None
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )

    truncated = len(text) > MAX_PAGE_CHARS
    if truncated:
        text = text[:MAX_PAGE_CHARS]

    return {
        "ok": True,
        "url": url,
        "title": title,
        "truncated": truncated,
        "text": text,
    }
