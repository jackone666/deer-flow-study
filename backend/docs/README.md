# 文档

该目录包含 DeerFlow 后端的详细文档。

## 快速链接

| 文档 | 描述 |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构概述 |
| [API.md](API.md) | 完整的 API 参考 |
| [AUTH_DESIGN.md](AUTH_DESIGN.md) | 用户认证、CSRF、每用户隔离设计 |
| [CONFIGURATION.md](CONFIGURATION.md) | 配置选项 |
| [SETUP.md](SETUP.md) | 快速设置指南 |

## 功能文档

| 文档 | 说明 |
|----------|-------------|
| [STREAMING.md](STREAMING.md) | Token 级流式设计：网关与 DeerFlowClient 路径、`stream_mode` 语义、按 ID 去重 |
| [FILE_UPLOAD.md](FILE_UPLOAD.md) | 文件上传功能 |
| [PATH_EXAMPLES.md](PATH_EXAMPLES.md) | 路径类型和使用示例 |
| [SANDBOX_MEMORY_PROFILING.md](SANDBOX_MEMORY_PROFILING.md) | 沙箱内存基线和运行时比较指南 |
| [summarization.md](summarization.md) | 上下文摘要功能 |
| [plan_mode_usage.md](plan_mode_usage.md) | 带有 TodoList 的计划模式 |
| [AUTO_TITLE_GENERATION.md](AUTO_TITLE_GENERATION.md) | 自动标题生成 |

## 开发

| 文档 | 描述 |
|----------|-------------|
| [TODO.md](TODO.md) | 计划的功能和已知问题 |

## 入门

1. **DeerFlow 新手？** 从 [SETUP.md](SETUP.md) 开始快速安装
2. **配置系统？** 请参阅 [CONFIGURATION.md](CONFIGURATION.md)
3. **了解架构？** 阅读 [ARCHITECTURE.md](ARCHITECTURE.md)
4. **构建集成？** 检查 [API.md](API.md) 以获得 API 参考

## 文档组织

```
docs/
├── README.md                  # 本文件
├── ARCHITECTURE.md            # 系统架构
├── API.md                     # API 参考
├── AUTH_DESIGN.md             # 用户认证与隔离设计
├── CONFIGURATION.md           # 配置指南
├── SETUP.md                   # 设置说明
├── FILE_UPLOAD.md             # 文件上传功能
├── PATH_EXAMPLES.md           # 路径使用示例
├── summarization.md           # 摘要功能
├── plan_mode_usage.md         # 计划模式功能
├── STREAMING.md               # Token 级流式设计
├── AUTO_TITLE_GENERATION.md   # 标题生成
├── TITLE_GENERATION_IMPLEMENTATION.md  # 标题实现细节
└── TODO.md                    # 路线图和问题
```
