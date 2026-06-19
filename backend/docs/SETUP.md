# 设置指南

DeerFlow 的快速设置说明。

## 配置设置

DeerFlow 使用 YAML 配置文件，该文件应放置在**项目根目录**中。

### 步骤

1. **导航到项目根目录**：
   ```bash
   cd /path/to/deer-flow
   ```

2. **复制示例配置**：
   ```bash
   cp config.example.yaml config.yaml
   ```

3. **编辑配置**：
   ```bash
   # Option A: Set environment variables (recommended)
   export OPENAI_API_KEY="your-key-here"

   # Optional: pin the project root when running from another directory
   export DEER_FLOW_PROJECT_ROOT="/path/to/deer-flow"

   # Option B: Edit config.yaml directly
   vim config.yaml  # or your preferred editor
   ```

4. **验证配置**：
   ```bash
   cd backend
   python -c "from deerflow.config import get_app_config; print('✓ Config loaded:', get_app_config().models[0].name)"
   ```

## 重要提示

- **位置**：`config.yaml`应位于`deer-flow/` （项目根目录）
- **Git**： `config.yaml` 被 git 自动忽略（包含秘密）
- **运行时根**：如果 DeerFlow 可以从项目根外部启动，则设置 `DEER_FLOW_PROJECT_ROOT`
- **运行时数据**：状态默认为项目根目录下的`.deer-flow`；设置 `DEER_FLOW_HOME` 来移动它
- **技能**：技能默认为项目根目录下的`skills/`；设置 `DEER_FLOW_SKILLS_PATH`或`skills.path` 来移动它们

## 配置文件位置

后端按以下顺序搜索 `config.yaml`：

1. 代码中的显式 `config_path` 参数
2. `DEER_FLOW_CONFIG_PATH` 环境变量（如果设置）
3. `DEER_FLOW_PROJECT_ROOT`下的`config.yaml`，或未设置 `DEER_FLOW_PROJECT_ROOT` 时的当前工作目录
4. 传统 backend/repository-root 位置以实现单一存储库兼容性

**推荐**：将 `config.yaml` 放在项目根目录 (`deer-flow/config.yaml`) 中。

## 沙盒设置（可选但推荐）

如果您计划使用 Docker/Container-based 沙箱（在 `sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider`下的`config.yaml` 中配置），强烈建议预先拉取容器镜像：

```bash
# From project root
make setup-sandbox
```

**为什么要预拉？**
- 沙盒映像（~500MB+）在第一次使用时被拉出，导致长时间等待
- 预拉提供清晰的进度指示
- 避免首次使用代理时出现混淆

如果您跳过此步骤，图像将在第一次代理执行时自动拉取，这可能需要几分钟，具体取决于您的网络速度。

## 故障排除

### 找不到配置文件

```bash
# Check where the backend is looking
cd deer-flow/backend
python -c "from deerflow.config.app_config import AppConfig; print(AppConfig.resolve_config_path())"
```

如果找不到配置：
1. 确保您已将 `config.example.yaml`复制到`config.yaml`
2. 验证您位于项目根目录中，或设置 `DEER_FLOW_PROJECT_ROOT`
3. 检查文件是否存在：`ls -la config.yaml`

### 权限被拒绝

```bash
chmod 600 ../config.yaml  # Protect sensitive configuration
```

## 另请参阅

- [Configuration Guide](CONFIGURATION.md) - 详细配置选项
- [Architecture Overview](../CLAUDE.md) - 系统架构
