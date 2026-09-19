# 测试

无第三方依赖，`node` 和 `python` 直接跑。不需要 osu! 在运行。

```bash
python tests/test_reader.py        # 读取器：内存被释放的各种情况、命名规则
python tests/test_frozen_paths.py  # 打包版：数据写到哪、能不能写进去
node  tests/verify_ui.js           # 前端：id 覆盖、四条渲染路径、产出值
node  tests/test_flicker.js        # 空数据块不能把面板清空（切屏时的闪烁）
node  tests/test_pages.js          # 页面切换：谁在显示、谁还在退场、被打断怎么办
node  tests/check_ids.js           # app.js 里像 id 的字符串必须真的存在
```

## 各文件在防什么

| 文件 | 防的问题 |
|---|---|
| `test_reader.py` | 对象被 osu! 释放后指针变野。包括空指针、被回收的指针、半可读的记录、以及用 MD5 校验挡"可读但无意义"的垃圾。全部用假进程驱动，不需要游戏。 |
| `test_frozen_paths.py` | 打包版把文件写到哪。起一个**假装自己是冻结版**的子进程（`sys.frozen` / `sys._MEIPASS` / `sys.executable`），`LOCALAPPDATA` 指向临时目录，然后真的驱动一次成绩捕获。防的是这两个都犯过的错：**把文件写在 exe 旁边**，以及**写到临时 bundle 里等于没写**。真实缓存不会被碰。 |
| `verify_ui.js` | 每个 `els[...]` 引用的 id 都必须在 `index.html` 里存在——**这个 bug 让三个页面整块空白过两次**。另外验证四条渲染路径都能跑完，以及它们产出的值。 |
| `test_flicker.js` | osu! 切屏那一帧会把对象释放掉，读回来是空的。验证空块**不会**把面板清空、不会把页面踢回主页。 |
| `test_pages.js` | 切屏。**结构检查**：每屏里每一个带文字的元素都必须落在某个被动画的祖先之下——四屏是叠在一起的固定图层，漏掉一个就会被两屏同时画出来（"文字重叠"）。另外校验 JS 里的 `PAGE_LEAVE_MS`、CSS 里的 `--pg-out`、`--pg-in-delay` 三者的先后关系（分居两个文件，只有测试绑得住），以及退场/入场的类名账目：连切两屏时中间那屏不能留在屏幕上，退回一个还在退场的页面要取消它自己的退场。 |
| `check_ids.js` | 单独跑很快，专门查 id 漂移。 |

## 环境假设

- 脚本自己推算仓库路径（`tests/../osu_live/web/`），换目录 checkout 也能跑
- 没有 `requestAnimationFrame` 时动画补间会**同步落到目标值**，所以断言读到的是最终值而不是中间帧
- `verify_ui.js` / `test_flicker.js` 用 DOM 桩驱动 `app.js`，不是真浏览器。真浏览器里的排版、动画、滚动要另外验
- `test_pages.js` 用 `vm` 起两个独立上下文，分别在"有 rAF"和"没有 rAF"下跑一遍 `app.js`。它只看类名和计时器，看不到动画本身

## 真浏览器验证

排版和动画没法靠桩验证。`osu_live/web/diag/` 下有两个常驻页面，浏览器打开就能看：

- **`preview.html`** —— 四个界面并排放在 812×375 的 iframe 里（iPhone 13 mini 横屏），
  用来对排版。取哪一块的截图都行。
- **`anim-probe.html`** —— 在 iframe 里真的调 `window.osuLive.render()` 切屏，然后
  **采样浏览器算出来的 opacity 和 transform**。截图看不出一个值是滚过去的还是跳过去的，
  这个能。它会走遍四屏的每一次切换，对每一屏做一次"带文字的子树 × 累计不透明度"遍历，
  报出**有没有哪一瞬间两屏同时可见**，并列出具体是哪些元素、不透明度多少。

  > 写这个探针本身踩了三次坑，都是同一个原因：**隐藏元素的后代仍然会报出自己的
  > computed style**。`display: none` 的屏幕、被横屏隐藏的判定栏、以及隐藏盒子的子元素，
  > 它们的 `opacity` 照样读得到 1.00 或自己的 `display` 值。所以遍历时必须**逐层**
  > 检查 `display`，包括根节点。三次都先把结论指向了假的重叠。

两个都只用于**看排版和动画**。诊断"横屏首屏崩溃"那类问题不能这样测——iframe 一加载就完成
首次布局，必须用 `diag/` 里那些独立的变体页。

`window.osuLive` 是特意暴露的，就是给这种验证用的。

CSS 动画在 iframe 里确实会跑（本次实测：前进从 +12.1px 起、后退从 −13.0px 起）。
**没有测的是** rAF 回调在无头 iframe 里是否触发——所以 JS 补间那部分仍然只按兜底路径验证过。
真机手感、刘海、OLED 对比度只能真机看。
