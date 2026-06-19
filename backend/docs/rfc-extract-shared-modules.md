# RFC：将共享技能安装程序和上传管理器提取到 Harness 中

## 1. 问题

网关（`app/gateway/routers/skills.py`，`uploads.py`）和客户端（`deerflow/client.py`）各自独立实现相同的业务逻辑：

### 技能安装

| 逻辑 | 网关 (`skills.py`) | 客户 (`client.py`) |
|-------|----------------------|---------------------|
| 拉链安全检查 | `_is_unsafe_zip_member()` | 内联 `Path(info.filename).is_absolute()` |
| 符号链接过滤 | `_is_symlink_member()` | `p.is_symlink()` 提取后删除 |
| Zip炸弹防御 | `total_size += info.file_size` （已声明） | `total_size > 100MB` （已声明） |
| macOS 元数据过滤器 | `_should_ignore_archive_entry()` | 无 |
| Frontmatter验证 | `_validate_skill_frontmatter()` | `_validate_skill_frontmatter()` |
| 重复检测 | `HTTPException(409)` | `ValueError` |

**两种实现，行为不一致**：网关流写入并跟踪实际解压缩大小；客户申报金额为 `file_size`。网关在提取过程中跳过符号链接；客户端提取所有内容，然后遍历并删除符号链接。

### 上传管理

| 逻辑 | 网关 (`uploads.py`) | 客户 (`client.py`) |
|-------|----------------------|---------------------|
| 目录访问 | `get_uploads_dir()` + `mkdir` | `_get_uploads_dir()` + `mkdir` |
| 文件名安全 | 内联 `Path(f).name` + 手动检查 | 不检查，直接使用 `src_path.name` |
| 重复处理 | 无（覆盖） | 无（覆盖） |
| 上市 | 内联 `iterdir()` | 内联 `os.scandir()` |
| 删除 | 内联 `unlink()` + 遍历检查 | 内联 `unlink()` + 遍历检查 |
| 路径遍历 | `resolve().relative_to()` | `resolve().relative_to()` |

**相同的遍历检查被写入两次** - 任何安全修复都必须应用于两个位置。

## 2. 设计原则

### 依赖方向

```
app.gateway.routers.skills  ──┐
app.gateway.routers.uploads ──┤── calls ──→  deerflow.skills.installer
deerflow.client             ──┘              deerflow.uploads.manager
```

- 共享模块位于线束层（`deerflow.*`）中，纯业务逻辑，无FastAPI依赖
- 网关处理 HTTP 适配（`UploadFile`→ 字节，异常 →`HTTPException`）
- 客户端处理本地适配（`Path`→复制、异常→Python异常）
- 满足 `test_harness_boundary.py` 约束：harness 从不导入应用程序

### 异常策略

| 共享层异常 | 网关映射到 | 客户 |
|----------------------|-----------------|--------|
| `FileNotFoundError` | `HTTPException(404)` | 传播 |
| `ValueError` | `HTTPException(400)` | 传播 |
| `SkillAlreadyExistsError` | `HTTPException(409)` | 传播 |
| `PermissionError` | `HTTPException(403)` | 传播 |

用类型化异常匹配 (`SkillAlreadyExistsError`) 替换字符串类型路由 (`"already exists" in str(e)`)。

## 3.新模块

### 3.1 `deerflow.skills.installer`

```python
# Safety checks
is_unsafe_zip_member(info: ZipInfo) -> bool     # Absolute path / .. traversal
is_symlink_member(info: ZipInfo) -> bool         # Unix symlink detection
should_ignore_archive_entry(path: Path) -> bool  # __MACOSX / dotfiles

# Extraction
safe_extract_skill_archive(zip_ref, dest_path, max_total_size=512MB)
  # Streaming write, accumulates real bytes (vs declared file_size)
  # Dual traversal check: member-level + resolve-level

# Directory resolution
resolve_skill_dir_from_archive(temp_path: Path) -> Path
  # Auto-enters single directory, filters macOS metadata

# Install entry point
install_skill_from_archive(zip_path, *, skills_root=None) -> dict
  # is_file() pre-check before extension validation
  # SkillAlreadyExistsError replaces ValueError

# Exception
class SkillAlreadyExistsError(ValueError)
```

### 3.2 `deerflow.uploads.manager`

```python
# Directory management
get_uploads_dir(thread_id: str) -> Path      # Pure path, no side effects
ensure_uploads_dir(thread_id: str) -> Path   # Creates directory (for write paths)

# Filename safety
normalize_filename(filename: str) -> str
  # Path.name extraction + rejects ".." / "." / backslash / >255 bytes
deduplicate_filename(name: str, seen: set) -> str
  # _N suffix increment for dedup, mutates seen in place

# Path safety
validate_path_traversal(path: Path, base: Path) -> None
  # resolve().relative_to(), raises PermissionError on failure

# File operations
list_files_in_dir(directory: Path) -> dict
  # scandir with stat inside context (no re-stat)
  # follow_symlinks=False to prevent metadata leakage
  # Non-existent directory returns empty list
delete_file_safe(base_dir: Path, filename: str) -> dict
  # Validates traversal first, then unlinks

# URL helpers
upload_artifact_url(thread_id, filename) -> str   # Percent-encoded for HTTP safety
upload_virtual_path(filename) -> str               # Sandbox-internal path
enrich_file_listing(result, thread_id) -> dict     # Adds URLs, stringifies sizes
```

## 4. 变化

### 4.1 网关瘦身

**`app/gateway/routers/skills.py`**:
- 删除 `_is_unsafe_zip_member`、`_is_symlink_member`、`_safe_extract_skill_archive`、`_should_ignore_archive_entry`、`_resolve_skill_dir_from_archive_root`（约 80 行）
- `install_skill`路由成为对`install_skill_from_archive(path)` 的单个调用
- 异常映射：`SkillAlreadyExistsError → 409`、`ValueError → 400`、`FileNotFoundError → 404`

**`app/gateway/routers/uploads.py`**:
- 删除内联 `get_uploads_dir`（替换为 `ensure_uploads_dir`/`get_uploads_dir`）
- `upload_files`使用`normalize_filename()` 而不是内联安全检查
- `list_uploaded_files`使用`list_files_in_dir()` + 补充信息
- `delete_uploaded_file`使用`delete_file_safe()` + 配套的降价清理

### 4.2 客户端瘦身

**`deerflow/client.py`**:
- 删除 `_get_uploads_dir` 静态方法
- 删除 `install_skill` 中约 50 行内联 zip 处理
- `install_skill`代表`install_skill_from_archive()`
- `upload_files`使用`deduplicate_filename()`+`ensure_uploads_dir()`
- `list_uploads`使用`get_uploads_dir()`+`list_files_in_dir()`
- `delete_upload`使用`get_uploads_dir()`+`delete_file_safe()`
- `update_mcp_config`/`update_skill`现在重置`_agent_config_key = None`

### 4.3 Read/Write 路径分隔

| 操作 | 功能 | 创建目录？ |
|-----------|----------|:------------:|
| 上传（写入） | `ensure_uploads_dir()` | 是的 |
| 列表（已读） | `get_uploads_dir()` | 没有 |
| 删除（已读） | `get_uploads_dir()` | 没有 |

读取路径不再有 `mkdir` 副作用 - 不存在的目录返回空列表。

## 5. 安全改进

| 改进 | 之前 | 之后 |
|-------------|--------|-------|
| 拉链炸弹检测 | 声明的 `file_size` 的总和 | 流式写入，累积真实字节 |
| 符号链接处理 | 网关跳过/客户端删除解压后的内容 | 统一跳过+日志 |
| 遍历检查 | 仅限会员级别 | 会员级别 + `resolve().is_relative_to()` |
| 文件名反斜杠 | 网关检查/客户端不检查 | 统一拒绝 |
| 文件名长度 | 没有检查 | 拒绝 > 255 字节（OS 限制） |
| thread_id 验证 | 无 | 拒绝不安全的文件系统字符 |
| 列出符号链接泄漏 | `follow_symlinks=True` （默认） | `follow_symlinks=False` |
| 409状态路由 | `"already exists" in str(e)` | `SkillAlreadyExistsError` 类型匹配 |
| 工件 URL 编码 | URL 中的原始文件名 | `urllib.parse.quote()` |

## 6. 考虑的替代方案

| 替代方案 | 为什么不呢 |
|-------------|---------|
| 将逻辑保留在网关中，客户端通过 HTTP 调用网关 | 为嵌入式客户端添加网络依赖；违背了 `DeerFlowClient` 作为进程内 API 的目的 |
| 具有 Gateway/Client 子类的抽象基类 | 对纯函数的过度设计；不需要多态性 |
| 将所有内容移至 `client.py` 并让 Gateway 导入它 | 违反 harness/app 边界 - 客户端处于harness中，但特定于网关的模型（Pydantic响应类型）应保留在应用程序层 |
| 将网关和客户端合并为一个模块 | 他们服务于具有不同适应需求的不同消费者（HTTP 与进程中） |

## 7. 重大变更

**无。** 所有公共 APIs （网关 HTTP 端点、`DeerFlowClient`方法）保留其现有签名和返回格式。`SkillAlreadyExistsError`是`ValueError`的子类，因此现有的`except ValueError` 处理程序仍然可以捕获它。

## 8. 测试

| 模块 | 测试文件 | 计数 |
|--------|-----------|:-----:|
| `skills.installer` | `tests/test_skills_installer.py` | 22 |
| `uploads.manager` | `tests/test_uploads_manager.py` | 20 |
| `client` 硬化 | `tests/test_client.py`（新病例） | ~40 |
| `client` e2e | `tests/test_client_e2e.py`（新文件） | ~20 |

覆盖范围：不安全的 zip / 符号链接 / zip 炸弹 / frontmatter / 重复 / 扩展 / macOS 过滤器 / 标准化 / 去重 / 遍历 / 列表 / 删除 / 代理失效 / 上传生命周期 / 线程隔离 / URL 编码 / 配置污染。
