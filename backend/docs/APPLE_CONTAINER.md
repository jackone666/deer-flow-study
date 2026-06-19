# Apple 容器支持

DeerFlow 现在支持 Apple Container 作为 macOS 上的首选容器运行时，并自动回退到 Docker。

## 概述

从这个版本开始，DeerFlow 自动检测并使用 macOS 上的 Apple 容器（如果可用），在以下情况下回退到 Docker：
- Apple 容器未安装
- 在非 macOS 平台上运行

这可以在 Apple Silicon Mac 上提供更好的性能，同时保持跨所有平台的兼容性。

## 好处

### 在带有 Apple Container 的 Apple Silicon Mac 上：
- **更好的性能**：本机 ARM64 执行，无需 Rosetta 2 转换
- **较低的资源使用**：比 Docker Desktop 更轻
- **本机集成**：使用 macOS Virtualization.framework

### 回退到 Docker：
- 完全向后兼容
- 适用于所有平台（macOS、Linux、Windows）
- 无需更改配置

## 要求

### 对于 Apple 容器（仅限 macOS）：
- macOS 15.0 或更高版本
- 苹果硅 (M1/M2/M3/M4)
- Apple 容器 CLI 已安装

### 安装：
```bash
# Download from GitHub releases
# https://github.com/apple/container/releases

# Verify installation
container --version

# Start the service
container system start
```

### 对于 Docker（所有平台）：
- Docker 桌面或 Docker 引擎

## 它是如何运作的

### 自动检测

`AioSandboxProvider` 自动检测可用的容器运行时：

1. 在 macOS 上：尝试 `container --version`
   - 成功 → 使用 Apple 容器
   - 失败 → 回退到 Docker

2. 其他平台：直接使用Docker

### 运行时差异

两个运行时使用几乎相同的命令语法：

**容器启动：**
```bash
# Apple Container
container run --rm -d -p 8080:8080 -v /host:/container -e KEY=value image

# Docker
docker run --rm -d -p 8080:8080 -v /host:/container -e KEY=value image
```

**容器清理：**
```bash
# Apple Container (with --rm flag)
container stop <id>  # Auto-removes due to --rm

# Docker (with --rm flag)
docker stop <id>     # Auto-removes due to --rm
```

### 实施细节

实现在 `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py` 中：

- `_detect_container_runtime()`：启动时检测可用的运行时
- `_start_container()`：使用检测到的运行时，跳过 Apple 容器的 Docker 特定选项
- `_stop_container()`：为运行时使用适当的停止命令

## 配置

无需更改配置！系统自动工作。

但是，您可以通过检查日志来验证正在使用的运行时：

```
INFO:deerflow.community.aio_sandbox.aio_sandbox_provider:Detected Apple Container: container version 0.1.0
INFO:deerflow.community.aio_sandbox.aio_sandbox_provider:Starting sandbox container using container: ...
```

或者对于 Docker：
```
INFO:deerflow.community.aio_sandbox.aio_sandbox_provider:Apple Container not available, falling back to Docker
INFO:deerflow.community.aio_sandbox.aio_sandbox_provider:Starting sandbox container using docker: ...
```

## 容器图像

两个运行时都使用 OCI 兼容的图像。默认图像适用于两者：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest  # Default image
```

确保您的图像可用于适当的架构：
- ARM64 用于 Apple Silicon 上的 Apple 容器
- AMD64 用于 Intel Mac 上的 Docker
- 多架构图像适用于两者

### 预拉图片（推荐）

**重要**：容器镜像通常很大（500MB+）并且在第一次使用时被拉取，这可能会导致长时间的等待时间而没有明确的反馈。

**最佳实践**：在设置过程中预拉图像：

```bash
# From project root
make setup-sandbox
```

该命令将：
1. 从`config.yaml`读取配置的图像（或使用默认值）
2. 检测可用的运行时（Apple Container 或 Docker）
3. 拉取带有进度指示的镜像
4. 验证图像是否可供使用

**手动预拉**：

```bash
# Using Apple Container
container image pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

# Using Docker
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

如果您跳过预拉取，图像将在第一次代理执行时自动拉取，这可能需要几分钟，具体取决于您的网络速度。

## 清理脚本

该项目包含一个处理两个运行时的统一清理脚本：

**脚本：** `scripts/cleanup-containers.sh`

**用法：**
```bash
# Clean up all DeerFlow sandbox containers
./scripts/cleanup-containers.sh deer-flow-sandbox

# Custom prefix
./scripts/cleanup-containers.sh my-prefix
```

**Makefile 集成：**

`Makefile` 中的所有清理命令都会自动处理两个运行时：
```bash
make stop   # Stops all services and cleans up containers
make clean  # Full cleanup including logs
```

## 测试

测试容器运行时检测：

```bash
cd backend
python test_container_runtime.py
```

这将：
1. 检测可用运行时
2. 可选择启动一个测试容器
3. 验证连接
4. 清理

## 故障排除

### macOS 上未检测到 Apple 容器

1. 检查是否已安装：
   ```bash
   which container
   container --version
   ```

2. 检查服务是否正在运行：
   ```bash
   container system start
   ```

3. 检查日志进行检测：
   ```bash
   # Look for detection message in application logs
   grep "container runtime" logs/*.log
   ```

### 容器未清理

1. 手动检查正在运行的容器：
   ```bash
   # Apple Container
   container list

   # Docker
   docker ps
   ```

2. 手动运行清理脚本：
   ```bash
   ./scripts/cleanup-containers.sh deer-flow-sandbox
   ```

### 性能问题

- Apple Container 在 Apple Silicon 上应该更快
- 如果遇到问题，您可以通过临时重命名 `container` 命令来强制使用 Docker：
   ```bash
   # Temporary workaround - not recommended for permanent use
   sudo mv /opt/homebrew/bin/container /opt/homebrew/bin/container.bak
   ```

## 参考文献

- [Apple Container GitHub](https://github.com/apple/container)
- [Apple Container Documentation](https://github.com/apple/container/blob/main/docs/)
- [OCI Image Spec](https://github.com/opencontainers/image-spec)
