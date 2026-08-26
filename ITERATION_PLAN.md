# Real Information Analysis — 新数据源迭代计划

## 产品定位

Real Information Analysis 的核心哲学：**"所有公开信息都已经被价格消化了。一切信息都在 K 线里。"**

新数据源的标准：
- 必须是**真金白银的市场交易数据**（价格、利差、波动率、持仓量），不是观点/预测/投票
- 必须是**结构化 API**，不是需要 web search 碰运气的数据
- 优先**零外部依赖**（纯 Python stdlib + urllib）
- 遵循项目现有的 provider 模式（见下方规范）

---

## 项目代码规范

### Provider 模式

所有 provider 必须遵循的模式（参考 `bis.py`, `cftc.py` 等现有实现）：

1. **继承 `SignalProvider` 基类**（`from .base import SignalProvider`）
2. **依赖注入 HTTP 客户端**：构造函数接受可选的 `http_client` 参数，默认用 `UrllibJsonClient()`
3. **Query 用 `@dataclass(frozen=True)`**，Result 用 `@dataclass` 或 `@dataclass(frozen=True)`
4. **数值解析用 `_coerce_float()` / `_coerce_int()`**（从 `._coerce` 导入）
5. **错误用 `ProviderParseError`**（从 `.base` 导入）
6. **文件放 `real_information_analysis/providers/` 目录下**

### HTTP 客户端

项目提供 `UrllibJsonClient`（`real_information_analysis/http.py`），支持：
- `get_json(url, params={...})` — 返回解析后的 JSON
- `get_text(url, params={...})` — 返回原始文本（用于 CSV）
- 自带重试（3 次）和超时（20 秒）
- 重要：使用 `Mapping[str, object]` 作为 params 类型，值为 `None` 的参数会被自动过滤

### 测试规范

每个 provider 有对应的 `tests/test_{name}_provider.py`：
- 使用 `unittest.TestCase`
- 用 **Fake HTTP Client** 注入预设响应（不做真实网络请求）
- 测试正常解析、空响应、异常数据等情况
- 参考 `tests/test_bis_provider.py` 的模式

### 导出规范

新 provider 的 Query 和 Result 类型必须在以下位置注册导出：
1. `real_information_analysis/providers/__init__.py` — import 并加入 `__all__`
2. `real_information_analysis/__init__.py` — import 并加入 `__all__`

### 文档规范

- `references/providers.md` — 添加 API 速查章节
- `SKILL.md` — 在 Step 2 信号菜单和 Step 3 代码示例中引用

---

## 迭代 1：FredProvider（FRED 美联储经济数据库）

### 为什么

现在 VIX、高收益债利差（OAS）、MOVE 指数、利差等关键市场价格数据全靠 web search 获取，不稳定且不结构化。FRED 提供 84 万条时间序列，覆盖利率、利差、波动率等核心价格信号。

### API 概要

- Base URL: `https://api.stlouisfed.org/fred`
- 认证: 需要免费 API key（注册 https://fredaccount.stlouisfed.org/apikeys ）
- 格式: JSON
- 无外部依赖

### 需要实现的方法

#### 1. `get_series(query: FredSeriesQuery) -> FredSeries`

获取一条时间序列的数据点。

```python
@dataclass(frozen=True)
class FredSeriesQuery:
    series_id: str              # e.g. "VIXCLS", "BAMLH0A0HYM2"
    observation_start: str | None = None  # YYYY-MM-DD
    observation_end: str | None = None    # YYYY-MM-DD
    limit: int | None = None
    sort_order: str = "desc"    # "asc" or "desc"

@dataclass(frozen=True)
class FredObservation:
    date: str       # YYYY-MM-DD
    value: float

@dataclass
class FredSeries:
    series_id: str
    title: str | None
    frequency: str | None          # e.g. "Daily", "Monthly"
    units: str | None              # e.g. "Percent", "Index"
    observations: tuple[FredObservation, ...]
```

API endpoint: `GET /fred/series/observations`
- params: `series_id`, `api_key`, `file_type=json`, `observation_start`, `observation_end`, `limit`, `sort_order`
- 返回 JSON: `{"observations": [{"date": "2026-04-10", "value": "19.23"}, ...]}`
- 注意: value 为字符串，"." 表示缺失值，需要过滤

#### 2. `search_series(query: FredSearchQuery) -> list[FredSeriesInfo]`

按关键词搜索序列。

```python
@dataclass(frozen=True)
class FredSearchQuery:
    search_text: str
    limit: int = 20

@dataclass(frozen=True)
class FredSeriesInfo:
    series_id: str
    title: str
    frequency: str | None
    units: str | None
    observation_start: str | None
    observation_end: str | None
    popularity: int | None
```

API endpoint: `GET /fred/series/search`
- params: `search_text`, `api_key`, `file_type=json`, `limit`

### Provider 构造

```python
class FredProvider(SignalProvider):
    provider_id = "fred"
    display_name = "FRED (Federal Reserve Economic Data)"
    capabilities = ("economic_series", "series_search")

    def __init__(self, api_key: str, http_client: FredHttpClient | None = None):
        self.api_key = api_key
        self.http_client = http_client or UrllibJsonClient()
```

注意: `api_key` 是必传参数（FRED 要求认证）。

### SKILL.md 集成

在 Step 2 信号菜单中将以下 web search 项替换为 FRED：

| 原来（web search） | 替换为（FRED series_id） |
|---|---|
| "VIX index current" | `VIXCLS` (CBOE Volatility Index) |
| "US high yield OAS spread" | `BAMLH0A0HYM2` (ICE BofA US High Yield OAS) |
| "MOVE index" | `MOVE` (ICE BofAML MOVE Index)  |
| 10Y-2Y spread | `T10Y2Y` (10-Year minus 2-Year Treasury) |
| TED spread | `TEDRATE` |
| Margin debt | `BOGZ1FL663067003Q` |
| US breakeven inflation | `T10YIE` (10-Year Breakeven Inflation) |
| Initial jobless claims | `ICSA` |
| US CPI | `CPIAUCSL` |

在 Step 3 的 gather 示例中添加:
```python
fred = FredProvider(api_key="YOUR_FRED_API_KEY")  # free at https://fredaccount.stlouisfed.org/apikeys

result = gather({
    # ... existing providers ...
    "vix": lambda: fred.get_series(FredSeriesQuery(series_id="VIXCLS", limit=30)),
    "hy_spread": lambda: fred.get_series(FredSeriesQuery(series_id="BAMLH0A0HYM2", limit=30)),
    "move": lambda: fred.get_series(FredSeriesQuery(series_id="MOVE", limit=30)),
    "t10y2y": lambda: fred.get_series(FredSeriesQuery(series_id="T10Y2Y", limit=30)),
})
```

### 测试要求

在 `tests/test_fred_provider.py` 中：

```python
SAMPLE_OBSERVATIONS_JSON = {
    "observations": [
        {"date": "2026-04-10", "value": "19.23"},
        {"date": "2026-04-09", "value": "21.05"},
        {"date": "2026-04-08", "value": "."},      # missing value, should be skipped
    ]
}

SAMPLE_SEARCH_JSON = {
    "seriess": [
        {
            "id": "VIXCLS",
            "title": "CBOE Volatility Index: VIX",
            "frequency": "Daily",
            "units": "Index",
            "observation_start": "1990-01-02",
            "observation_end": "2026-04-10",
            "popularity": 95,
        }
    ]
}
```

测试用例：
1. 正常解析 observations（含跳过 "." 缺失值）
2. 空 observations 返回空 tuple
3. search_series 正常解析
4. 验证 api_key 被传入请求参数
5. limit 和 sort_order 参数传递正确

---

## 迭代 2：FearGreedProvider（CNN Fear & Greed Index）

### 为什么

CNN Fear & Greed Index 是一个**完全基于市场交易数据衍生**的综合情绪指标，由 7 个价格信号加权合成：
1. 股票价格动量（S&P 500 vs 125 日均线）
2. 股票价格强度（52 周新高 vs 新低）
3. 股票价格波幅（VIX）
4. Put/Call 期权比率
5. 垃圾债需求（高收益债 vs 投资级利差）
6. 市场波动率（VIX 偏离度）
7. 避险需求（股票 vs 债券收益率差）

**它不是观点聚合，是 7 个价格信号的合成**，完全符合 Real Information Analysis 的产品哲学。

### API 概要

- URL: `https://production.dataviz.cnn.io/index/fearandgreed/graphdata`
- 认证: 无需 API key
- 格式: JSON
- 无外部依赖

### 需要实现的方法

#### `get_index() -> FearGreedSnapshot`

```python
@dataclass(frozen=True)
class FearGreedSnapshot:
    score: float            # 0-100, 0=Extreme Fear, 100=Extreme Greed
    rating: str             # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    timestamp: str | None   # ISO timestamp
    previous_close: float | None
    one_week_ago: float | None
    one_month_ago: float | None
    one_year_ago: float | None

class FearGreedProvider(SignalProvider):
    provider_id = "fear_greed"
    display_name = "CNN Fear & Greed Index"
    capabilities = ("market_sentiment",)
```

注意：CNN 的 API 可能返回嵌套结构，需要从 `fear_and_greed` 字段提取 `score` 和 `rating`，从 `fear_and_greed_historical` 中提取历史对比值。请先用 `web.fetch_page` 或 curl 测试实际返回结构再实现。

### SKILL.md 集成

在 Step 2 的所有分类中添加：
- `FearGreedProvider: CNN Fear & Greed Index (composite of 7 price signals — momentum, breadth, VIX, put/call, junk bond demand, volatility, safe haven demand)`

在 provider 表格中添加一行：
```
| FearGreedProvider | Market sentiment composite | 7 price signals → 0-100 fear/greed score | stdlib |
```

### 测试要求

构造 fake JSON 响应测试正常解析、缺失字段、极端值（0, 100）。

---

## 迭代顺序和理由

| 优先级 | Provider | 依赖 | 理由 |
|--------|----------|------|------|
| **P0** | FredProvider | stdlib（需 API key） | 一举替换最多的 web search 补丁，覆盖 VIX/OAS/MOVE/利差 |
| **P1** | FearGreedProvider | stdlib | 10 分钟搞定，免费无 key，7 个价格信号的合成情绪指标 |
| ~~P2~~ | ~~CMEFedWatchProvider~~ | — | **已废弃**：CME endpoint 被 Akamai 在连接层永久封锁，curl_cffi 浏览器指纹亦无法突破。利率概率改用 Kalshi `KXFED` 直接二值合约定价（一手市场定价，优于期货推导）。 |

每完成一个迭代后：
1. 添加 provider 代码 + 测试
2. 在 `providers/__init__.py` 和 `__init__.py` 注册导出
3. 更新 `SKILL.md`（Step 2 信号菜单 + Step 3 代码示例 + provider 表 + Notes）
4. 更新 `references/providers.md`
5. 运行 `pytest tests/ -x -q` 确保全部通过
