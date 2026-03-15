import json
import time
import yaml
import asyncio
from pathlib import Path
from tools import get_all_tools

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

console = Console()


def load_subagents(config_path: Path) -> list:
    """Load subagent definitions from YAML and wire up tools."""
    available_tools = get_all_tools()

    with open(config_path) as f:
        config = yaml.safe_load(f)

    subagents = []
    for name, spec in config.items():
        subagent = {
            "name": name,
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
        }
        if "model" in spec:
            subagent["model"] = spec["model"]
        if "tools" in spec:
            subagent["tools"] = [available_tools[t] for t in spec["tools"]]
        subagents.append(subagent)

    return subagents


def display_stream_messages(namespace, stream_chunk):
    token, metadata = stream_chunk

    # Identify source: "main" or the subagent namespace segment
    is_subagent = any(s.startswith("tools:") for s in namespace)
    source = next((s for s in namespace if s.startswith(
        "tools:")), "main") if is_subagent else "main"

    # Tool call chunks (streaming tool invocations)
    if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
        for tc in token.tool_call_chunks:
            if tc.get("name"):
                print(f"\nTool call: {tc['name']}\n")
            # Args stream in chunks — write them incrementally
            if tc.get("args"):
                print(tc["args"], end="", flush=True)

    # Tool results
    elif hasattr(token, 'type') and token.type == "tool":
        # print(f'lpk{token.type}, {token.type == "tool"}')
        print(
            f"\nTool result [{token.name}]: {str(token.content)[:150]}\n")

    # Regular AI content (skip tool call messages)
    # hasattr(token, 'type') and token.type == "ai" and token.content and not hasattr(token, 'tool_call_chunks')
    else:
        # print(f'kpl{token.type if hasattr(token, "type") else "text"}')
        print(token.content, end="", flush=True)

    return


def display_stream_updates(namespace, stream_chunk):
    # Main agent updates (empty namespace)
    if not namespace:
        for node_name, data in stream_chunk.items():
            if node_name == "tools":
                # Subagent results returned to main agent
                for msg in data.get("messages", []):
                    if msg.type == "tool":
                        print(f"\nSubagent complete: {msg.name}")
                        print(f"  Result: {str(msg.content)[:200]}...")
            else:
                print(f"[main agent] step: {node_name}")

    # Subagent updates (non-empty namespace)
    else:
        for node_name, data in stream_chunk.items():
            print(f"  [{namespace[0]}] step: {node_name}")

    return


def display_stream_custom(namespace, stream_chunk):
    is_subagent = any(s.startswith("tools:") for s in namespace)
    if is_subagent:
        subagent_ns = next(s for s in namespace if s.startswith("tools:"))
        print(f"[{subagent_ns}]", stream_chunk)
    else:
        print("[main]", stream_chunk)

    return


def create_sse_chunk(chunk_id: str, content: str = "", reasoning_content: str = "", finish_reason: str = None) -> str:
    """Format a string into an OpenAI-compatible SSE chunk."""
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "langgraph-agent",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": content,
                    "reasoning_content": reasoning_content
                },
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(data)}\n\n"


async def stream_agent_to_openai_format(agent, user_message: str, config: dict, chunk_id: str):
    """Generator that yields OpenAI-compatible SSE chunks from LangGraph agent streams."""
    last_source = ""
    # Start of stream
    yield create_sse_chunk(chunk_id, "")

    try:
        async for event in agent.astream(
            {"messages": [("user", user_message)]},
            config=config,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
        ):
            namespace, mode, data = event

            # Check source changes
            is_subagent = any(s.startswith("tools:") for s in namespace)
            source = next((s for s in namespace if s.startswith(
                "tools:")), "main") if is_subagent else "main"

            if source != last_source:
                yield create_sse_chunk(chunk_id, reasoning_content=f"\n=================[{source}]=================\n")
                last_source = source

            # Format different stream modes
            if mode == "messages":
                token, metadata = data
                if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
                    for tc in token.tool_call_chunks:
                        if tc.get("name"):
                            yield create_sse_chunk(chunk_id, reasoning_content=f"\nTool call: {tc['name']}\n")
                        if tc.get("args"):
                            yield create_sse_chunk(chunk_id, reasoning_content=tc["args"])
                elif hasattr(token, 'type') and token.type == "tool":
                    yield create_sse_chunk(chunk_id, reasoning_content=f"\nTool result [{token.name}]: {str(token.content)[:150]}\n")
                else:
                    if hasattr(token, 'content') and token.content:
                        # Output exactly the streamed token
                        yield create_sse_chunk(chunk_id, str(token.content))
            elif mode == "updates":
                if not namespace:
                    for node_name, node_data in data.items():
                        if node_name == "tools":
                            for msg in node_data.get("messages", []):
                                if getattr(msg, 'type', '') == "tool":
                                    yield create_sse_chunk(chunk_id, reasoning_content=f"\nSubagent complete: {msg.name}\n  Result: {str(msg.content)[:200]}...\n")
                        else:
                            yield create_sse_chunk(chunk_id, reasoning_content=f"\n[main agent] step: {node_name}\n")
                else:
                    for node_name, node_data in data.items():
                        yield create_sse_chunk(chunk_id, reasoning_content=f"\n  [{namespace[0]}] step: {node_name}\n")
            elif mode == "custom":
                if is_subagent:
                    subagent_ns = next(
                        s for s in namespace if s.startswith("tools:"))
                    yield create_sse_chunk(chunk_id, f"\n[{subagent_ns}] {str(data)}\n")
                else:
                    yield create_sse_chunk(chunk_id, f"\n[main] {str(data)}\n")

    except Exception as e:
        yield create_sse_chunk(chunk_id, f"\n[Error: {str(e)}]\n")

    # End of stream
    yield create_sse_chunk(chunk_id, "", finish_reason="stop")
    yield "data: [DONE]\n\n"
