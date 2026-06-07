"""Lead Agent 中间件集合：包含工具错误处理、沙箱审计、循环检测、记忆/上传等。

各中间件在 ``_build_middlewares`` 中按固定顺序组装到 Lead Agent 链中，
具体顺序与适用条件见 ``backend/CLAUDE.md`` 中间件链章节。
"""
