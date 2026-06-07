"""Run 状态与断连模式枚举。"""

from enum import StrEnum


class RunStatus(StrEnum):
    """单个 Run 的生命周期状态。

    状态机流转（参考 :class:`RunManager`）：
        ``pending`` → ``running`` → ``success``/``error``/``timeout``/``interrupted``

    Attributes:
        pending: Run 已创建但尚未开始执行。
        running: Run 正在执行中。
        success: Run 正常完成。
        error: Run 因异常失败。
        timeout: Run 因超时被强制结束。
        interrupted: Run 被用户主动取消/中断。
    """

    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    timeout = "timeout"
    interrupted = "interrupted"


class DisconnectMode(StrEnum):
    """SSE 消费端断连时的行为策略。

    Attributes:
        cancel: 客户端断开后立即取消后台 Run 任务（默认行为）。
        continue_: 客户端断开后 Run 继续在后台跑完，断开不影响结果。
    """

    cancel = "cancel"
    continue_ = "continue"
