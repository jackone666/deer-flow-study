"""在子 Agent 内收集 LLM token 用量的回调处理器。

每个子 Agent 执行实例化一个 collector;子 Agent 结束后,所收集的记录会被转移
到父 RunJournal,通过 :meth:`RunJournal.record_external_llm_usage_records` 上报。
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class SubagentTokenCollector(BaseCallbackHandler):
    """轻量级回调处理器,用于在子 Agent 内累计 LLM token 用量。"""

    def __init__(self, caller: str):
        """初始化 collector。

        Args:
            caller: 调用方标识,会写入每条记录便于后续归因。
        """
        super().__init__()
        self.caller = caller
        self._records: list[dict[str, int | str]] = []
        self._counted_run_ids: set[str] = set()

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """LangChain ``on_llm_end`` 钩子:提取 usage_metadata 并去重累加。

        Args:
            response: LLM 返回的响应。
            run_id: 当前运行的唯一 ID。
            tags: LangChain 传入的标签。
            **kwargs: 其它兼容参数。
        """
        rid = str(run_id)
        if rid in self._counted_run_ids:
            return

        for generation in response.generations:
            for gen in generation:
                if not hasattr(gen, "message"):
                    continue
                usage = getattr(gen.message, "usage_metadata", None)
                usage_dict = dict(usage) if usage else {}
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk <= 0:
                    total_tk = input_tk + output_tk
                if total_tk <= 0:
                    continue
                self._counted_run_ids.add(rid)
                self._records.append(
                    {
                        "source_run_id": rid,
                        "caller": self.caller,
                        "input_tokens": input_tk,
                        "output_tokens": output_tk,
                        "total_tokens": total_tk,
                    }
                )
                return

    def snapshot_records(self) -> list[dict[str, int | str]]:
        """返回累计用量记录的浅拷贝。"""
        return list(self._records)
