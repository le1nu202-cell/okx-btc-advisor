# Third-Party Notices

本项目使用下列直接依赖。版本来自 `requirements.txt`、`frontend/package.json` 与已锁定的 `frontend/pnpm-lock.yaml`。本文件仅汇总主要直接依赖；各依赖的完整许可证文本和传递依赖信息以其发布包为准。

## Python 运行时依赖

| 组件 | 版本 | 用途 | 许可证 |
|---|---:|---|---|
| FastAPI | 0.116.1 | 本机 REST 与 WebSocket API | MIT |
| Uvicorn | 0.35.0 | 本机 ASGI 服务 | BSD-3-Clause |
| HTTPX | 0.28.1 | 公共行情与公开新闻 HTTP 客户端 | BSD-3-Clause |
| websockets | 15.0.1 | OKX 公共/免鉴权 WebSocket | BSD-3-Clause |
| Pydantic | 2.11.7 | 数据模型和输入验证 | MIT |
| NumPy | 2.3.2 | 指标与回测数值计算 | BSD-3-Clause |

## Python 测试依赖

| 组件 | 版本 | 用途 | 许可证 |
|---|---:|---|---|
| pytest | 8.4.1 | 后端自动测试 | MIT |
| pytest-asyncio | 1.1.0 | 异步后端测试 | Apache-2.0 |

## 前端运行时与构建依赖

| 组件 | 版本 | 用途 | 许可证 |
|---|---:|---|---|
| React | 19.2.7 | 用户界面 | MIT |
| React DOM | 19.2.7 | 浏览器渲染 | MIT |
| Lucide React | 1.24.0 | 界面图标 | ISC |
| TradingView Lightweight Charts | 5.2.0 | 本地图表引擎（蜡烛图、价格线、成交标记） | Apache-2.0 |
| fancy-canvas | 2.1.0 | Lightweight Charts 的 Canvas 渲染依赖 | MIT |
| Vite | 8.1.4 | 前端开发与生产构建 | MIT |
| @vitejs/plugin-react | 6.0.3 | Vite React 编译支持 | MIT |
| TypeScript | 7.0.2 | 前端类型检查与编译 | Apache-2.0 |
| @types/react | 19.2.17 | React TypeScript 类型 | MIT |
| @types/react-dom | 19.2.3 | React DOM TypeScript 类型 | MIT |

## 前端测试依赖

| 组件 | 版本 | 用途 | 许可证 |
|---|---:|---|---|
| Vitest | 4.1.10 | TypeScript 与 React 单元/组件测试 | MIT |
| React Testing Library | 16.3.2 | React 真实组件交互测试 | MIT |
| Testing Library DOM | 10.4.1 | DOM 查询与交互基础 | MIT |
| Testing Library user-event | 14.6.1 | 浏览器式输入和点击交互 | MIT |
| jsdom | 29.1.1 | 前端测试 DOM 环境 | MIT |

## TradingView Lightweight Charts NOTICE 与署名

本仓库保留上游 `v5.2.0` 的 NOTICE 与 Apache-2.0 许可证，精确副本分别位于 `third_party/lightweight-charts-NOTICE.txt` 和 `third_party/lightweight-charts-LICENSE.txt`：

```text
TradingView Lightweight Charts™
Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/
```

工作台显式启用 `layout.attributionLogo: true`，并在图表下方保留指向 TradingView 的用户可见链接。完整许可证为 [Apache License 2.0](https://github.com/tradingview/lightweight-charts/blob/v5.2.0/LICENSE)；上游项目、许可证与本地副本记录在 `docs/COMPONENT_AUDIT.md`。

## 外部服务和内容

- OKX 公共 REST、`/public` WebSocket 与免鉴权 `/business` K 线 WebSocket 仅用于读取公共市场数据。本项目不包含 OKX API Key、账户访问或交易权限。
- 新闻模块读取公开的 OKX 公告、CoinDesk、Cointelegraph Bitcoin 与 Decrypt 内容；原内容版权归各发布方所有，本项目只保存必要的元数据、链接和简短分析结果。

Lightweight Charts 由锁定的本地 npm 包随生产资源打包，不使用 iframe，不加载 TradingView 或其他第三方远程脚本，也没有引入自动交易 SDK。

## v0.6 外部设计参考（非依赖）

v0.6 市场分析和验证文档参考了下列开源项目对多周期研究、指标组织、数据质量和历史验证边界的公开设计。它们不是本项目依赖，本轮没有复制其源代码、资源、测试或许可证文本，也没有通过导入、链接、子模块、包管理器或远程脚本使用它们。

| 项目 | 上游与许可证 | 本项目使用方式 |
|---|---|---|
| Jesse | [上游仓库](https://github.com/jesse-ai/jesse)；[MIT License](https://github.com/jesse-ai/jesse/blob/master/LICENSE) | 仅参考自托管研究、多周期和 point-in-time 设计思想；未复制代码 |
| CryptoSignal | [上游仓库](https://github.com/CryptoSignal/Crypto-Signal)；[MIT License](https://github.com/CryptoSignal/Crypto-Signal/blob/master/LICENSE) | 仅参考指标组合与可解释输出的组织方式；未复制代码 |
| Freqtrade | [上游仓库](https://github.com/freqtrade/freqtrade)；[GPL-3.0 License](https://github.com/freqtrade/freqtrade/blob/develop/LICENSE) | 仅参考数据质量、回测与前视偏差防护的设计问题；未复制、链接或派生 GPL 代码 |
| VectorBT | [上游仓库](https://github.com/polakowo/vectorbt)；[Apache-2.0 with Commons Clause](https://github.com/polakowo/vectorbt/blob/master/LICENSE.md) | 仅参考向量化研究和统计报告的设计问题；未复制代码、未依赖该包 |

这些设计参考不改变本项目现有直接依赖和许可证清单。v0.6 实现继续复用仓库自身的 NumPy 指标、FastAPI/SQLite 服务和 Lightweight Charts；本轮无新增第三方依赖。
