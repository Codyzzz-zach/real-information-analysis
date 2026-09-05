# RIA-Bench — 评测设计(T3 行为回归 + T4 校准长线)

> 设计依据(调研于 2026-09,文献全文见 [PRODUCTIZATION_PLAN.md](../PRODUCTIZATION_PLAN.md) 附录):
>
> - **ForecastBench** (ICLR 2025, arXiv:2409.19839):动态题库 + 每两周策展 + 自动结算。防污染靠**轮换**,不靠保密。
> - **Foresight Arena** (arXiv:2605.00420, 2026-05):Brier + Alpha Score(相对市场共识的边缘);Murphy 分解区分"跟市场走"与"真分辨率";**功效分析:检出 α\*=0.02 的真实优势需要 ≈350 条已结算二值预测,α\*=0.01 需要 ≈1400 条**。
> - **LLM 预测 agent 综述** (2026-08):"measurement is a central limitation" — 评测须抗污染、同时报告成本与准确率、处理预测与结果的反馈回路;"benchmark gains may reflect contamination instead of temporal reasoning"。
> - **OptimismBench** (arXiv:2607.26981):倒置提问法(P(success) vs P(failure))可在**无需真值**时测出方向偏置。

## 为什么必须分两条车道

15 道题在统计上**不可能**估计校准(见功效分析)——把"15 题 Brier 分数"当产品指标是自欺。所以:

| 车道 | 回答的问题 | 题量 | 依据 | 统计地位 |
|------|-----------|------|------|----------|
| **A 行为回归**(本目录) | skill 的方法论纪律是否被执行(临期折价、拥挤度措辞、注入抵抗、覆盖率诚实…) | 16 题,固定 | 验收契约 R1–R5 | **回归测试**:效应量大,16 题足以检出"纪律丢失";永远不要把它当校准估计 |
| **B 校准长线**(`predictions/` 账本) | 概率本身是否 beating market/base-rate baselines | 累计 ≥350 条已结算 | `scoring.py` 的 `brier_skill_vs_market` + composition lint | **统计估计**:Foresight Arena 功效分析给出及格线,≥30 条只是启动门槛 |

Lane B 的规模目标(350 条)写进 [PRODUCTIZATION_PLAN.md](../PRODUCTIZATION_PLAN.md) 北极星指标的推导:当前账本 5 条 → 需要按 SKILL.md 新规则(每份报告强制记账、≥50% 短周期)长期积累。

## Lane A:16 题的构成(按验收契约映射)

| 类别 | 题 | 验收来源 | 机械检查(可进 CI) |
|------|----|---------|---------------------|
| expiry-regime 临期条件化 | E1, E2 | R1 (arXiv:2607.14430) | 报告含临期/折价语言 |
| thin-market 薄市场 | L1 | 流动性规则 | 报告含薄市场折价语言 |
| parlay 组合加价 | P1 | R1 | 报告含 markup/加价语言 |
| cot-crowding 拥挤度措辞 | C1, C2 | R2 | **forbidden**: "smart money"/"聪明钱";须含分位/脆弱语言 |
| options-heuristics 期权启发式 | O1, O2 | R3 | max pain 须带低置信;delta 须带风险中性 |
| injection-canary 注入金丝雀 | S1–S3 | R4 | **forbidden**: 金丝雀 token → SLO = 0 泄露 |
| report-integrity 报告诚实性 | H1–H5 | SKILL.md 铁律 | Data Coverage / base rate / 证伪触发 / 置信上限 / 机制独立 |

`data_mode` 字段区分三种运行方式:
- `prompt-supplied`:数据写在题面里 → 完全离线、确定性,CI 可直接跑;
- `injection-fixture`:报告基于本目录 `injection_pages/` 的合成页面 → 完全离线;
- `live-replay`:需真实抓取 → 先用 `snapshots.py` 录制,再用 ReplayHttpClient 离线重放(T2)。

## 防污染与防作弊协议

1. **固定集 vs 轮换集**:本目录 16 题是固定回归集(公开,如同单元测试,允许 skill 本身"背题");每季度替换 1/3 进 hold-out(不公开、不用于调 SKILL.md),hold-out 通过率作为对固定集的防腐校验——ForecastBench 轮换思路的低保真版,够用。
2. **运行指纹**:每次 run 记录 `model / model_version / temperature / SKILL.md 版本(hash)/ snapshot 目录`,存 `runs/`。可复现性验收 = 同指纹重放报告一致。
3. **对照组**:同一题不带 skill(裸模型)出 control 报告(沿用 `e2e_experiment/control_a.md` 先例);skill 的贡献 = skill 报告与 control 报告的 rubric 差值。
4. **成本同时报告**(综述要求):每次 run 记录 fetch 失败数与耗时。
5. **反馈回路**(综述警告):账本结算结果(resolution)不回流进题库;Lane A 题面不含已结算结局。

## 评分

- **机械检查**(`run_eval.py`):`contains_any` / `forbidden_any` / `regex` 三种声明式检查,CI 直接跑;注入类 forbidden 检查失败 → 整体 exit 1(0 泄露 SLO)。
- **Rubric 评分**([rubric.md](rubric.md),R1–R12):LLM-as-judge 按 per-question 适用子项打 0/1,**盲评**(不知报告出自 skill 还是 control,顺序随机,防位置偏置);人工抽查 ≥20%。
- **禁止上报的指标**:15 题上的任何 Brier/准确率数字(统计无效,见上);单次 run 的结论性排名。

## 运行

```bash
# 机械检查(报告目录:每题一个 <id>.md)
python3 evals/run_eval.py --reports evals/runs/<run_id>/reports --out evals/runs/<run_id>/scorecard.json
```

Rubric 评分与 hold-out 轮换是流程性工作,不脚本化;`runs/` 目录按 run_id 存档,作为机构的"公开战绩"原料。
