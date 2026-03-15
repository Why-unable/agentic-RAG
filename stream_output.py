import sys
import asyncio
from rich.console import Console
from utils import load_subagents, display_stream_messages, display_stream_updates, display_stream_custom
from agent import get_main_agent, get_thread_id

console = Console()


async def main():
    console.print()
    console.print("[bold blue]高级研究代理启动 (输入 'quit' 或 'exit' 退出)[/]")
    console.print()

    agent = get_main_agent()
    # 为长对话赋予个会话ID，因为启用了 Checkpointer，同ID将继承上下文
    config = {"configurable": {"thread_id": get_thread_id()}}

    # 使用 while 循环来实现多轮对话交互
    while True:
        # 获取用户在终端的输入
        try:
            task = console.input("\n[bold cyan]👤 你的问题:[/] ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]已退出[/]")
            break

        # 处理退出命令
        if task.lower().strip() in ['quit', 'exit']:
            console.print("[green]再见！[/]")
            break

        # 防止空输入触发异常
        if not task.strip():
            continue

        console.print("\n[dim]--- Agent 开始工作 ---[/]\n")

        last_source = ""

        # 【核心1】开启三大流模式以及子图穿透
        async for event in agent.astream(
            {"messages": [("user", task)]},
            config=config,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
        ):
            namespace, mode, data = event

            # Identify source: "main" or the subagent namespace segment
            is_subagent = any(s.startswith("tools:") for s in namespace)
            source = next((s for s in namespace if s.startswith(
                "tools:")), "main") if is_subagent else "main"
            if source != last_source:
                print(
                    f"\n=================[{source}]=================", end="")
                last_source = source

            if mode == "messages":
                display_stream_messages(namespace, data)
            elif mode == "updates":
                display_stream_updates(namespace, data)
            elif mode == "custom":
                display_stream_custom(namespace, data)
            else:
                console.print(f"[red]未知流模式 {mode}，原始事件: {event}[/]")

        console.print("\n\n[dim]--- 本轮结束 ---[/]")

if __name__ == "__main__":
    asyncio.run(main())
