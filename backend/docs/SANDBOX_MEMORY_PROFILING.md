# 沙箱内存分析

本指南在更改沙箱运行时之前记录了可重复的基线。
问题 #3213 报告 Kubernetes 中每个沙箱的内存接近 1 GiB。
在添加或推荐新的提供者之前，请捕获当前 AIO 沙箱基线，
并用相同 DeerFlow 工作负载对比候选运行时。

## 测量什么

至少测量这些样本：

1. 准备就绪后清空沙箱。
2. 在一个简单的 bash 命令之后。
3. 在导入公共包的Python任务之后。
4. 当需要基于节点的工作负载时，在节点任务之后。
5. 在`/mnt/user-data/outputs`下生成文件后。
6. 释放并预热复用后。
7. 在目标并发级别，例如 10、50 或 100 个沙箱。

`kubectl top` 报告 Kubernetes/container 工作集内存。将其视为容量规划信号，
而不是独占的 RSS/PSS。Pod 级内存包括 Pod 中的每个容器，
并且可能包含计入 cgroup 的缓存。如果结果看起来异常，
请先检查节点上的沙箱进程和 cgroup 指标，再下结论。

## 捕捉快照

从存储库根运行此命令：

```bash
python scripts/sandbox_memory_profile.py \
  --namespace deer-flow \
  --selector app=deer-flow-sandbox \
  --sample empty \
  --include-processes \
  --format markdown
```

对每个阶段使用描述性的 `--sample` 值：

```bash
python scripts/sandbox_memory_profile.py --sample after-bash --format json
python scripts/sandbox_memory_profile.py --sample after-python --format json
python scripts/sandbox_memory_profile.py --sample after-artifact --format json
```

`--include-processes`在每个沙箱 Pod 中运行`kubectl exec ... ps` 并添加
最高 RSS 进程报告。这有助于区分 Pod 级别的 cgroup
进程 RSS 的内存。这两个数字不会完全匹配，因为 cgroup
内存可以包括缓存和其他内核占用的内存。

比较后端时保存原始 JSON 以便总数、pod 名称、图像、
请求、限制和时间戳可以稍后审核。

## 候选运行时矩阵

对于 AIO、CubeSandbox、OpenSandbox、gVisor、Kata 或其他候选方案，
请比较相同工作负载并记录：

| 区域 | 所需证据 |
| --- | --- |
| 容量 | Pod 或实例计数、总内存、平均内存、最大内存 |
| 启动 | 1、10、50 和 100 个并发沙箱的就绪延迟 |
| 命令 | Bash 输出、超时行为、失败形态 |
| 文件 | `read_file`、`write_file`、二进制 `update_file`、`list_dir`、`glob`、`grep` |
| 上传 | 网关上传的文件在沙箱内可见 |
| 工件 | 写入 `/mnt/user-data/outputs` 的文件可由后端工件 API 读取 |
| 路径 | `/mnt/user-data/workspace`、`/mnt/user-data/uploads`、`/mnt/user-data/outputs`、`/mnt/acp-workspace` 和技能路径保持其预期语义 |
| 隔离 | 不同用户和线程不能互相读取对方的数据 |
| 清理 | 释放、空闲超时、进程重启、孤儿清理释放资源 |
| 运营 | 部署先决条件、特权组件、网络、存储和升级路径 |

## PR 指导

在相同 DeerFlow 工作负载已经同时跑过当前 AIO 沙箱和候选后端之前，
不要声称新的提供者修复了高并发内存占用问题。

对于实验提供者 PR，更喜欢 `Related to #3213`，除非 PR 也
包括可重现的 DeerFlow 工作负载数据，用于演示目标内存
减少并保留上传、输出、工件和隔离行为。
