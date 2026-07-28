# 产业资本开支 — 公司速查

供 `EdgarProvider.get_capital_trends(tickers=[...], concept=..., years=N)` 使用。

> **设计原则**：这份表是**参考锚点，不是约束**。它给 AI 一个稳定的默认起点（避免凭记忆拍脑袋、保证结论可复现），但 AI 完全可以按问题需要自由增删公司。遇到表中没有的新兴产业（如量子计算、合成生物学），AI 应自行补充相关 ticker。
>
> **广覆盖原则**：产业判断时尽量覆盖**全产业链 10-25 家**，不只取 2-3 个龙头。更广的覆盖能暴露结构性模式（如"23/25 家在扩张=全行业共识"），少数龙头无法呈现。

## 各产业推荐 concept

不同产业的"资本投入"性质不同，应选择对应的 XBRL 概念：

| 产业类型 | 推荐 concept | 理由 |
|----------|-------------|------|
| 芯片设计 / 软件 / 医药 / 生物科技 | `R&D` | 研发驱动，R&D 是核心投入指标 |
| 代工 / 公用事业 / 能源 / 重工业 | `PP&E` | 资产驱动，净资产厂房设备反映产能基座 |
| 制造 / 国防 / 航天 | `R&D` 或 `PP&E` | 兼有研发和重资产，按问题侧重选 |

> `CapEx`（PaymentsToAcquireProductiveAssets）覆盖面较窄（部分公司用其他标签），稳定性不如 R&D 和 PP&E。

## 半导体 / AI 硬件全链

分析"AI/算力竞争"时，覆盖芯片设计 → 代工 → 设备 → 封测四层，不要只看 GPU 龙头。

### 芯片设计 / Fabless（concept: `R&D`）

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `NVDA` | NVIDIA | AI 算力需求晴雨表（GPU/数据中心） |
| `AMD` | AMD | CPU+GPU 追赶者（EPYC/Instinct） |
| `INTC` | Intel | 传统 CPU 龙头，代工转型中（掉队信号观察） |
| `AVGO` | Broadcom | 定制 AI ASIC + 网络芯片 |
| `QCOM` | Qualcomm | 移动 SoC + 汽车芯片 |
| `MRVL` | Marvell | 数据中心网络 + 定制 AI ASIC |
| `ARM` | Arm Holdings (ADR) | CPU/GPU IP 架构授权（全行业基础） |
| `TXN` | Texas Instruments | 模拟芯片龙头（工业/汽车） |
| `ADI` | Analog Devices | 高性能模拟芯片 |
| `MCHP` | Microchip Technology | MCU + 模拟（工业/嵌入式） |
| `MPWR` | Monolithic Power | 服务器/AI 电源管理芯片（⚠️ R&D 数据可能停在早期，需验证） |

### 代工 / 制造

> ⚠️ **代工厂用 IFRS 命名空间（`ifrs-full`），不是 `us-gaap`**。`get_capital_trends` 当前只查 us-gaap 概念，因此 **TSM/UMC/GFS 的 XBRL 财务数据取不到**。台积电的资本开支需通过其他渠道（新闻/财报 PDF）或观察其设备供应商（ASML/AMAT/KLAC/LRCX）的订单趋势间接推断。

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `TSM` | 台积电 (ADR, 20-F, **IFRS**) | 全球逻辑代工龙头（算力产能瓶颈）— XBRL 不可取 |
| `UMC` | 联电 (ADR, 20-F, **IFRS**) | 成熟制程代工 — XBRL 不可取 |
| `GFS` | GlobalFoundries (20-F, **IFRS**) | 美国差异化/RF 代工 — XBRL 不可取 |

**替代方案**：分析代工产能时，看**设备商**（ASML/AMAT/KLAC/LRCX）的 R&D 和收入趋势——它们的订单领先于代工厂扩产。

### 半导体设备（concept: `R&D` 或 `PP&E`）

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `ASML` | ASML (ADR, 20-F) | EUV 光刻垄断者（先进制程咽喉） |
| `AMAT` | Applied Materials | 材料工程/沉积/刻蚀设备 |
| `KLAC` | KLA | 晶圆检测/工艺控制 |
| `LRCX` | Lam Research | 刻蚀/沉积设备 |
| `ON` | ON Semiconductor | 汽车电源/传感器（concept 用 `PP&E`） |

### 封装测试 / OSAT（concept: `PP&E`）

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `AMKR` | Amkor (20-F) | 先进封装龙头 |
| `UCTT` | Ultra Clean | 晶圆厂设备子系统 |

## 云计算 / AI 软件

分析"AI 软件/云"投入强度时覆盖 hyperscaler + SaaS + 安全。

**concept: `R&D`**

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `MSFT` | Microsoft | Azure + Copilot AI |
| `GOOGL` | Alphabet | Google Cloud + Gemini |
| `AMZN` | Amazon | AWS（注：R&D 可能需查 `TechnologyAndContent`，建议加 PP&E 辅助） |
| `META` | Meta | 社交 + AI 研究/推理基础设施 |
| `ORCL` | Oracle | 云基础设施 + 数据库 |
| `CRM` | Salesforce | CRM SaaS + Agentforce AI |
| `IBM` | IBM | 混合云 + watsonx |
| `PLTR` | Palantir | 政府/企业 AI 数据分析 |
| `NOW` | ServiceNow | 工作流 SaaS + AI |
| `SNOW` | Snowflake | 数据云/AI 数据平台 |
| `DDOG` | Datadog | 云可观测性 |
| `MDB` | MongoDB | 开发者数据库 |
| `NET` | Cloudflare | 边缘网络/安全/Workers AI |
| `INTU` | Intuit | 财税 SaaS + GenAI |
| `ADBE` | Adobe | 创意/营销 SaaS + Firefly（注：R&D 标签可能不同，建议验证） |
| `CRWD` | CrowdStrike | 云端点安全 |
| `PANW` | Palo Alto Networks | 企业网络安全 |

## 国防 / 航空航天

分析"地缘冲突/军备扩张"时使用。

**concept: `R&D`（武器系统研发）或 `PP&E`（产能基座）**

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `LMT` | Lockheed Martin | 第一梯队（F-35/导弹/太空） |
| `RTX` | RTX (前 Raytheon) | 导弹/防空/航空发动机 |
| `GD` | General Dynamics | 战车/舰船/IT |
| `NOC` | Northrop Grumman | 隐身轰炸机/导弹/太空 |
| `BA` | Boeing | 商用飞机 + 国防 |
| `KTOS` | Kratos | 无人系统/高超声速 |
| `IRDM` | Iridium | 卫星星座 |
| `HWM` | Howmet Aerospace | 航空发动机零部件 |
| `TDG` | TransDigm | 专有航空零部件 |

## 能源转型 / 清洁能源

分析"能源转型/碳中和"进度时使用。

**concept: `PP&E`（资产驱动型产业）**

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `NEE` | NextEra Energy | 全球最大风/光公用事业开发商 |
| `ENPH` | Enphase | 住宅微型逆变器+储能 |
| `FSLR` | First Solar | 美国薄膜光伏组件 |
| `SEDG` | SolarEdge | 光伏逆变器 |
| `BE` | Bloom Energy | 燃料电池（数据中心/氢能） |
| `RUN` | Sunrun | 住宅太阳能安装 |
| `PLUG` | Plug Power | 氢电解槽/燃料电池 |
| `CHPT` | ChargePoint | 电动汽车充电网络 |
| `ARRY` | Array Technologies | 公用事业太阳能跟踪器 |

## 医药 / 生物科技

分析"医药创新周期/管线价值"时使用。注意医药行业部分公司用 `ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost` 变体。

**concept: `R&D`**

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `LLY` | Eli Lilly | GLP-1/糖尿病/肥胖龙头 |
| `MRK` | Merck | Keytruda 肿瘤 |
| `JNJ` | Johnson & Johnson | 制药+医疗技术 |
| `BMY` | Bristol-Myers Squibb | 肿瘤+心血管 |
| `REGN` | Regeneron | 眼科/免疫生物科技 |
| `GILD` | Gilead | 抗病毒+肿瘤 |
| `VRTX` | Vertex | 囊性纤维化+镰状细胞 |
| `MRNA` | Moderna | mRNA 平台 |
| `BMRN` | BioMarin | 罕见病酶替代 |
| `HALO` | Halozyme | 药物递送技术授权 |

> **注**：`PFE`/`ABBV`/`BNTX` 等用 R&D 变体标签，`get_capital_trends` 当前 concept 映射可能取不到——如需覆盖，AI 可自行用底层 API 查询变体标签。

## 中国科技（ADR，全部 20-F）

分析"中美科技竞争"时的中国侧数据。

**concept: `R&D`**

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `BABA` | 阿里巴巴 | 电商+云+物流 |
| `BIDU` | 百度 | 搜索+AI+自动驾驶 |
| `PDD` | 拼多多 | 电商（拼多多/Temu） |
| `JD` | 京东 | 电商+物流 |
| `NTES` | 网易 | 游戏+云音乐 |
| `BILI` | 哔哩哔哩 | Z 世代视频平台 |

> **局限**：腾讯/美团/小米等不在 SEC 上市（港交所），无法通过此渠道获取。中国侧数据覆盖限于在美 ADR。

## 金融科技 / 支付

**concept: `PP&E`**（V/MA 等网络型公司无 R&D，用资产基座或运营成本）

| Ticker | 公司 | 信号含义 |
|--------|------|---------|
| `V` | Visa | 全球支付网络 |
| `XYZ` | Block (前 Square) | Cash App + Square |
| `PYPL` | PayPal | 数字钱包 |
| `AFRM` | Affirm | 先买后付 |

> **注**：`MA`/`FIS`/`FISV` 在 XBRL 里用非标准标签，可能取不到标准 concept。

---

## 使用建议

1. **先按产业定位选 concept**（R&D vs PP&E），避免代工厂查 R&D 拿空结果。
2. **覆盖全链**，不只取龙头——结构性模式藏在长尾公司里。
3. **遇到取不到的公司**：换 concept（R&D↔PP&E），或该公司可能用变体标签。
4. **这份表可扩展**：发现遗漏的代表性公司时，往对应产业组补充。
