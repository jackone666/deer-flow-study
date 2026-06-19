# 内存设置回顾

以尽可能少的手动步骤在本地查看内存设置 add/edit 流程时使用此选项。

## 快速回顾

1. 使用您已使用的任何工作开发设置在本地启动 DeerFlow。

   示例：

   ```bash
   make dev
   ```

   或

   ```bash
   make docker-start
   ```

   如果您已经在本地运行 DeerFlow，则可以重用现有的设置。

2. 加载样本内存夹具。

   ```bash
   python scripts/load_memory_sample.py
   ```

3. 打开 `Settings > Memory`。

   默认本地 URLs:
   - 应用程序：`http://localhost:2026`
   - 仅本地前端后备：`http://localhost:3000`

## 最少的手动测试

1. 单击 `Add fact`。
2. 创建一个新事实：
   - 内容：`Reviewer-added memory fact`
   - 类别：`testing`
   - 置信度：`0.88`
3. 确认新事实立即出现并显示 `Manual` 作为源。
4. 编辑示例事实 `This sample fact is intended for edit testing.` 并将其更改为：
   - 内容：`This sample fact was edited during manual review.`
   - 类别：`testing`
   - 置信度：`0.91`
5. 确认编辑后的事实立即更新。
6. 刷新页面并确认新添加的事实和编辑的事实仍然存在。

## 可选的健全性检查

- 搜索 `Reviewer-added` 并确认新事实匹配。
- 搜索 `workflow` 并确认类别文本可搜索。
- 在 `All`、`Facts`和`Summaries` 之间切换。
- 删除一次性样本事实 `Delete fact testing can target this disposable sample entry.` 并立即确认列表更新。
- 清除所有内存并确认页面进入空状态。

## 夹具文件

- 样品夹具：`backend/docs/memory-settings-sample.json`
- 默认本地运行时目标：`backend/.deer-flow/memory.json`

加载器脚本在覆盖现有运行时内存文件之前自动创建带时间戳的备份。
