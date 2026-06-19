# 阻塞 IO 检测使用和维护

本文档介绍如何使用和维护 DeerFlow 后端的阻塞 IO 检测，
以保护异步事件循环安全。

目标很明确：找出并防止同步 IO 阻塞后端异步事件循环路径。
静态检测和运行时检测互为补充，但职责不同。

## 静态检测器

静态检测器是发现工具。它扫描后端源代码，并报告可能需要人工审核的
阻塞 IO 候选调用点。

从存储库根目录运行它：

```bash
make detect-blocking-io
```

或来自 `backend/`：

```bash
make detect-blocking-io
```

报告写入：

```text
.deer-flow/blocking-io-findings.json
```

使用此输出进行审查和分类。静态发现只是候选项，
并不能证明生产环境会在运行时阻塞事件循环。当前静态规则有意保持宽泛；
添加新的静态规则之前，应优先梳理已有输出。

仅当审核发现当前检测器不可见、重复出现的高风险阻塞模式时，才添加静态规则。

## 运行时检测器

运行时检测器是 CI 回归防护。它使用 Blockbuster，
在 `app.*` 或 `deerflow.*` 下的代码在异步事件循环线程上执行阻塞 IO 时，
让聚焦测试失败。

从 `backend/` 运行它：

```bash
make test-blocking-io
```

运行时检查从已确认的生产 bug 开始，并保护这些路径不再回归。
它不能证明整个后端完全没有阻塞 IO；它只覆盖
`backend/tests/blocking_io/` 执行到的生产路径。

## 维护工作流程

使用静态检测器寻找候选项，再通过审查决定哪些异步生产路径值得在 CI 中保护。

正常的工作流程是：

1. 运行静态检测器以查找后端阻塞-IO 候选者。
2. 使用人工审查来选择高风险的生产异步路径。
3. 在 `backend/tests/blocking_io/` 中添加或更新聚焦的运行时锚点。
4. 让 CI 防止该路径倒退。

运行时检测有两个维护路径。

### 添加运行时规则

当 Blockbuster 的默认规则没有覆盖生产代码使用的常见阻塞原语时，
添加运行时规则。

规则属于：

```text
backend/tests/support/detectors/blocking_io_runtime.py
```

将它们添加到 `_PROJECT_BLOCKING_RULES`，而不是直接在各个测试中。
集中维护规则可以明确 DeerFlow 期望 Blockbuster 捕获哪些额外原语。

示例结构：

```python
import subprocess

from blockbuster import BlockBusterFunction

_PROJECT_BLOCKING_RULES = (
    (
        "subprocess.Popen.__init__",
        BlockBusterFunction(
            subprocess.Popen,
            "__init__",
            scanned_modules=["app", "deerflow"],
        ),
    ),
)
```

不要因为业务路径没有经过测试就添加运行时规则。一条规则
仅扩展了 Blockbuster 在代码运行后可以拦截的内容。

### 添加运行时锚点

当需要由 CI 保护某条高风险异步生产路径，但现有
`backend/tests/blocking_io/` 测试尚未执行到它时，添加运行时锚点。

锚点属于：

```text
backend/tests/blocking_io/
```

一个好的锚点应该：

- 调用真正的生产异步入口点。
- 避免只通过测试专用 `asyncio.to_thread` 包装器绕过阻塞面。
- 当 bug 模式是文件系统 IO 时，使用真实的本地文件系统输入。
- 仅模拟外部依赖边界，例如网络服务或
  第三方 saver 类。
- 如果未来改动把阻塞操作移回事件循环，则测试应失败。

避免仅测试低级帮助程序，除非该帮助程序是生产环境
异步入口点。运行时检查在保护生产实际执行的调用方时最有用。

## 当前运行时覆盖范围

运行时锚点保护已确认的阻塞 IO bug 模式：

- SQLite 检查点设置，包括路径解析和父目录创建。
- 子代理技能元数据通过 `SubagentExecutor._load_skills()` 加载。
- `JsonlRunEventStore` 异步 API (`put`/`list_*`/`delete_*`)：JSONL
  run-event 后端通过 `asyncio.to_thread` 卸载其同步文件 IO
  （修复 #3084）；该锚点在检查下驱动真实的异步 API，
  因此任何在事件循环上重新引入阻塞 IO 的行为都会失败，
  而不只是检查是否删除了某个 `to_thread` 调用。
- `UploadsMiddleware.before_agent` 上传目录扫描：仅同步中间件
  钩子在异步图执行下的事件循环上运行，因此扫描是
  通过 `abefore_agent`+`run_in_executor` 卸载。
- 检查健康度：Blockbuster 捕获未卸载的调用、验证选择退出机制可用，
  并确认补丁在异常后会恢复。

随着静态检测和审查识别更多高风险异步路径，添加新的
运行时锚点。
