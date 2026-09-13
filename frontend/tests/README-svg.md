# SVG 产物回归

- `npm run test:svg`：分类、画布产物节点、点击选择契约、URL 编码和实际图片组件渲染；也随 `npm test` 执行。
- `python -m pytest backend/tests/test_artifacts.py backend/tests/test_svg_security.py backend/tests/test_launch_prompt.py backend/tests/test_runner_preview_repair.py`：从仓库根目录验证发布限制、四类提示、原始响应安全策略，以及使用测试 provider 驱动的真实 NodeRunner → reap → 持久副本 → API 链路。
- `BROWSER_BIN="/path/to/chromium" npm run test:svg:browser`：从 `frontend/` 启动独立无头 Chrome、Chromium 或 Edge。需要 Node.js 22+、可导入后端的 Python；可用 `PYTHON` 指定解释器。无需安装额外浏览器依赖。

浏览器测试使用真实后端 API 的响应内容和安全头，在随机本地端口重放；挂载生产 `SvgArtifactPreview` 组件，检查脚本、事件处理器、外部图片/样式/字体、XML 实体、foreignObject、导航、弹窗、data URL 和隐式下载。像素断言同时验证正常图形、内联样式及 data URI 图片仍可显示。测试使用独立浏览器配置目录，仅关闭自己启动的进程。

## 安全边界

SVG 以 `<img>` 图片上下文显示，不是可交互文档；不要换回 iframe、object、embed 或直接内联 SVG。脚本、外部资源和链接交互不可用，交互式产物应选择 HTML 模式。

SVG raw 响应保留原始文件，设置禁止脚本与外部资源的 CSP、`nosniff` 和附件下载头。附件头不会妨碍 `<img>` 展示，但直接导航到 raw 地址只会下载，不会把未可信 SVG 当作同源文档运行。HTML 模式保留自身既有策略，不受这次变更影响。

这不是 SVG 内容清洗器。下载后在其他应用或本地浏览器中打开文件，不再受 MiniClaw2 响应头保护；不要把未知来源的下载文件当作可信文档执行。
