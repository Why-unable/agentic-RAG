import os
import time
import requests
import httpx

# v3 版本配置
COZE_API_KEY = "pat_qwFHtaFir4bDU2IptATIDj0d091D1CsiXY1ktAhMQRvYHwstns3cZ78XVQq7mP3b"
BOT_ID = "7610409939835912227"
BASE_URL = "https://api.coze.cn/v3/chat"


@tool(parse_docstring=True)
def fetch_knowledge(question: str) -> str:
    """Fetch answers from an external knowledge base for questions not found in local files.

    Use this tool ONLY after you have exhausted the local file search (ls, grep, read_file)
    and failed to find sufficient information. This queries a broader external knowledge source.

    Args:
        question: The specific question to ask the external knowledge base.

    Returns:
        The answer retrieved from the external knowledge base.
    """
    try:
        # Create logs directory if it doesn't exist
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Append the question to logs/question.md
        with open(os.path.join(log_dir, "question.md"), "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            f.write(f"\n\n## Question logged at {timestamp}\n{question}")
    except Exception as e_log:
        print(f"Warning: Failed to log question: {e_log}")

    headers = {
        "Authorization": f"Bearer {COZE_API_KEY}",
        "Content-Type": "application/json"
    }

    # 1. 启动对话 (Launch Chat)
    # https://www.coze.cn/docs/developer_guides/chat_v3
    chat_url = "https://api.coze.cn/v3/chat"
    payload = {
        "bot_id": BOT_ID,
        "user_id": "test_user_001",
        "stream": False,
        "auto_save_history": True,
        "additional_messages": [
            {
                "role": "user",
                "content": question,
                "content_type": "text"
            }
        ]
    }

    try:
        # Step 1: Create Chat
        print(f"Creating chat with question: {question}")
        response = requests.post(
            chat_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()

        # Debug log
        # print(f"Coze API create chat response: {json.dumps(result, indent=2)}")

        if result["code"] != 0:
            return f"错误：创建对话失败 {result['msg']}"

        chat_data = result["data"]
        chat_id = chat_data["id"]
        conversation_id = chat_data["conversation_id"]
        status = chat_data["status"]

        print(f"Chat created. ID: {chat_id}, Status: {status}")

        # Step 2: Poll for completion
        # 如果初始返回不是 completed，需要轮询状态
        if status == "created" or status == "in_progress":
            max_retries = 20
            for i in range(max_retries):
                time.sleep(2)  # Wait 2 seconds

                retrieve_url = f"https://api.coze.cn/v3/chat/retrieve?chat_id={chat_id}&conversation_id={conversation_id}"
                poll_response = requests.get(
                    retrieve_url, headers=headers, timeout=30)
                poll_result = poll_response.json()

                if poll_result["code"] != 0:
                    return f"轮询状态错误: {poll_result['msg']}"

                status = poll_result["data"]["status"]
                # print(f"Polling attempt {i+1}: status={status}")

                if status == "completed":
                    break
                elif status == "failed":
                    return f"对话失败: {poll_result['data'].get('last_error', '未知错误')}"
                elif status == "canceled":
                    return "对话被取消"
            else:
                return "等待对话完成超时 (40s)"

        # Step 3: Fetch messages after completion
        if status == "completed":
            messages_url = f"https://api.coze.cn/v3/chat/message/list?chat_id={chat_id}&conversation_id={conversation_id}"
            msg_response = requests.get(
                messages_url, headers=headers, timeout=30)
            msg_result = msg_response.json()

            if msg_result["code"] == 0:
                messages = msg_result["data"]

                # 过滤出 bot 的回答
                bot_answers = [
                    msg for msg in messages
                    if msg["role"] == "assistant" and msg["type"] == "answer"
                ]

                # 如果有 answer 类型的消息，返回最后一条
                if bot_answers:
                    answer_text = bot_answers[-1]["content"]
                    try:
                        # Create logs directory if it doesn't exist
                        log_dir = "logs"
                        if not os.path.exists(log_dir):
                            os.makedirs(log_dir)

                        # Append the question to logs/question.md
                        with open(os.path.join(log_dir, "Q-A.md"), "a", encoding="utf-8") as f:
                            timestamp = time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime())
                            f.write(
                                f"\n\n## answer to:\n{question}\nlogged at {timestamp}\n{answer_text}")

                        return answer_text
                    except Exception as e_log:
                        print(f"Warning: Failed to log answer: {e_log}")

                # Fallback: 返回任何 assistant 消息
                assistant_msgs = [
                    msg for msg in messages if msg["role"] == "assistant"]
                if assistant_msgs:
                    print("======="*20)
                    return assistant_msgs[-1]["content"]

                return "错误：对话已完成但未找到助手的回复内容"
            else:
                return f"获取消息列表错误: {msg_result['msg']}"
        else:
            return f"对话未正常完成，最终状态: {status}"

    except Exception as e:
        return f"请求异常：{str(e)}"
