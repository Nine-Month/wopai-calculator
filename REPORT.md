# 太平洋前线·布阵助手 — 交付报告

## 如何使用

1. 运行 `dist\PacificFrontHelper\PacificFrontHelper.exe`（或源码 `python main.py`）。
2. 点「复制注入脚本」，在游戏页 DevTools Console 粘贴；或点「连接/采数」复制书签。
3. 连接后自动采数；底部「无道具 / 可购买道具 / 不可购买道具」计算。
4. **点击**结果卡片才会放牌；不会点击战斗。

## 测试结果（Linux 箱）

| 项 | 结果 |
|----|------|
| 模块 import | OK |
| settings 读写/clamp | OK |
| kp7 无道具 wrapper | ≥1 解（实测 3–5） |
| kp8 无道具 | 0 解（与原求解器一致，属正常） |
| kp8 不可购买道具 | 5 条预存；顶解 约翰斯顿+心无旁骛 / 阿拉斯加 / 新奥尔良+特化 / 拉菲+老水手 |
| evaluate_with_mods | OK |
| 桥接 HTTP/WS/inject.js | OK（端口占用释放后再绑 OK） |
| MainWindow 构造 + 卡片渲染 | OK（offscreen） |
| Windows EXE | 见本机 `build\build_windows.bat` 产物 |

## 已知限制

- 放牌靠 DOM/store 启发式，游戏改版可能失效；失败时仍可手动摆。
- 可购买道具穷举较慢（数十秒级），请看进度条文字。
- 无道具模式只保留全确解。
- Linux 箱无法直接产出 Windows EXE，需在 Windows 跑打包脚本。

## 目录要点

- `core/` — solver / items_solve / scrape（规则与原仓库一致）
- `bridge/inject.js` — 页面注入
- `app/` — UI + 桥接服务 + worker
- `build/build_windows.bat` — 一键打包 onedir
