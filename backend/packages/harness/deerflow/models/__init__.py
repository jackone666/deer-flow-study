"""DeerFlow 模型子包。

提供聊天模型（chat model）创建与提供商（provider）相关能力：
- 工厂函数 ``create_chat_model`` 根据配置/凭据创建 LangChain 兼容的 chat model 实例
- 各 provider 模块（OpenAI、Claude、vLLM、MiniMax、MindIE 等）封装对应后端的接入
- ``credential_loader``：统一加载模型凭据
- ``patched_*``：针对部分国产/代理后端的兼容性补丁
"""

from .factory import create_chat_model

__all__ = ["create_chat_model"]
