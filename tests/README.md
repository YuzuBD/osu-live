# 测试

无第三方依赖，`node` 和 `python` 直接跑。不需要 osu! 在运行。

```bash
python tests/test_reader.py     # 读取器：内存被释放的各种情况、命名规则
node  tests/verify_ui.js        # 前端：id 覆盖、四条渲染路径、产出值
node  tests/test_flicker.js     # 空数据块不能把面板清空（切屏时的闪烁）
node  tests/check_ids.js        # app.js 里像 id 的字符串必须真的存在
```

## 各文件在防什么

| 文件 | 防的问题 |
|---|---|
| `test_reader.py` | 对象被 osu! 释放后指针变野。包括空指针、被回收的指针、半可读的记录、以及用 MD5 校验挡"可读但无意义"的垃圾。全部用假进程驱动，不需要游戏。 |
| `verify_ui.js` | 每个 `els[...]` 引用的 id 都必须在 `index.html` 里存在——**这个 bug 让三个页面整块空白过两次**。另外验证四条渲染路径都能跑完，以及它们产出的值。 |
| `test_flicker.js` | osu! 切屏那一帧会把对象释放掉，读回来是空的。验证空块**不会**把面板清空、不会把页面踢回主页。 |
| `check_ids.js` | 单独跑很快，专门查 id 漂移。 |

## 环境假设

- 脚本自己推算仓库路径（`tests/../osu_live/web/`），换目录 checkout 也能跑
- 没有 `requestAnimationFrame` 时动画补间会**同步落到目标值**，所以断言读到的是最终值而不是中间帧
- `verify_ui.js` / `test_flicker.js` 用 DOM 桩驱动 `app.js`，不是真浏览器。真浏览器里的排版、动画、滚动要另外验

## 真浏览器验证

排版和动画没法靠桩验证。之前用的办法：

- **截图**：headless chromium 加 `--window-size=812,375`（iPhone 13 mini 横屏）
- **几何探针**：同源 iframe 里量每个区块的盒子，查重叠和越界。探针是临时文件，用完删了——需要时重建
- **动画采样**：`window.osuLive.render()` 暴露出来专门给这个用（静态截图看不出值是滚动过去的还是跳过去的）

注意 headless 环境里 `requestAnimationFrame` **不会触发**，所以动画测到的是兜底路径。真机手感只能真机看。
