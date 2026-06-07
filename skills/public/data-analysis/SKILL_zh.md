---
name: data-analysis
description: Use this skill when the user uploads Excel (.xlsx/.xls) or CSV files and wants to perform data analysis, generate statistics, create summaries, pivot tables, SQL queries, or any form of structured data exploration. Supports multi-sheet Excel workbooks, aggregation, filtering, joins, and exporting results to CSV/JSON/Markdown.
---

# 数据分析技能（Data Analysis Skill）

## 概述

本技能使用 DuckDB —— 一个进程内分析型 SQL 引擎 —— 分析用户上传的 Excel / CSV 文件。支持 schema 检查、SQL 查询、统计摘要、结果导出，全部通过单个 Python 脚本完成。

## 核心能力

- 检查 Excel / CSV 文件结构（sheets、columns、types、row counts）
- 对上传的数据执行任意 SQL 查询
- 生成统计摘要（mean、median、stddev、percentiles、nulls）
- 支持多 sheet 的 Excel 工作簿（每个 sheet 一张表）
- 把查询结果导出为 CSV、JSON 或 Markdown
- 借助 DuckDB 的列式引擎高效处理大文件

## 工作流

### 第 1 步：理解需求

当用户上传数据文件并请求分析时，先明确：

- **文件位置**：上传的 Excel / CSV 文件路径，位于 `/mnt/user-data/uploads/`
- **分析目标**：用户想得到的洞察（汇总、过滤、聚合、对比等）
- **输出格式**：结果的呈现方式（表格、CSV 导出、JSON 等）
- 无需检查 `/mnt/user-data` 下的目录

### 第 2 步：检查文件结构

先检查上传的文件，了解其 schema：

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/data.xlsx \
  --action inspect
```

返回内容：

- Sheet 名（Excel）或文件名（CSV）
- 列名、数据类型、非空计数
- 每个 sheet / 文件的行数
- 样本数据（前 5 行）

### 第 3 步：执行分析

基于 schema，构造 SQL 查询以回答用户的问题。

#### 运行 SQL 查询

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/data.xlsx \
  --action query \
  --sql "SELECT category, COUNT(*) as count, AVG(amount) as avg_amount FROM Sheet1 GROUP BY category ORDER BY count DESC"
```

#### 生成统计摘要

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/data.xlsx \
  --action summary \
  --table Sheet1
```

每个数值列返回：count、mean、std、min、25%、50%、75%、max、null_count。
字符串列返回：count、unique、top、frequency、null_count。

#### 导出结果

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/data.xlsx \
  --action query \
  --sql "SELECT * FROM Sheet1 WHERE amount > 1000" \
  --output-file /mnt/user-data/outputs/filtered-results.csv
```

支持的输出格式（按扩展名自动识别）：

- `.csv` —— 逗号分隔值
- `.json` —— 记录数组 JSON
- `.md` —— Markdown 表格

### 参数说明

| 参数 | 必填 | 说明 |
|-----------|----------|-------------|
| `--files` | 是 | 空格分隔的 Excel / CSV 文件路径 |
| `--action` | 是 | 取值之一：`inspect`、`query`、`summary` |
| `--sql` | 用于 `query` | 要执行的 SQL 查询 |
| `--table` | 用于 `summary` | 要汇总的表 / sheet 名 |
| `--output-file` | 否 | 结果导出路径（CSV / JSON / MD） |

> [!NOTE]
> 不要阅读 Python 文件，直接带参数调用即可。

## 表命名规则

- **Excel 文件**：每个 sheet 变为一张表，表名就是 sheet 名（例如 `Sheet1`、`Sales`、`Revenue`）
- **CSV 文件**：表名是去掉扩展名的文件名（例如 `data.csv` → `data`）
- **多文件**：所有文件中的表都处于同一查询上下文里，可做跨文件 join
- **特殊字符**：含空格或特殊字符的 sheet / 文件名会自动转义（空格 → 下划线）。以数字开头或含特殊字符的要用双引号，例如 `"2024_Sales"`

## 分析模式

### 基础探查

```sql
-- 行数
SELECT COUNT(*) FROM Sheet1

-- 列的去重值
SELECT DISTINCT category FROM Sheet1

-- 取值分布
SELECT category, COUNT(*) as cnt FROM Sheet1 GROUP BY category ORDER BY cnt DESC

-- 日期范围
SELECT MIN(date_col), MAX(date_col) FROM Sheet1
```

### 聚合与分组

```sql
-- 按品类 + 月份统计营收
SELECT category, DATE_TRUNC('month', order_date) as month,
       SUM(revenue) as total_revenue
FROM Sales
GROUP BY category, month
ORDER BY month, total_revenue DESC

-- 消费 Top 10 客户
SELECT customer_name, SUM(amount) as total_spend
FROM Orders GROUP BY customer_name
ORDER BY total_spend DESC LIMIT 10
```

### 跨文件 Join

```sql
-- 把销售与不同文件中的客户信息 join 起来
SELECT s.order_id, s.amount, c.customer_name, c.region
FROM sales s
JOIN customers c ON s.customer_id = c.id
WHERE s.amount > 500
```

### 窗口函数

```sql
-- 累计求和 + 排名
SELECT order_date, amount,
       SUM(amount) OVER (ORDER BY order_date) as running_total,
       RANK() OVER (ORDER BY amount DESC) as amount_rank
FROM Sales
```

### 数据透视风格分析

```sql
-- 透视：各品类的月度营收
SELECT category,
       SUM(CASE WHEN MONTH(date) = 1 THEN revenue END) as Jan,
       SUM(CASE WHEN MONTH(date) = 2 THEN revenue END) as Feb,
       SUM(CASE WHEN MONTH(date) = 3 THEN revenue END) as Mar
FROM Sales
GROUP BY category
```

## 完整示例

用户上传 `sales_2024.xlsx`（含 sheets：`Orders`、`Products`、`Customers`），并问："Analyze my sales data — show top products by revenue and monthly trends."

### 第 1 步：检查文件

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/sales_2024.xlsx \
  --action inspect
```

### 第 2 步：营收 Top 产品

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/sales_2024.xlsx \
  --action query \
  --sql "SELECT p.product_name, SUM(o.quantity * o.unit_price) as total_revenue, SUM(o.quantity) as total_units FROM Orders o JOIN Products p ON o.product_id = p.id GROUP BY p.product_name ORDER BY total_revenue DESC LIMIT 10"
```

### 第 3 步：月度营收趋势

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/sales_2024.xlsx \
  --action query \
  --sql "SELECT DATE_TRUNC('month', order_date) as month, SUM(quantity * unit_price) as revenue FROM Orders GROUP BY month ORDER BY month" \
  --output-file /mnt/user-data/outputs/monthly-trends.csv
```

### 第 4 步：统计摘要

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/sales_2024.xlsx \
  --action summary \
  --table Orders
```

把结果以清晰的发现、趋势、可执行洞察呈现给用户。

## 多文件示例

用户上传 `orders.csv` 和 `customers.xlsx`，并问："Which region has the highest average order value?"

```bash
python /mnt/skills/public/data-analysis/scripts/analyze.py \
  --files /mnt/user-data/uploads/orders.csv /mnt/user-data/uploads/customers.xlsx \
  --action query \
  --sql "SELECT c.region, AVG(o.amount) as avg_order_value, COUNT(*) as order_count FROM orders o JOIN Customers c ON o.customer_id = c.id GROUP BY c.region ORDER BY avg_order_value DESC"
```

## 输出处理

分析完成后：

- 在对话中以格式化表格直接展示查询结果
- 结果较大时，导出到文件并通过 `present_files` 工具分享
- 用平实语言解释发现与关键结论
- 发现有趣规律时，建议做进一步分析
- 用户希望保留结果时主动提供导出

## 缓存

脚本会自动缓存已加载的数据，避免每次调用都重解析文件：

- 首次加载时，文件被解析并存入 `/mnt/user-data/workspace/.data-analysis-cache/` 下的持久化 DuckDB 数据库
- 缓存键是所有输入文件内容的 SHA256 哈希 —— 文件变化时会创建新缓存
- 之后用同样文件的调用会直接命中缓存（启动几乎瞬时）
- 缓存对用户透明 —— 无需额外参数

这在多次跑同一组文件的查询时尤其有用（inspect → query → summary）。

## 注意事项

- DuckDB 支持完整 SQL，包括窗口函数、CTE、子查询、复杂聚合
- Excel 的日期列会自动解析；可直接用 DuckDB 的日期函数（`DATE_TRUNC`、`EXTRACT` 等）
- 对超大文件（100MB+），DuckDB 无需把全部内容加载到内存即可高效处理
- 含空格的列名要用双引号引用，例如 `"Column Name"`
