# ChanceDetect

面向场外基金投资者的盘后日频量化选基工具：基于高流动性场内基金（ETF / LOF）的量价与技术面策略发现机会，严格限定标的池为**存在对应场外基金（ETF 联接基金 / 可场外申赎 LOF）**的品种，按跟踪标的指数智能聚合，一键获取对应场外 A/C 份额代码并支持历史信号回看。

完整设计文档见 [SPEC.md](./SPEC.md)。

---

## 核心特性

- **严格场内↔场外映射**：基于天天基金全量目录与详情元数据，仅保留有明确「ETF 联接基金」的场内 ETF 或本身可场外申赎的 LOF（含兄弟 C 类份额），目前覆盖 A 股 **1,375 只场内基金 → 2,893 只对应场外基金**。
- **混合双源高可靠采集**：
  - 行情主源（腾讯前复权日 K HTTPS）+ 行情备源（新浪日 K + 复权因子）自动降级；
  - 每次增量同步自动执行 5 日重叠窗口 **除权/分红漂移检测**，发现前复权基准变动自动全量重拉修正；
  - 入库前 OHLCV 质检、SQLite 映射表骤降保护（Drop Protection）与同步健康日志。
- **冷热分离嵌入式存储 + 百毫秒级过滤**：
  - **SQLite (WAL)** 存储元数据、映射、同步日志、策略预设与扫描快照；
  - **Parquet + DuckDB + Polars** 零拷贝列式存储（百万级日 K 仅 ~21MB），全市场 8 策略向量化筛选耗时 **~100ms**。
- **可插拔策略集合与轻量回看**：
  - 内置 8 个向量化策略（趋势突破 / 超跌回归 / 动量轮动），基于 Pydantic 自动生成前端参数表单；
  - 支持多策略 `OR / RESONANCE(共振) / AND` 组合、底线流动性过滤、按跟踪指数聚合去重与 C/A 份额偏好排序；
  - 集成 **TradingView Lightweight Charts**，支持个基 K 线、历史策略触发点打点与 T+1/5/10/20 胜率及收益统计。
- **盘后自动同步 & Mac 休眠补拉**：内置 APScheduler 工作日 `16:15` 增量同步，电脑休眠错过调度时下次启动/唤醒自动补拉。

---

## 快速开始（本地 Mac / Linux）

### 1. 环境准备
需要 Python 3.12+、[`uv`](https://docs.astral.sh/uv/)、Node.js 20+。

```bash
# 安装后端依赖
uv sync --extra dev

# 构建前端静态资源（FastAPI 将单端口统一托管）
cd frontend && npm install && npm run build && cd ..
```

### 2. 首次初始化数据（约 1 分钟）
```bash
# 拉取全市场场内↔场外严格映射表（~20s）
uv run chancedetect sync-metadata

# 拉取 5 年前复权日 K、基准指数并预计算指标宽表（~50s）
uv run chancedetect sync-bars
```

### 3. 启动 Web 看板
```bash
uv run chancedetect serve --host 127.0.0.1 --port 8000
```
打开浏览器访问 **http://127.0.0.1:8000** 即可使用！

### 开发模式（前后端热更新）
```bash
# 终端 1：后端 API
uv run chancedetect serve --reload

# 终端 2：前端 Vite（自动代理 /api -> 127.0.0.1:8000）
cd frontend && npm run dev
```

---

## 常用 CLI 命令

```bash
uv run chancedetect status                                           # 查看本地池子、Parquet 规模与同步健康日志
uv run chancedetect sync-metadata                                    # 刷新场内↔场外映射表
uv run chancedetect sync-bars                                        # 增量同步盘后日K并重算指标
uv run chancedetect scan --combine RESONANCE --min-hits 2 --share C  # 终端快速扫描多策略共振机会
uv run chancedetect serve --port 8000                                # 启动单端口 Web 服务与定时调度器
```

---

## Docker 一键部署

```bash
docker compose up -d --build
```
访问 `http://localhost:8000`，首次使用可在界面「数据源与基金池」点击「全量一键同步」拉取数据，持久化数据保存在宿主机 `./data` 目录。

---

## 扩展自定义策略

在 `backend/chancedetect/strategies/` 下新增继承 `BaseStrategy` 的策略类（声明 Pydantic `params_model` 并实现向量化 `evaluate()`），并在 `backend/chancedetect/strategies/registry.py` 的 `create_default_registry()` 中注册即可自动出现在 Web 看板与参数表单中。

---

## 运行测试

```bash
uv run pytest -v
uv run ruff check backend tests
```
