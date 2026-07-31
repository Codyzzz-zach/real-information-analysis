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
| `notes` | 可选 | 备注 |

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
