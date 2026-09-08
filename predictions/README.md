# Predictions Ledger — 预测登记簿

每份分析报告的概率估计都必须登记到这里。这是整个方法论的最终裁判：
**Brier score 不关心理论，只关心你预测 30% 的事情是否真的大约有 30% 发生。**

## 文件约定

- 文件名：`YYYY-MM.jsonl`（按 `created_at` 月份归档）
- 格式：JSON Lines — 每行一个 JSON 对象，一行一条预测
- 空行和 `#` 开头的注释行会被忽略

## 记录格式

每份报告 Step 6 "Probability Estimates" 表里的**每一行场景**登记为一条记录：

```json
{"question": "Will there be a US recession in 2026?", "scenario": "NBER declares a recession covering any month of 2026", "probability": 0.25, "market_implied": 0.20, "base_rate": 0.15, "created_at": "2026-07-29", "resolve_by": "2027-01-31", "resolution_criteria": "NBER officially declares a recession whose range includes any month of 2026", "outcome": null}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `question` | ✅ | 原始问题 |
| `probability` | ✅ | 最终概率，0–1（报告里 Final 列的值） |
| `scenario` | 推荐 | 具体场景描述 |
| `market_implied` | 推荐 | 市场隐含概率（用于对照"直接信市场"的基线） |
| `base_rate` | 推荐 | 历史基础概率（用于对照"直接用 base rate"的基线） |
| `created_at` | ✅ | 登记日期 YYYY-MM-DD |
| `resolve_by` | ✅ | 结算截止日，不得超过报告使用的时间窗口 |
| `resolution_criteria` | ✅ | **客观可核查**的判定条件：谁宣布、以什么为准、截至何时 |
| `outcome` | ✅ | `null` = 未结算；到期后填 `true`/`false`（也接受 `1`/`0`/`yes`/`no`） |
| `resolved_at` | 结算时填 | 实际结算日期 |
| `market_ref` | 推荐（有 `market_implied` 时） | 该市场读数来自哪个子市场——子市场的问题原文或 ticker（如 `KXFED-26DEC-T3.75`）。多结果事件里每个子市场是独立价格，不记 ref 的 market_implied 无法审计 |
| `mutually_exclusive_group` | 可选 | 互斥完备的若干条目共用一个组 id；`ledger_coherence_lint` 会检查组内概率之和 ≤ 1。**只给真正互斥完备的组打**（"hold vs hike vs cut" 是组，"hold vs hike" 不是——缺了 cut 就不完备） |
| `notes` | 可选 | 备注 |

## 一致性规则（`--check-coherence`）

登记时必须满足，`python3 scripts/score_predictions.py --check-coherence` 可机械检查：

- **同一极性**：`question`、`scenario`、`resolution_criteria` 必须断言同一事件、同一方向。反例（真实发生过）：question 问"会降息吗？"，scenario 写"按兵不动"，结算标准写"未下调即 true"——审计时读起来是"97% 预测降息"，实际登记的是"97% 预测不降息"。极性颠倒本身的语义无法 100% 机器判定，lint 只抓它的**痕迹**（negated market_ref、组内求和越界）。
- **嵌套事件单调性**："9 月加息"是"年内加息"的子集，所以 P(年内加息) ≥ P(9 月加息)。登记嵌套事件对时自查。
- **读数可溯源**：每条 `market_implied` 都要有 `market_ref`。多结果事件（Fed cut 阶梯、strike ladder）里每个子市场是独立价格——你读到的 0.375 可能是"≥4.00%"档而不是"任意加息"，没有 ref 就无法发现。

## 工作流

1. **登记**：生成报告时，把概率估计逐条追加到当月 jsonl（`outcome: null`）
2. **结算**：`resolve_by` 到期后，按 `resolution_criteria` 判定，把 `outcome` 改为 `true`/`false` 并填 `resolved_at`。**不许修改 `probability`** — 事后改数等于销毁证据
3. **评分**：定期运行

```bash
python3 scripts/score_predictions.py              # 默认读 predictions/
python3 scripts/score_predictions.py predictions/2026-07.jsonl --buckets 5
```

输出三个核心指标：

- **Brier score** — 概率预测的均方误差，0 = 完美，0.25 = 全部猜 50%
- **vs base-rate / market-implied baseline** — 你的方法是否真正跑赢了"只看基础概率"和"只信市场"这两个懒惰基线（skill > 0 才算有增量价值）
- **Calibration 分桶** — 预测 70–80% 的事件实际发生了多少；系统性偏离 = 系统性过度自信或自信不足

## 规则

- `resolution_criteria` 必须在登记时写死，结算时不得重新解释
- 无法写出客观判定条件的预测**不允许登记**（不可证伪的判断没有资格进 ledger）
- 结算时只求"判定诚实"，不求"结果好看"—— ledger 的价值随条目数增长，几十条之后校准曲线才开始说话
