#!/usr/bin/env python
"""
lead_agent 的调试脚本。

在 VS Code 中直接以断点方式运行本文件。

要求：
    在 ``backend/`` 目录下用 ``uv run`` 启动，使 uv workspace
    能正确解析 ``deerflow-harness`` 与 ``app`` 包：

        cd backend && PYTHONPATH=. uv run python debug.py

用法：
    1. 在 ``agent.py`` 等文件中设置断点
    2. 按 F5 或使用 "Run and Debug" 面板
    3. 在终端中输入消息与 agent 交互
"""

import asyncio
import logging

from dotenv import load_dotenv

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

load_dotenv()

_LOG_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_logging(log_level: int = logging.INFO) -> None:
    """将日志路由到 ``debug.log``，使用 *log_level* 作为根 logger 与文件 handler 的初始级别。

    该函数会配置根 logger 与 ``debug.log`` 文件 handler，使日志不在交互式控制台打印。
    它是幂等的：根 logger 上已存在的 handler（例如由传递导入模块中的
    ``logging.basicConfig`` 注册的）都会被移除，使调试会话的输出只写入
    ``debug.log``。

    注意：后续由配置驱动的日志调整可能会更改具名 logger 的详细级别，但不会
    提升此处设置的根 logger 与文件 handler 阈值，因此 ``debug.log`` 的最终
    内容未必仅由本函数的 ``log_level`` 参数过滤。
    """
    root = logging.root
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(log_level)

    file_handler = logging.FileHandler("debug.log", mode="a", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    root.addHandler(file_handler)


async def main():
    """启动 lead_agent 调试会话：初始化日志、配置、agent 并进入 REPL 循环。"""
    # Install file logging first so warnings emitted while loading config do not
    # leak onto the interactive terminal via Python's lastResort handler.
    _setup_logging()

    from deerflow.config import get_app_config
    from deerflow.config.app_config import apply_logging_level

    app_config = get_app_config()
    apply_logging_level(app_config.log_level)

    # Delay the rest of the deerflow imports until *after* logging is installed
    # so that any import-time side effects (e.g. deerflow.agents starts a
    # background skill-loader thread on import) emit logs to debug.log instead
    # of leaking onto the interactive terminal via Python's lastResort handler.
    from langchain_core.messages import HumanMessage
    from langgraph.runtime import Runtime

    from deerflow.agents import make_lead_agent
    from deerflow.config.paths import get_paths
    from deerflow.mcp import initialize_mcp_tools
    from deerflow.runtime.user_context import get_effective_user_id

    # Initialize MCP tools at startup
    try:
        await initialize_mcp_tools()
    except Exception as e:
        print(f"Warning: Failed to initialize MCP tools: {e}")

    # Create agent with default config
    config = {
        "configurable": {
            "thread_id": "debug-thread-001",
            "thinking_enabled": True,
            "is_plan_mode": True,
            # Uncomment to use a specific model
            "model_name": "kimi-k2.5",
        }
    }

    runtime = Runtime(context={"thread_id": config["configurable"]["thread_id"]})
    config["configurable"]["__pregel_runtime"] = runtime

    agent = make_lead_agent(config)

    session = PromptSession(history=InMemoryHistory()) if _HAS_PROMPT_TOOLKIT else None

    print("=" * 50)
    print("Lead Agent Debug Mode")
    print("Type 'quit' or 'exit' to stop")
    print(f"Logs: debug.log (log_level={app_config.log_level})")
    if not _HAS_PROMPT_TOOLKIT:
        print("Tip: `uv sync --group dev` to enable arrow-key & history support")
    print("=" * 50)

    seen_artifacts: set[str] = set()

    while True:
        try:
            if session:
                user_input = (await session.prompt_async("\nYou: ")).strip()
            else:
                user_input = input("\nYou: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                print("Goodbye!")
                break

            # Invoke the agent
            state = {"messages": [HumanMessage(content=user_input)]}
            result = await agent.ainvoke(state, config=config)

            # Print the response
            if result.get("messages"):
                last_message = result["messages"][-1]
                print(f"\nAgent: {last_message.content}")

            # Show files presented to the user this turn (new artifacts only)
            artifacts = result.get("artifacts") or []
            new_artifacts = [p for p in artifacts if p not in seen_artifacts]
            if new_artifacts:
                thread_id = config["configurable"]["thread_id"]
                user_id = get_effective_user_id()
                paths = get_paths()
                print("\n[Presented files]")
                for virtual in new_artifacts:
                    try:
                        physical = paths.resolve_virtual_path(thread_id, virtual, user_id=user_id)
                        print(f"  - {virtual}\n    → {physical}")
                    except ValueError as exc:
                        print(f"  - {virtual}    (failed to resolve physical path: {exc})")
                seen_artifacts.update(new_artifacts)

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
