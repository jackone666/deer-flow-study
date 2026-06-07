---
name: code-documentation
description: Use this skill when the user requests to generate, create, or improve documentation for code, APIs, libraries, repositories, or software projects. Supports README generation, API reference documentation, inline code comments, architecture documentation, changelog generation, and developer guides. Trigger on requests like "document this code", "create a README", "generate API docs", "write developer guide", or when analyzing codebases for documentation purposes.
---

# 代码文档生成技能（Code Documentation Skill）

## 概述（Overview）

本技能用于为软件项目、代码库、库与 API 生成专业、全面的文档。它遵循 React、Django、Stripe、Kubernetes 等业界项目的最佳实践，所产出的文档既准确、结构清晰，又能同时服务新贡献者与有经验的开发者。

输出范围从单文件 README 到多文档的开发者指南不等，始终与项目复杂度及用户需求相匹配。

## 核心能力（Core Capabilities）

- 生成带徽章（badges）、安装、用法与 API 参考的完整 README.md
- 通过源代码分析生成 API 参考文档
- 生成含架构图的架构与设计文档
- 撰写开发者上手与贡献指南
- 基于提交历史或发布说明生成更新日志（changelog）
- 按语言惯例生成内联代码文档
- 支持 JSDoc、docstrings、GoDoc、Javadoc、Rustdoc 等格式
- 适配项目所在语言与生态的文档风格

## 何时使用本技能（When to Use This Skill）

**在以下场景中应始终加载本技能：**

- 用户希望"document（文档化）""create docs（写文档）""write documentation（撰写文档）"任何代码
- 用户请求 README、API 参考或开发者指南
- 用户分享了一个代码库或仓库，希望生成文档
- 用户希望改进或更新已有文档
- 用户需要架构文档（含图表）
- 用户请求更新日志或迁移指南

## 文档工作流（Documentation Workflow）

### 阶段 1：代码库分析（Codebase Analysis）

在动手写任何文档之前，先彻底理解代码库。

#### 步骤 1.1：项目发现（Project Discovery）

确认项目基础信息：

| 字段 | 确定方式 |
|-------|-----------------|
| **Language(s)** | 查看文件扩展名、`package.json`、`pyproject.toml`、`go.mod`、`Cargo.toml` 等 |
| **Framework** | 从依赖中识别常见框架（React、Django、Express、Spring 等） |
| **Build System** | 检查 `Makefile`、`CMakeLists.txt`、`webpack.config.js`、`build.gradle` 等 |
| **Package Manager** | npm/yarn/pnpm、pip/uv/poetry、cargo、go modules 等 |
| **Project Structure** | 梳理目录树以理解架构 |
| **Entry Points** | 找到主文件、CLI 入口、对外导出的模块 |
| **Existing Docs** | 检查是否已有 README、docs/、wiki 或内联文档 |

#### 步骤 1.2：代码结构分析（Code Structure Analysis）

使用沙箱工具探索代码库：

```bash
# 获取目录结构
ls /mnt/user-data/uploads/project-dir/

# 读取关键文件
read_file /mnt/user-data/uploads/project-dir/package.json
read_file /mnt/user-data/uploads/project-dir/pyproject.toml

# 搜索公开 API 表面
grep -r "export " /mnt/user-data/uploads/project-dir/src/
grep -r "def " /mnt/user-data/uploads/project-dir/src/ --include="*.py"
grep -r "func " /mnt/user-data/uploads/project-dir/ --include="*.go"
```

#### 步骤 1.3：明确文档范围（Identify Documentation Scope）

基于分析结果，决定产出何种文档：

| 项目规模 | 推荐的文档 |
|-------------|--------------------------|
| **单文件 / 脚本** | 内联注释 + 用法说明头部 |
| **小型库** | 包含 API 参考的 README |
| **中型项目** | README + API 文档 + 示例 |
| **大型项目** | README + 架构 + API + 贡献指南 + 更新日志 |

### 阶段 2：文档生成（Documentation Generation）

#### 步骤 2.1：README 生成（README Generation）

每个项目都需要 README。按以下结构产出：

```markdown
# Project Name

[One-line project description — what it does and why it matters]

[![Badge](link)](#) [![Badge](link)](#)

## Features

- [Key feature 1 — brief description]
- [Key feature 2 — brief description]
- [Key feature 3 — brief description]

## Quick Start

### Prerequisites

- [Prerequisite 1 with version requirement]
- [Prerequisite 2 with version requirement]

### Installation

[Installation commands with copy-paste-ready code blocks]

### Basic Usage

[Minimal working example that demonstrates core functionality]

## Documentation

- [Link to full API reference if separate]
- [Link to architecture docs if separate]
- [Link to examples directory if applicable]

## API Reference

[Inline API reference for smaller projects OR link to generated docs]

## Configuration

[Environment variables, config files, or runtime options]

## Examples

[2-3 practical examples covering common use cases]

## Development

### Setup

[How to set up a development environment]

### Testing

[How to run tests]

### Building

[How to build the project]

## Contributing

[Contribution guidelines or link to CONTRIBUTING.md]

## License

[License information]
```

#### 步骤 2.2：API 参考生成（API Reference Generation）

对每个公开 API 表面，记录：

**函数 / 方法文档**：

```markdown
### `functionName(param1, param2, options?)`

Brief description of what this function does.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `param1` | `string` | Yes | — | Description of param1 |
| `param2` | `number` | Yes | — | Description of param2 |
| `options` | `Object` | No | `{}` | Configuration options |
| `options.timeout` | `number` | No | `5000` | Timeout in milliseconds |

**Returns:** `Promise<Result>` — Description of return value

**Throws:**
- `ValidationError` — When param1 is empty
- `TimeoutError` — When the operation exceeds the timeout

**Example:**

\`\`\`javascript
const result = await functionName("hello", 42, { timeout: 10000 });
console.log(result.data);
\`\`\`
```

**类文档**：

```markdown
### `ClassName`

Brief description of the class and its purpose.

**Constructor:**

\`\`\`javascript
new ClassName(config)
\`\`\`

| Parameter | Type | Description |
|-----------|------|-------------|
| `config.option1` | `string` | Description |
| `config.option2` | `boolean` | Description |

**Methods:**

- [`method1()`](#method1) — Brief description
- [`method2(param)`](#method2) — Brief description

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `property1` | `string` | Description |
| `property2` | `number` | Read-only. Description |
```

#### 步骤 2.3：架构文档（Architecture Documentation）

对于中大型项目，应包含架构文档：

```markdown
# Architecture Overview

## System Diagram

[Include a Mermaid diagram showing the high-level architecture]

\`\`\`mermaid
graph TD
    A[Client] --> B[API Gateway]
    B --> C[Service A]
    B --> D[Service B]
    C --> E[(Database)]
    D --> E
\`\`\`

## Component Overview

### Component Name
- **Purpose**: What this component does
- **Location**: `src/components/name/`
- **Dependencies**: What it depends on
- **Public API**: Key exports or interfaces

## Data Flow

[Describe how data flows through the system for key operations]

## Design Decisions

### Decision Title
- **Context**: What situation led to this decision
- **Decision**: What was decided
- **Rationale**: Why this approach was chosen
- **Trade-offs**: What was sacrificed
```

#### 步骤 2.4：内联代码文档（Inline Code Documentation）

按语言惯例生成内联文档：

**Python（Docstrings —— Google 风格）**：
```python
def process_data(input_path: str, options: dict | None = None) -> ProcessResult:
    """Process data from the given file path.

    Reads the input file, applies transformations based on the provided
    options, and returns a structured result object.

    Args:
        input_path: Absolute path to the input data file.
            Supports CSV, JSON, and Parquet formats.
        options: Optional configuration dictionary.
            - "validate" (bool): Enable input validation. Defaults to True.
            - "format" (str): Output format ("json" or "csv"). Defaults to "json".

    Returns:
        A ProcessResult containing the transformed data and metadata.

    Raises:
        FileNotFoundError: If input_path does not exist.
        ValidationError: If validation is enabled and data is malformed.

    Example:
        >>> result = process_data("/data/input.csv", {"validate": True})
        >>> print(result.row_count)
        1500
    """
```

**TypeScript（JSDoc / TSDoc）**：
```typescript
/**
 * Fetches user data from the API and transforms it for display.
 *
 * @param userId - The unique identifier of the user
 * @param options - Configuration options for the fetch operation
 * @param options.includeProfile - Whether to include the full profile. Defaults to `false`.
 * @param options.cache - Cache duration in seconds. Set to `0` to disable.
 * @returns The transformed user data ready for rendering
 * @throws {NotFoundError} When the user ID does not exist
 * @throws {NetworkError} When the API is unreachable
 *
 * @example
 * ```ts
 * const user = await fetchUser("usr_123", { includeProfile: true });
 * console.log(user.displayName);
 * ```
 */
```

**Go（GoDoc）**：
```go
// ProcessData reads the input file at the given path, applies the specified
// transformations, and returns the processed result.
//
// The input path must be an absolute path to a CSV or JSON file.
// If options is nil, default options are used.
//
// ProcessData returns an error if the file does not exist or cannot be parsed.
func ProcessData(inputPath string, options *ProcessOptions) (*Result, error) {
```

### 阶段 3：质量保障（Quality Assurance）

#### 步骤 3.1：文档完整性检查（Documentation Completeness Check）

确认文档覆盖以下要点：

- [ ] **是什么（What it is）** —— 对新人也能一目了然的项目说明
- [ ] **为何存在（Why it exists）** —— 解决的问题与价值主张
- [ ] **如何安装（How to install）** —— 可复制粘贴的安装命令
- [ ] **如何使用（How to use）** —— 至少一个最小可运行示例
- [ ] **API 表面（API surface）** —— 公开函数、类与类型全部有文档
- [ ] **配置（Configuration）** —— 全部环境变量、配置文件与选项
- [ ] **错误处理（Error handling）** —— 常见错误与解决方法
- [ ] **贡献（Contributing）** —— 如何搭建开发环境与提交变更

#### 步骤 3.2：质量标准（Quality Standards）

| 标准 | 检查项 |
|----------|-------|
| **准确性（Accuracy）** | 每段代码示例都应可与所述 API 实际配合运行 |
| **完整性（Completeness）** | 任何公开 API 表面均无遗漏 |
| **一致性（Consistency）** | 全文格式与结构保持一致 |
| **新鲜度（Freshness）** | 文档与当前代码匹配，而非旧版本 |
| **可访问性（Accessibility）** | 不出现未经解释的术语，首次使用的缩写需有定义 |
| **示例（Examples）** | 每个复杂概念至少配一个可操作示例 |

#### 步骤 3.3：交叉引用校验（Cross-reference Validation）

确保：

- 所有提到的文件路径在项目中真实存在
- 所有引用的函数与类在代码中真实存在
- 所有代码示例的函数签名与代码一致
- 版本号与项目实际版本一致
- 所有链接（内部与外部）均可访问

## 文档风格指南（Documentation Style Guide）

### 写作原则（Writing Principles）

1. **先讲"为什么"（Lead with the "why"）** —— 解释存在的原因，再讲工作方式
2. **渐进式披露（Progressive disclosure）** —— 先简单，后逐步增加复杂度
3. **展示优于讲述（Show, don't tell）** —— 优先用代码示例代替长篇解释
4. **主动语态（Active voice）** —— "The function returns X" 而非 "X is returned by the function"
5. **现在时（Present tense）** —— "The server starts on port 8080" 而非 "The server will start on port 8080"
6. **第二人称（Second person）** —— "You can configure..." 而非 "Users can configure..."

### 格式规则（Formatting Rules）

- 使用 ATX 风格标题（`#`、`##`、`###`）
- 使用带语言标记的围栏代码块（` ```python `、` ```bash `）
- 使用表格组织结构化信息（参数、选项、配置）
- 使用提示块标注重要信息、警告与技巧
- 源代码中正文行宽保持可读（约 80-100 字符换行）
- 对函数名、文件路径、变量名、CLI 命令使用 `code formatting`

### 语言专属惯例（Language-Specific Conventions）

| 语言 | 文档格式 | 风格指南 |
|----------|-----------|-------------|
| Python | Google 风格 docstrings | PEP 257 |
| TypeScript/JavaScript | TSDoc / JSDoc | TypeDoc 规范 |
| Go | GoDoc 注释 | Effective Go |
| Rust | Rustdoc (`///`) | Rust API Guidelines |
| Java | Javadoc | Oracle Javadoc Guide |
| C/C++ | Doxygen | Doxygen 手册 |

## 输出处理（Output Handling）

生成完成后：

- 将文档文件保存到 `/mnt/user-data/outputs/`
- 对于多文件文档，保持项目目录结构
- 使用 `present_files` 工具将生成的文件展示给用户
- 主动提出可针对具体章节迭代或调整详细程度
- 建议可能还需要补充的其他文档

## 备注（Notes）

- 撰写文档前务必先分析实际代码 —— 切勿凭空猜测 API 签名或行为
- 当已有文档存在时，除非用户明确要求重写，否则保留其结构
- 对于大型代码库，优先记录公开 API 表面与关键抽象
- 文档语言应与项目既有文档语言保持一致；若无既有文档则默认使用英语
- 生成更新日志时，使用 [Keep a Changelog](https://keepachangelog.com/) 格式
- 本技能与 `deep-research` 技能组合效果良好，可用于记录第三方集成或依赖
