---
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
---

# 技能创建器（Skill Creator）

一个用于创建新技能并以迭代方式持续改进它们的技能。

从较高的层面看，创建技能的过程如下：

- 明确你希望技能做什么，以及大致应该怎么做
- 撰写一份技能草稿
- 设计若干测试提示词，并让"具备技能访问权限的 Claude"运行它们
- 协助用户从定性与定量两个维度评估结果
  - 在运行于后台的期间，如尚无量化评测，可先起草一些；如果已有，可直接使用或酌情修改。然后向用户解释这些评测（若已存在则解释现有的）
  - 使用 `eval-viewer/generate_review.py` 脚本向用户呈现结果供其审阅，并允许其查看量化指标
- 根据用户对结果的反馈（以及量化基准中暴露出的明显缺陷）改写技能
- 重复上述过程直到满意
- 扩充测试集并尝试更大规模的验证

使用本技能时，你的任务是判断用户当前位于该流程的哪一步，然后主动介入、协助其向前推进。例如，用户可能说"我想做一个关于 X 的技能"，这时你可以协助其澄清需求、撰写草稿、设计测试用例、确定评估方式、运行所有提示词，然后再迭代。

反过来，用户也可能已经手握一份技能草稿。这种情况下你可以直接跳到"评测 / 迭代"环节。

当然，请始终保持灵活。如果用户表示"我不需要跑一堆评测，跟我随便聊聊就行"，那也可以照此进行。

技能定稿之后（顺序可以灵活），你还可以运行"技能描述优化器"——我们为其准备了独立脚本——以优化技能的触发准确性。

明白了吗？明白了。

## 与用户沟通

技能创建器的使用者覆盖面很广，从代码圈外人到资深人士都有。如果你还没听说（确实非常近期才出现的现象），如今有一股潮流：Claude 的强大能力正在激励水管工打开终端、激励父母祖辈去搜索"如何安装 npm"。另一方面，大多数用户应当具备相当的计算机素养。

因此请关注上下文线索，以判断如何措辞沟通。默认情况下，给你一些参考：

- "evaluation"（评估）和 "benchmark"（基准）属于边界词，可以使用
- 对于 "JSON" 和 "assertion"（断言），在未加解释地使用它们之前，请确认用户表现出了熟悉这些概念的明确信号

如有疑虑，简短地解释术语是完全可以的；如果你也不确定用户是否能理解，可以用一句话给出定义。

---

## 创建技能

### 捕捉意图

首先理解用户的意图。当前对话本身可能就包含用户希望固化的流程（例如他们说"把这个做成一个技能"）。如果是这样，优先从对话历史中提取答案 —— 用到了哪些工具、步骤顺序、用户做了哪些修正、观察到的输入/输出格式。用户可能需要补充缺失信息，并在进入下一步之前确认。

1. 该技能应当让 Claude 具备什么能力？
2. 何时应当触发该技能？（哪些用户表述 / 上下文）
3. 期望的输出格式是什么？
4. 是否需要建立测试用例验证技能是否有效？对于输出可客观验证的技能（文件转换、数据抽取、代码生成、固定流程步骤），建立测试用例很有帮助。对于输出偏主观的技能（写作风格、艺术设计），通常不需要测试用例。建议根据技能类型给出恰当的默认方案，但最终决定权交给用户。

### 访谈与调研

主动询问关于边界情况、输入/输出格式、示例文件、成功标准与依赖项的问题。在把这些敲定之前，不要先写测试提示词。

检查可用的 MCP —— 若有助于调研（搜索文档、寻找类似技能、查询最佳实践），可借助子代理并行调研（若可用），否则就地完成。带着充分背景去与用户沟通，以减轻用户负担。

### 撰写 SKILL.md

根据用户访谈结果，填写以下组成部分：

- **name**：技能标识符
- **description**：何时触发、做什么。这是主要的触发机制 —— 既要写明"技能做什么"，也要写明"何时使用的具体上下文"。所有"何时使用"的信息都应放在 description 中，而非正文。注意：目前 Claude 存在"不愿触发技能"的倾向 —— 在本该使用技能时却未使用。为缓解这一点，请把技能描述写得稍微"主动"一些。例如，与"How to build a simple fast dashboard to display internal Anthropic data."相比，可以写"如何为内部 Anthropic 数据构建一个简洁快速的可视化看板。每当用户提到看板、数据可视化、内部指标，或希望展示任何形式的内部公司数据时，请务必使用本技能，即便用户没有明确要求'看板'一词。"
- **compatibility**：必需的工具、依赖项（可选，极少使用）
- **技能的其余部分 :)**

### 技能编写指南

#### 技能结构

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Files used in output (templates, icons, fonts)
```

#### 渐进式披露

技能采用三级加载体系：
1. **元数据（name + description）** —— 始终在上下文中（约 100 词）
2. **SKILL.md 正文** —— 技能被触发时进入上下文（理想 < 500 行）
3. **捆绑资源** —— 按需加载（无限制，脚本可直接执行而无需加载）

以上词数仅为大致参考，如确有需要可适当超出。

**关键模式：**
- 保持 SKILL.md 控制在 500 行以内；若接近此上限，请新增一层目录结构，并清晰指明模型下一步应去哪里查阅
- 在 SKILL.md 中明确引用其他文件，并给出何时阅读的指引
- 对于大型参考文件（> 300 行），应附上目录

**按领域组织**：当一个技能需要支持多个领域 / 框架时，按变体组织：
```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```
Claude 只会读取与之相关的参考文件。

#### "不令人意外"原则

这是不言自明的：技能不得包含恶意软件、漏洞利用代码或任何可能危及系统安全的内容。技能的内容在被描述时不应让用户感到意外。不要配合那些要求创建误导性技能、或旨在实现未授权访问、数据外泄等恶意活动的技能。但类似"以某某身份进行角色扮演"之类的请求是 OK 的。

#### 写作模式

在指令中优先使用祈使语气。

**定义输出格式** —— 可以这样写：
```markdown
## Report structure
ALWAYS use this exact template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

**示例模式** —— 包含示例非常有用。可以这样组织（不过若示例中包含 "Input" 和 "Output"，可酌情变通）：
```markdown
## Commit message format
**Example 1:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

### 写作风格

尝试向模型解释"事情为何重要"，而不是堆砌生硬的"必须"。运用心智模型理论，让技能尽可能通用、避免过度贴合具体例子。先写一份草稿，然后以全新眼光审视并加以改进。

### 测试用例

写完技能草稿后，设计 2-3 个贴近真实的测试提示词 —— 也就是真实用户实际会说的那种话。向用户展示：[不必逐字照搬] "我准备用这几个测试用例。你看这样可以吗？是否需要补充？" 然后运行它们。

将测试用例保存到 `evals/evals.json`。暂时不要写断言 —— 仅写提示词即可。下一阶段你会在运行期间起草断言。

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

完整的 schema（包含后续会添加的 `assertions` 字段）请参阅 `references/schemas.md`。

## 运行与评估测试用例

本节是连续执行的流程 —— 中途不要停止。**不要**使用 `/skill-test` 或任何其他测试技能。

将结果放入与技能目录同级的 `<skill-name>-workspace/` 下。在 workspace 内按迭代组织结果（`iteration-1/`、`iteration-2/` 等），每个迭代中每个测试用例各占一个目录（`eval-0/`、`eval-1/` 等）。不要一次性创建好所有目录 —— 用到什么就创建什么。

### 第 1 步：在同一回合内派发所有运行（有技能 和 基线）

对每个测试用例，在同一回合中派发两个子代理 —— 一个带技能，一个不带。这一点很重要：不要先派发"带技能"的任务，之后再回来跑基线。所有任务应一次性派发，从而大致同时完成。

**带技能的运行：**

```
Execute this task:
- Skill path: <path-to-skill>
- Task: <eval prompt>
- Input files: <eval files if any, or "none">
- Save outputs to: <workspace>/iteration-<N>/eval-<ID>/with_skill/outputs/
- Outputs to save: <what the user cares about — e.g., "the .docx file", "the final CSV">
```

**基线运行**（相同提示词，但基线取决于上下文）：
- **创建新技能**：完全不使用任何技能。相同提示词，不指定技能路径，输出保存到 `without_skill/outputs/`。
- **改进既有技能**：使用旧版本。在编辑之前，先对技能做快照（`cp -r <skill-path> <workspace>/skill-snapshot/`），然后让基线子代理使用该快照。输出保存到 `old_skill/outputs/`。

为每个测试用例编写一份 `eval_metadata.json`（断言可以暂时为空）。给每个评估起一个能反映其测试内容的描述性名称 —— 不要只叫"eval-0"。目录名也使用同样的命名。如果本次迭代使用了新增或修改过的评估提示词，请为每个新评估目录创建这些文件 —— 不要假设它们会从之前的迭代自动继承。

```json
{
  "eval_id": 0,
  "eval_name": "descriptive-name-here",
  "prompt": "The user's task prompt",
  "assertions": []
}
```

### 第 2 步：在运行进行期间，起草断言

不要只是干等运行结束 —— 可以利用这段时间。为每个测试用例起草量化断言，并向用户解释它们。如果 `evals/evals.json` 中已存在断言，请审阅并说明其检查项。

好的断言应当客观可验证，且命名具有描述性 —— 在基准查看器中应该一眼就能看清每条断言检查的内容。主观类技能（写作风格、设计质量）更适合定性评估 —— 不要将需要人为判断的事项强行套上断言。

起草完成后，更新 `eval_metadata.json` 文件与 `evals/evals.json` 中的断言。同时向用户说明其在查看器中会看到的内容 —— 既有定性输出，也有定量基准。

### 第 3 步：在每次运行完成时，捕获时序数据

每当子代理任务完成时，你会收到一条包含 `total_tokens` 和 `duration_ms` 的通知。立刻将数据保存到该运行目录的 `timing.json` 中：

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3
}
```

这是捕获此数据的唯一机会 —— 它通过任务通知一次性送达，并不会在别处持久化。请在通知到来时立即处理，不要试图攒批处理。

### 第 4 步：评分、聚合并启动查看器

所有运行结束后：

1. **为每次运行打分** —— 派发一个评分子代理（或就地评分），它会读取 `agents/grader.md` 并对照各条断言评估输出。在每个运行目录下保存 `grading.json`。`grading.json` 的 expectations 数组必须使用 `text`、`passed`、`evidence` 这三个字段（不要使用 `name`/`met`/`details` 或其他变体）—— 查看器依赖这些精确的字段名。对于可程序化检查的断言，应编写并运行脚本而非人工判断 —— 脚本更快、更可靠，且可跨迭代复用。

2. **聚合为基准** —— 在技能创建器目录下运行聚合脚本：
   ```bash
   python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>
   ```
   这将生成 `benchmark.json` 与 `benchmark.md`，包含每次配置的通过率、耗时与 token，并给出均值 ± 标准差以及差值。若手动生成 benchmark.json，请参阅 `references/schemas.md` 获取查看器所期望的精确 schema。
   将"带技能"版本排在对应的基线之前。

3. **进行一次分析（analyst）复盘** —— 读取基准数据，找出聚合统计可能掩盖的模式。可参阅 `agents/analyzer.md`（"Analyzing Benchmark Results" 一节）了解需要关注的方面 —— 例如无论是否使用技能都始终通过的断言（无区分度）、高方差评估（可能存在偶发性问题）、时间 / token 之间的权衡等。

4. **启动查看器** 同时展示定性输出与定量数据：
   ```bash
   nohup python <skill-creator-path>/eval-viewer/generate_review.py \
     <workspace>/iteration-N \
     --skill-name "my-skill" \
     --benchmark <workspace>/iteration-N/benchmark.json \
     > /dev/null 2>&1 &
   VIEWER_PID=$!
   ```
   对于第 2 轮及以后的迭代，还要传入 `--previous-workspace <workspace>/iteration-<N-1>`。

   **Cowork / 无桌面环境：** 若 `webbrowser.open()` 不可用或环境没有显示器，请使用 `--static <output_path>` 生成一个独立的 HTML 文件，而不是启动服务。用户点击"Submit All Reviews"后，反馈将以 `feedback.json` 文件形式下载。下载后，将 `feedback.json` 复制到 workspace 目录中，供下一轮迭代读取。

注意：请使用 `generate_review.py` 生成查看器，无需自己编写定制 HTML。

5. **告知用户** 类似："我已在浏览器中为你打开结果。这里有两个标签页 —— 'Outputs' 让你逐个查看测试用例并留下反馈；'Benchmark' 展示定量比较。完成后请回到这里告诉我。"

### 用户在查看器中看到的内容

"Outputs" 标签页一次展示一个测试用例：
- **Prompt**：所给任务
- **Output**：技能生成的文件，能内联展示的就直接渲染
- **Previous Output**（第 2 轮及之后）：以折叠区展示上一轮的输出
- **Formal Grades**（若已评分）：以折叠区展示断言通过/未通过情况
- **Feedback**：一个边输入边自动保存的文本框
- **Previous Feedback**（第 2 轮及之后）：上一轮用户的评论，显示在文本框下方

"Benchmark" 标签页展示统计摘要：各配置的通过率、耗时、token 使用量，附按评估维度的细目以及分析观察。

通过"上一条 / 下一条"按钮或方向键导航。完成后，用户点击"Submit All Reviews"，将所有反馈保存到 `feedback.json`。

### 第 5 步：读取反馈

当用户表示完成时，读取 `feedback.json`：

```json
{
  "reviews": [
    {"run_id": "eval-0-with_skill", "feedback": "the chart is missing axis labels", "timestamp": "..."},
    {"run_id": "eval-1-with_skill", "feedback": "", "timestamp": "..."},
    {"run_id": "eval-2-with_skill", "feedback": "perfect, love this", "timestamp": "..."}
  ],
  "status": "complete"
}
```

空反馈意味着用户认为该项可以接受。请将改进重点放在用户有具体意见的测试用例上。

用完查看器后请将其关闭：

```bash
kill $VIEWER_PID 2>/dev/null
```

---

## 改进技能

这是整个循环的核心。你已运行过测试用例，用户也已审阅过结果，现在需要根据反馈让技能变得更好。

### 如何思考改进

1. **从反馈中归纳共性。** 这里的根本目标，是打造出可以被使用百万次（也许字面上、也许更多，谁知道呢）的技能，跨各种各样的提示词使用。此刻你与用户在若干例子上反复迭代，是因为这样推进更快 —— 用户对这些例子了然于胸，能快速评估新的输出。但是，如果你和用户共同开发的技能只在这些例子上有效，那它毫无用处。与其堆砌零碎过拟合的小修改，或令人窒息的强约束"必须"，如果存在顽固问题，不妨尝试换一个隐喻、换一套工作模式。这种尝试成本不高，也许能碰出很棒的结果。

2. **保持提示词精炼。** 砍掉那些没有发挥作用的负担。务必阅读对话全文而非只看最终输出 —— 如果技能在让模型做大量无谓的事情，可尝试移除引发此类行为的部分，观察效果如何。

3. **解释原因。** 努力解释你要求模型所做之事的**为什么**。如今的 LLM 都很聪明。它们有不错的心智模型（theory of mind），一旦获得良好框架，便能超越机械指令真正把事做成。即便用户的反馈简短甚至带情绪，请尝试真正理解其任务、为什么这样写、写的究竟是什么，然后把这种理解注入到指令之中。如果你发现自己频繁使用全大写 ALWAYS 或 NEVER、或非常死板的结构，那是一个黄色警告 —— 尽量重新措辞、解释原因，让模型明白你为何如此要求。这是更人性化、更强大、更有效的方式。

4. **留意跨测试用例的重复工作。** 阅读测试运行的对话全文，留意子代理是否各自独立编写了相似的辅助脚本，或对同一件事采取了相同的多步路径。如果三个测试用例都导致子代理写了 `create_docx.py` 或 `build_chart.py`，这就是一个强烈的信号：技能应当内置该脚本。写一次、放进 `scripts/`、并指示技能使用它。这能让未来每次调用都免于重新造轮子。

这项任务相当重要（我们正试图在这里每年创造数十亿美元的经济价值！），而你的思考时间不是瓶颈；请不吝思考时间，认真推敲。建议写一份修订草稿，然后以全新视角检视并改进。真正设身处地地理解用户想要什么、需要什么。

### 迭代循环

改进技能后：

1. 将改进内容应用到技能
2. 将所有测试用例重新运行到新的 `iteration-<N+1>/` 目录中，包括基线运行。如果是在创建新技能，基线始终是 `without_skill`（不使用技能）—— 跨迭代保持一致。如果是在改进既有技能，是否将基线设为原始版本还是上一轮版本，请自行判断。
3. 启动审阅器，并通过 `--previous-workspace` 指向上一轮迭代
4. 等待用户审阅并告知完成
5. 读取新反馈，再次改进，循环往复

请持续迭代，直到：
- 用户表示满意
- 反馈全部为空（一切看起来都很好）
- 已无法取得有意义的进展

---

## 进阶：盲测对比

对于希望更严格地对比两个技能版本的场景（例如用户问"新版本真的更好吗？"），本系统提供了盲测对比机制。请阅读 `agents/comparator.md` 与 `agents/analyzer.md` 了解详情。基本思路是：把两个输出交给一个独立子代理，但告知之前不透露各自来源，让它判定质量。然后分析胜方为何胜出。

这是可选的、依赖子代理的功能，大多数用户不会用到。人类审阅循环通常已经足够。

---

## 描述优化

SKILL.md frontmatter 中的 description 字段是决定 Claude 是否调用该技能的主要机制。在创建或改进完技能后，建议优化 description 以提升触发准确性。

### 第 1 步：生成触发评估查询

创建 20 条评估查询 —— 包含"应触发"和"不应触发"的混合。保存为 JSON：

```json
[
  {"query": "the user prompt", "should_trigger": true},
  {"query": "another prompt", "should_trigger": false}
]
```

查询必须贴近真实，是 Claude Code 或 Claude.ai 用户实际会输入的内容。不是抽象的请求，而是具体、特定、带有相当细节的请求。比如文件路径、用户工作或情境的私人上下文、列名与值、公司名、URL。一些背景故事。部分可能小写、含缩写、错别字或口语化。长度多样，并将重点放在边界情况而非一目了然的情况（用户会有机会确认这些查询）。

差的："Format this data"、"Extract text from PDF"、"Create a chart"

好的："ok so my boss just sent me this xlsx file (its in my downloads, called something like 'Q4 sales final FINAL v2.xlsx') and she wants me to add a column that shows the profit margin as a percentage. The revenue is in column C and costs are in column D i think"

对于**应触发**的查询（8-10 条），需要考虑覆盖度。围绕同一意图给出不同措辞 —— 有的正式、有的随意。包含用户未明确点名技能或文件类型、但明显需要的场景。加入一些少见用例以及与另一技能存在竞争但本应胜出的场景。

对于**不应触发**的查询（8-10 条），最有价值的是"几乎命中" —— 即与本技能共享关键词或概念、但实际需要别的方案的查询。思考相邻领域、措辞模糊的场景（朴素关键词匹配会触发但实际不应触发），以及查询触及技能能力却处于其他工具更合适的上下文。

最关键的避坑点：不要让"不应触发"的查询一眼就能看出无关。例如对 PDF 技能用 "Write a fibonacci function" 作为反向测试就太简单了 —— 它根本没在测什么。负向用例应当是真正刁钻的。

### 第 2 步：与用户一起审阅

使用 HTML 模板向用户展示评估集：

1. 读取 `assets/eval_review.html` 模板
2. 替换占位符：
   - `__EVAL_DATA_PLACEHOLDER__` → 评估项的 JSON 数组（不要在外面加引号 —— 它是 JS 变量赋值）
   - `__SKILL_NAME_PLACEHOLDER__` → 技能名称
   - `__SKILL_DESCRIPTION_PLACEHOLDER__` → 技能当前的 description
3. 写入临时文件（例如 `/tmp/eval_review_<skill-name>.html`）并打开：`open /tmp/eval_review_<skill-name>.html`
4. 用户可以编辑查询、切换 should-trigger、增删条目，然后点击"Export Eval Set"
5. 文件会下载到 `~/Downloads/eval_set.json` —— 若存在多个版本（例如 `eval_set (1).json`），请检查 Downloads 文件夹中最新的那份

这一步至关重要 —— 糟糕的评估查询会带来糟糕的描述。

### 第 3 步：运行优化循环

告知用户："这会花费一些时间 —— 我会在后台运行优化循环，并定时查看进度。"

将评估集保存到 workspace，然后在后台运行：

```bash
python -m scripts.run_loop \
  --eval-set <path-to-trigger-eval.json> \
  --skill-path <path-to-skill> \
  --model <model-id-powering-this-session> \
  --max-iterations 5 \
  --verbose
```

请使用系统提示中给出的模型 ID（驱动当前会话的那个），以保证触发测试与用户实际体验一致。

运行期间，定期 `tail` 输出，向用户汇报当前所在迭代及分数。

该脚本会自动完成完整的优化循环：它将评估集切分为 60% 训练集与 40% 留出测试集，先评估当前的 description（每条查询跑 3 次以获得稳定的触发率），然后调用 Claude 基于失败案例提出改进；接着在训练集与测试集上重新评估每个新 description，迭代最多 5 轮。完成后，它会在浏览器中打开一份 HTML 报告，呈现每轮迭代的结果，并返回包含 `best_description` 的 JSON —— 该字段按测试分数（而非训练分数）挑选，以避免过拟合。

### 技能触发机制的工作原理

理解触发机制有助于设计更好的评估查询。技能以"名称 + 描述"出现在 Claude 的 `available_skills` 列表中，Claude 据此决定是否咨询该技能。需要知道的关键一点是：Claude 仅在面对自己难以直接处理的任务时才会主动咨询技能 —— 像"读一下这个 PDF"这样的简单单步查询，即便描述完美匹配，也可能不会触发技能，因为 Claude 用基础工具就能直接完成。复杂、多步、专门化的查询则能在描述匹配时稳定触发技能。

这意味着评估查询应当足够"实质"，让 Claude 真正能从咨询技能中获益。像"读一下文件 X"这种简单查询是糟糕的测试用例 —— 无论描述质量如何都不会触发技能。

### 第 4 步：应用结果

从 JSON 输出中取出 `best_description`，更新技能的 SKILL.md frontmatter。向用户展示改前 / 改后对比，并报告分数。

---

### 打包与交付（仅当 `present_files` 工具可用时）

检查你是否拥有 `present_files` 工具。若没有，跳过此步。若有，将技能打包并把 .skill 文件呈现给用户：

```bash
python -m scripts.package_skill <path/to/skill-folder>
```

打包完成后，将生成的 `.skill` 文件路径告知用户，以便其安装。

---

## Claude.ai 专属指引

在 Claude.ai 中，核心工作流相同（草稿 → 测试 → 审阅 → 改进 → 重复），但由于 Claude.ai 没有子代理，部分机制需要调整。以下是需要适配的内容：

**运行测试用例**：没有子代理意味着无法并行执行。对每个测试用例，请阅读该技能的 SKILL.md，然后按其指令亲自完成该测试提示词所对应的任务。一次一个。这样做虽然不如独立子代理那样严格（既写技能又运行技能，因此你拥有完整上下文），但仍是有价值的健全性检查 —— 并且人类审阅环节会补足这一不足。跳过基线运行 —— 直接使用技能按要求完成任务即可。

**审阅结果**：如果无法打开浏览器（例如 Claude.ai 的 VM 没有显示器，或你位于远程服务器上），则完全跳过浏览器审阅器。改为直接在对话中呈现结果。对每个测试用例，展示提示词与输出。如果输出是用户需要查看的文件（如 .docx 或 .xlsx），请将其保存到文件系统，并告知其位置以便用户下载与查看。直接以对话方式征求反馈："看起来怎么样？有什么想改的？"

**基准对比**：跳过定量基准 —— 它依赖基线对比，没有子代理就失去意义。重点关注来自用户的定性反馈。

**迭代循环**：和之前一致 —— 改进技能、重新跑测试用例、征求反馈 —— 只是中间没有浏览器审阅器。如果你有文件系统，仍然可以按迭代组织结果。

**描述优化**：本节依赖 `claude` CLI 工具（具体而言是 `claude -p`），该工具仅在 Claude Code 中可用。在 Claude.ai 中请跳过。

**盲测对比**：依赖子代理。请跳过。

**打包**：`package_skill.py` 脚本在具备 Python 和文件系统的任何环境都能工作。在 Claude.ai 中，你可以运行它，用户可下载生成的 `.skill` 文件。

**更新既有技能**：用户可能希望更新既有技能而非新建一个。这种情况下：
- **保留原名称。** 记下技能目录名与 `name` 字段 —— 保持不变。例如，若已安装的技能是 `research-helper`，则输出 `research-helper.skill`（而不是 `research-helper-v2`）。
- **先复制到可写位置再编辑。** 已安装的技能路径可能是只读的。请先复制到 `/tmp/skill-name/`，再在那里编辑，然后从该副本打包。
- **若手动打包，先暂存到 `/tmp/`**，再复制到输出目录 —— 直接写入可能因权限而失败。

---

## Cowork 专属指引

在 Cowork 中，需要注意以下几点：

- 你有子代理，因此主工作流（并行派发测试用例、运行基线、打分等）全部可用。（但若遭遇严重的超时问题，将测试提示词改为串行运行也是 OK 的。）
- 你没有浏览器或显示器，因此生成评估查看器时，请使用 `--static <output_path>` 生成一个独立的 HTML 文件，而不是启动服务。然后向用户提供一个链接，让其可在自己的浏览器中打开 HTML。
- 不知为何，Cowork 的设置似乎使 Claude 在跑完测试后不太愿意生成评估查看器，因此再次强调：无论是在 Cowork 还是 Claude Code 中，跑完测试后请始终先生成评估查看器，让人类先于你自行评估样本、修订技能，**使用** `generate_review.py`（**不要**自己写定制 HTML）。抱歉要在这里大写强调：在你自己评估输入之前，**先**生成评估查看器。务必让人类尽快看到结果！
- 反馈机制有所不同：因没有运行中的服务，查看器的"Submit All Reviews"按钮会把 `feedback.json` 作为文件下载。你随后可以读取它（可能需要先请求访问权限）。
- 打包可用 —— `package_skill.py` 只需要 Python 和文件系统。
- 描述优化（`run_loop.py` / `run_eval.py`）在 Cowork 中应该可以正常工作，因为它通过子进程调用 `claude -p`，不依赖浏览器；但请在完全完成技能、用户也认可其状态良好之后再启动它。
- **更新既有技能**：用户可能希望更新既有技能而非新建一个。请遵循上文 Claude.ai 章节中的更新指引。

---

## 参考文件

`agents/` 目录包含各专业子代理的指令。需要派发相关子代理时请阅读对应文件。

- `agents/grader.md` —— 如何对照各断言评估输出
- `agents/comparator.md` —— 如何对两个输出做盲测 A/B 对比
- `agents/analyzer.md` —— 如何分析某一版本胜出的原因

`references/` 目录收录了额外文档：

- `references/schemas.md` —— `evals.json`、`grading.json` 等的 JSON 结构

---

最后再强调一遍核心循环：

- 弄清技能的目标
- 起草或编辑技能
- 在测试提示词上运行"具备技能访问权限的 Claude"
- 与用户一起评估输出：
  - 创建 benchmark.json 并运行 `eval-viewer/generate_review.py` 以帮助用户审阅测试用例
  - 运行量化评测
- 重复直到你和用户都满意
- 打包最终技能并交付给用户

请在 TodoList（如有）中加入上述步骤以避免遗漏。如果你身处 Cowork，请特别将"创建 evals JSON 并运行 `eval-viewer/generate_review.py`，以便人类审阅测试用例"放入 TodoList 中，确保它被执行。

祝你好运！
