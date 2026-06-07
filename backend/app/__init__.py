"""DeerFlow 应用层（App）。

应用层是 DeerFlow 后端未发布的部分（不可独立打包为 pip 包），运行在 ``app.*`` 导入前缀下。
该层依赖底层 ``deerflow-harness``（``deerflow.*``），但 harness 永远不依赖 app——此单向依赖
由 ``tests/test_harness_boundary.py`` 在 CI 中强制保证。

当前应用层主要包含：
- ``app.gateway``：暴露在 8001 端口的 FastAPI Gateway API。
- ``app.channels``：IM 平台（飞书、Slack、Telegram、钉钉、企业微信、微信、Discord）集成。
"""
