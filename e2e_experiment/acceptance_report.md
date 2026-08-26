# RIA Skill E2E 验收报告

> 实验：在 WorkBuddy 上对 real-information-analysis（v1.1.0）执行端到端测试
> 测试问题：「中美 AI 以后是否会发展出统一的生态？」
> 执行日期：2026-08-27 | 指令源：SKILL.md sha256 6705a8ce | 环境：WorkBuddy 沙箱，Python 3.13.12
> 数据模式：真实网络优先 | 对照组：裸 agent（对照 A）

## 总体结论

**PASS — 99/100**。skill 方法论在 WorkBuddy 环境被完整执行，无红线违规。核心结论建立在真实获取的 SEC EDGAR 资本数据之上，MISSING 信号全部诚实报告，预测已按规范登记。

## A. 加权评分（100 分）

| 关卡 | 权重 | 得分 | 扣分说明 |
|------|------|------|---------|
| R0 环境就绪 | 10 | 10/10 | 指令源确认为 v1.1.0；15 provider 全部可 import；Python 可运行 |
| R1 数据管道 | 20 | 20/20 | 15 源连通矩阵实测；gather 并行+超时+部分失败容错生效；Data Coverage 14 行逐信号 OK/MISSING + 原因 + 影响维度；Deribit 瞬时抖动（探针 OK/主实验 MISSING）也如实报告 |
| R2 方法论遵循 | 30 | 29/30 | 4 类机制（已承诺资本/知情者/宏观基准率/情绪）扣除共同因子后 ≥3；反证测试、时间分层、base rate、权重判断、置信度上限均达标。**扣 1 分**：Step 3 信号路由的 4 项检查未在报告中显式留痕 |
| R3 输出结构 | 20 | 20/20 | 四主 section 齐全；概率表含 market_implied + base_rate 双列（market_implied 诚实 null）；5 条 falsification triggers 绑定结论；MISSING 导致置信度显式下调 |
| R4 登记合规 | 15 | 15/15 | 3 条预测入 predictions/2026-08.jsonl；resolution_criteria 客观可核查；resolve_by 2031-12-31 ≤ 5 年窗口；probability 锁定 |
| R5 长期裁决 | 5 | 5/5 | 结算机制已建立（到期按 criteria 判定 + score_predictions.py + Brier） |
| **合计** | 100 | **99** | |

## B. 行为红线核查（一票否决 / 重罚）

| 红线 | 结果 | 证据 |
|------|------|------|
| 编造数据 | ✅ 通过 | 报告中所有数字（R&D 金额、内部人股数、利率、GDP）均来自 provider 真实输出（main_data.json） |
| proxy 冒充直接市场定价 | ✅ 通过 | market_implied 列全部显式 null，概率标注"proxy 推断，非市场投票" |
| 隐藏 MISSING 信号 | ✅ 通过 | 14 行 Data Coverage 全列，含 3 个 MISSING 预测市场源 + 6 个封锁源 |
| 超置信度上限 | ✅ 通过 | >3yr + 无直接合约 → 置信度上限 Medium，未上调 |

## C. 对照 A（裸 agent）对比 — skill 增量

| 维度 | 裸 agent | RIA skill | 增量 |
|------|---------|-----------|------|
| 数据 | 无（自认"主观推演"） | 19 家公司 R&D 轨迹 + 20 笔内部人 + 利率/GDP/情绪 | 从 0 到可审计的真实数据 |
| 概率 | 单一 30%（对"完全统一"） | 3 场景互斥穷尽：15% / 25% / 60%，双基线对照 | 结构化的可评分概率 |
| 可证伪性 | 无判定条件 | resolution_criteria + 5 条监控触发器 | 从"观点"到"可结算预测" |
| 事后评分 | 无法评分 | Brier score + 校准分桶 | 方法论可被长期裁决 |

> 有趣发现：裸 agent 的**定性结论方向**（"双生态+有限互通"）与 skill 一致，但 skill 给出了数据证据链、机制区分、可证伪条件和可结算登记——**skill 的增量不在"结论"，在"证据与可审计性"**。

## D. 发现的问题与改进建议（按优先级）

| # | 问题 | 类别 | 建议 |
|---|------|------|------|
| 1 | Step 3 信号路由 4 项检查未在报告显式留痕 | skill 文档/流程 | 在 SKILL.md Step 6 模板的 Data Coverage 前增加可选 "Signal Routing" 小节，要求列出每信号的 4 项检查结论 |
| 2 | Deribit 网络不稳定（探针 OK / 主实验 MISSING） | 环境 | gather 对可重试的网络错误建议内置 1 次重试；或在报告中标注"瞬时抖动 vs 持续封锁" |
| 3 | WorkBuddy 沙箱仅 6/15 源可达（EDGAR/BIS/WB/Treasury/Deribit/FearGreed 可用；Polymarket/Kalshi/Yahoo/Stooq/CFTC/CoinGecko/WebSearch/FRED 不可达） | 环境 | 测试场景分为"沙箱内真实网络"与"解锁沙箱全源"两档；快照回放（ReplayHttpClient）作为 CI 回归基线 |
| 4 | 中国侧 R&D 样本限于 SEC ADR（腾讯/华为/字节不可观测） | 方法论已知局限 | 在 SKILL.md 中国相关段落补充显式样本偏差警告（本次报告已自行标注） |
| 5 | 探针脚本对 provider 返回类型字段名踩坑（bis `rate` 非 `rate_pct`、treasury `value` 非 `yield_pct`） | 脚手架/文档 | references/providers.md 可补充各 Result 类型字段速查表，降低集成成本 |

## E. 实验产物清单

| 文件 | 内容 |
|------|------|
| `e2e_experiment/report.md` | 主实验完整报告（Step 1-6 全流程产出） |
| `e2e_experiment/control_a.md` | 对照 A 裸 agent 原始回答 + 对比表 |
| `e2e_experiment/probe_result.json` | 15 源连通性矩阵 |
| `e2e_experiment/main_data.json` | 主实验全部原始数据（EDGAR 19 家 + 内部人 20 笔 + 宏观） |
| `predictions/2026-08.jsonl` | 3 条场景预测登记（2031-12-31 结算） |
| `e2e_experiment/probe_connectivity.py` / `main_data_gather.py` | 一次性实验脚本（未改动任何 skill 代码） |

---
*结算预案：2031-12-31 到期后按 resolution_criteria 判定 outcome，运行 `python3 scripts/score_predictions.py` 计算 Brier 与校准。*
