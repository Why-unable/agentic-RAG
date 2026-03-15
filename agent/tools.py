import os
import asyncio
from typing import Literal
from langchain_core.tools import tool
from dotenv import load_dotenv
import requests
import time
import httpx
from langchain_core.tools import InjectedToolArg, tool
from tavily import TavilyClient
from typing_extensions import Annotated, Literal
from markdownify import markdownify
from chroma_tools.query_chroma import retrieve_by_question

load_dotenv()


@tool
def search_local_docs(query: str, top_k: int = 3) -> str:
    """
    Search the local vectorized knowledge base for information.

    Args:
        query: The search query string.
        top_k: Number of relevant document chunks to return.

    Returns:
        The text chunks retrieved from the local knowledge base.
    """
    # TODO: This is a placeholder shell. Real implementation will be supplemented later.
    return f"[Mock] Local knowledge base results for '{query}': No relevant information found."


@tool
def fetch_knowledge(query: str) -> str:
    """
    Fetch knowledge from an external API based on the query.

    Args:
        query: The search query string.
    Returns:
        The response from the external API.
        It is a list of dictionaries, each containing 'content' and 'metadata' keys.
    """
    return retrieve_by_question(query)


tavily_client = TavilyClient()


def fetch_webpage_content(url: str, timeout: float = 10.0) -> str:
    """Fetch and convert webpage content to markdown.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Webpage content as markdown
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return markdownify(response.text)
    except Exception as e:
        return f"Error fetching content from {url}: {str(e)}"


async def fetch_webpage_content_async(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = 10.0,
) -> str:
    """Fetch and convert webpage content to markdown asynchronously."""
    try:
        response = await client.get(url, timeout=timeout)
        response.raise_for_status()
        return markdownify(response.text)
    except Exception as e:
        return f"Error fetching content from {url}: {str(e)}"


@tool(parse_docstring=True)
def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 1,
    topic: Annotated[
        Literal["general", "news", "finance"], InjectedToolArg
    ] = "general",
) -> str:
    """Search the web for information on a given query.

    Uses Tavily to discover relevant URLs, then fetches and returns full webpage content as markdown.

    Args:
        query: Search query to execute
        max_results: Maximum number of results to return (default: 1)
        topic: Topic filter - 'general', 'news', or 'finance' (default: 'general')

    Returns:
        Formatted search results with full webpage content
    """
    print("searchTavily")
    try:
        # Create logs directory if it doesn't exist
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Append the question to logs/query.md
        with open(os.path.join(log_dir, "query.md"), "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            f.write(f"\n\n## Query logged at {timestamp}\n{query}")
    except Exception as e_log:
        print(f"Warning: Failed to log query: {e_log}")

    # Use Tavily to discover URLs
    search_results = tavily_client.search(
        query,
        max_results=max_results,
        topic=topic,
    )

    # Fetch full content for each URL
    result_texts = []
    for result in search_results.get("results", []):
        url = result["url"]
        title = result["title"]

        # Fetch webpage content
        content = fetch_webpage_content(url)

        result_text = f"""## {title}
**URL:** {url}

{content}

---
"""
        result_texts.append(result_text)

    # Format final response
    response = f"""🔍 Found {len(result_texts)} result(s) for '{query}':

{chr(10).join(result_texts)}"""

    return response


@tool(parse_docstring=True)
async def tavily_search_batch(
    queries: list[str],
    max_results: int = 3,
    topic: Literal["general", "news", "finance"] = "general",
    max_concurrency: int = 4,
) -> str:
    """Search the web for multiple queries in parallel.

    Uses Tavily to discover URLs for each query, then fetches webpage content concurrently.

    Args:
        queries: List of search queries to execute in parallel
        max_results: Maximum Tavily results per query (default: 3)
        topic: Topic filter - 'general', 'news', or 'finance' (default: 'general')
        max_concurrency: Max concurrent query-level tasks (default: 4)

    Returns:
        Aggregated formatted results for all queries
    """
    clean_queries = [q.strip()
                     for q in queries if isinstance(q, str) and q.strip()]
    if not clean_queries:
        return "No valid queries provided."

    try:
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        with open(os.path.join(log_dir, "query.md"), "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            f.write(
                "\n\n## Batch query logged at "
                f"{timestamp}\n"
                f"{chr(10).join(f'- {q}' for q in clean_queries)}"
            )
    except Exception as e_log:
        print(f"Warning: Failed to log batch query: {e_log}")

    query_semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def run_single_query(query: str) -> str:
        async with query_semaphore:
            search_results = await asyncio.to_thread(
                tavily_client.search,
                query,
                max_results=max_results,
                topic=topic,
            )

            results = search_results.get("results", [])
            if not results:
                return f"## Query: {query}\nNo results found.\n"

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }

            async with httpx.AsyncClient(headers=headers) as client:
                page_tasks = [
                    fetch_webpage_content_async(result["url"], client)
                    for result in results
                ]
                page_contents = await asyncio.gather(*page_tasks, return_exceptions=True)

            parts = [f"## Query: {query}"]
            for result, page_content in zip(results, page_contents):
                title = result.get("title", "Untitled")
                url = result.get("url", "")
                if isinstance(page_content, Exception):
                    content = f"Error fetching content from {url}: {str(page_content)}"
                else:
                    content = page_content

                parts.append(
                    f"## {title}\n"
                    f"**URL:** {url}\n\n"
                    f"{content}\n\n"
                    "---"
                )

            return "\n".join(parts)

    query_tasks = [run_single_query(query) for query in clean_queries]
    sections = await asyncio.gather(*query_tasks, return_exceptions=True)

    formatted_sections = []
    for query, section in zip(clean_queries, sections):
        if isinstance(section, Exception):
            formatted_sections.append(
                f"## Query: {query}\nError: {str(section)}")
        else:
            formatted_sections.append(section)

    return (
        f"🔍 Batch search finished for {len(clean_queries)} querie(s).\n\n"
        + "\n\n".join(formatted_sections)
    )


def get_all_tools():
    """Return a dictionary of all available tools for easy lookup in subagents."""
    return {
        "search_local_docs": search_local_docs,
        "fetch_knowledge": fetch_knowledge,
        "tavily_search": tavily_search,
        "tavily_search_batch": tavily_search_batch,
    }
