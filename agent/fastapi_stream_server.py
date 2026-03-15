from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uuid
from agent import get_main_agent, get_thread_id
from utils import stream_agent_to_openai_format

app = FastAPI(title="LangGraph Stream API", version="0.1.0")
# uvicorn fastapi_stream_server:app --reload --port 8001
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatCompletionRequest(BaseModel):
    messages: list
    model: str = "default-model"
    stream: bool = False


# @app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    # Extract the user's latest message
    user_message = next((msg["content"] for msg in reversed(
        req.messages) if msg["role"] == "user"), "")

    agent = get_main_agent()
    # Unique thread ID per request or session
    thread_id = get_thread_id()
    config = {"configurable": {"thread_id": thread_id}}

    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"

    if req.stream:
        return StreamingResponse(
            stream_agent_to_openai_format(
                agent, user_message, config, chunk_id),
            media_type="text/event-stream"
        )
    else:
        # Non-streaming implementation can be added if required, but focusing on streaming API
        return {"error": "Only streaming is supported currently"}
