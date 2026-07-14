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
| Vite | 8.1.4 | 前端开发与生产构建 | MIT |
| @vitejs/plugin-react | 6.0.3 | Vite React 编译支持 | MIT |
| TypeScript | 7.0.2 | 前端类型检查与编译 | Apache-2.0 |
| @types/react | 19.2.17 | React TypeScript 类型 | MIT |
| @types/react-dom | 19.2.3 | React DOM TypeScript 类型 | MIT |

## 外部服务和内容

- OKX 公共 REST、`/public` WebSocket 与免鉴权 `/business` K 线 WebSocket 仅用于读取公共市场数据。本项目不包含 OKX API Key、账户访问或交易权限。
- 新闻模块读取公开的 OKX 公告、CoinDesk、Cointelegraph Bitcoin 与 Decrypt 内容；原内容版权归各发布方所有，本项目只保存必要的元数据、链接和简短分析结果。

本版本没有引入 TradingView Lightweight Charts，也没有加载第三方远程脚本、字体或自动交易 SDK。
