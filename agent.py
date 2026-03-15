#!/usr/bin/env python3
from prompts import system_prompt
from deepagents.backends import FilesystemBackend
from deepagents import create_deep_agent
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import yaml
from typing import Literal
from pathlib import Path
import time
import sys
import os
import asyncio
import warnings
from langchain.chat_models import init_chat_model
from utils import load_subagents
from dotenv import load_dotenv
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

# Create the memory checkpointer
checkpointer = MemorySaver()


# Use current working directory to prevent path escape when running under LangGraph dev
data_root = os.path.join(os.getcwd(), "data")
timestamp = time.strftime("%Y%m%d-%H%M%S")

research_notes_dir = os.path.join(data_root, timestamp, "research_notes")


def get_thread_id():
    return f"thread-{timestamp}"


def backend_factory(runtime):
    """Create a composite backend with multiple storage tiers."""
    return CompositeBackend(
        default=StateBackend(runtime),
        routes={
            "/research_notes/": FilesystemBackend(
                root_dir=research_notes_dir,
                virtual_mode=True
            ),
        }
    )


model = init_chat_model(
    model="qwen-plus",
    model_provider="openai",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
    extra_body={"enable_thinking": False}
)


agent = create_deep_agent(
    model=model,
    # memory=["./AGENTS.md"],
    system_prompt=system_prompt,
    skills=["./skills/"],
    subagents=load_subagents(
        os.path.join(os.getcwd(), "subagents.yaml")),  # Custom helper
    backend=backend_factory,
    # checkpointer=checkpointer,
)


def get_main_agent():
    return agent
