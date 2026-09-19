# osu! live

把本机正在运行的 osu! stable 客户端变成一块可以放在旁边的屏幕：程序直接读取
游戏进程内存，实时算出 pp，然后通过 HTTP + SSE 推给手机、平板或第二台电脑。

数据全部来自**本机**：
游戏内存 → 本机程序 → 你的设备。只有可选的总 pp / 排名会去查一次
bancho.py 服务器（下称"服务器数据"），关掉也能完整运行。

参考了 [tosu](https://github.com/tosuapp/tosu) 的签名与结构偏移，
pp 使用 bancho.py 自己在用的 `akatsuki-pp-py`，因此数值和服务器一致。

## 功能

- **实时 pp**：当前 pp、FC(全连) pp、满分 pp，以及 100/99/98/97/95/90% 准确率的 pp 表。
  选歌界面、结算界面和主菜单都会算（只要选中了图），游戏中按 `PP_INTERVAL_MS` 刷新；
  切图时会先清空再重算，不会把上一张图的 pp 显示在新曲名下面。
- **游戏状态**：分数、连击、准确率、字母等级、HP、300/100/50/miss/geki/katu
- **谱面信息**：曲名、难度、作者、AR/CS/HP/OD、物件数、谱面 ID
- **Mod、按键次数、按键状态**（按下时高亮）
- **打击误差图**：最近 64 个判定偏移，一眼看出是早还是晚
- **本地排行榜**：当前谱面的榜上成绩
- **服务器数据**（可选）：总 pp、全球排名、准确率、游玩次数等

## 手机界面

为**横屏**设计（基准是 iPhone 13 mini，812×375）。四个界面按游戏状态自动切换，
一屏显示、不滚动：

| 界面 | 出现时机 | 主次取舍 |
| --- | --- | --- |
| 主页 | 主菜单 / 空闲 | 账号是主角：总 pp 最大号，下面一条四格（准确率 / 总分 / 游玩 / 排名）；当前选中的图退为次要 |
| 选歌 | 选歌 / 编辑 | 曲名难度 + 星数环 + AR/OD/CS/HP 一行；中间理论 pp，底部 6 档准确率做成梯级（按 pp 占比填充高度）；物件数 / mapper / mod 为附属 |
| 游戏中 | 正在游玩 | 分数为锚点，纵向 HP 条贴右侧；连击 / 准确率 / 实时 pp 三格以颜色区分；判定数压成一条细栏 |
| 结算 | 结算画面 / 排名 | 等级做成色块徽章与得分并排；表现 / 准确率 / 连击三格；判定数一栏；最大连击与时间收进脚注 |

退出结算画面回到选歌时会自动切回选歌页。

排版刻意避免上下堆叠：每屏由一组横向结构组成（状态轨 / 头部 / 圆环 / 读数栏 /
梯级 / 徽章），靠形状而不是阅读顺序找到重点。字号与间距都由 `vh` 推导，屏幕变矮
时整页等比收缩，而不是把内容挤出屏幕；横屏另有断点，把纵向块并成两栏。

### 切屏

四个界面是自动切的，所以切换本身也做了处理。**一次只让一屏可见**：

- 旧内容先淡出并向上走（90ms）
- 等它退完，新内容才分块淡入（200ms，每块错开 30ms）
- 从主页往结算走是"向上浮起"，退回则是"向下落下"，方向由两个界面在游戏流程里的先后决定
- 连切两屏时中间那屏会被提前放掉，不会留在屏幕上；退回一个还在退场的界面会取消它自己的退场

**为什么不做交叉淡入。** 每屏都是叠在前一屏上的固定图层，自己不带背景，所以
**任何没被动画覆盖的元素都会被两屏同时画出来**——那就是"文字重叠"。而四屏的内容
几乎都不一样：选歌页曲名只显示曲名、其余页显示"作者 - 曲名"，字号也不同；结算页
底部没有进度条，判定栏在竖屏还占一整行。所以这里不是"调不透明度"，而是**要么这屏
画、要么那屏画**。

唯一刻意不动的是右上角状态轨：四屏的时钟和"已连接"是同一个字符串，位置也一样，
叠着看不出来。为此 `tests/test_pages.js` 会做结构检查：**每屏里每一个带文字的元素，
都必须落在某个被动画的祖先之下**，否则直接报错——这个 bug 在屏幕上只表现为"切的那
一下有点怪"，靠看是抓不住的。

系统开了"减弱动态效果"时不播动画，直接切换。

刘海、挖孔与 Home 指示条通过 `env(safe-area-inset-*)` 让位（`viewport-fit=cover`
已在 meta 中），状态轨不会钻到刘海底下。

### 调试某一张界面

不必真的把游戏切到对应状态，直接打开 `?demo=home|select|play|results` 就会用固定
样例数据渲染该界面，方便在真机上对着调排版：

```
http://<手机能访问的地址>:24051/?demo=play
```

## 快速开始

```bash
pip install -r requirements.txt
python -m osu_live
```

启动后终端会打印可以访问的地址：

```
osu! live is running
  local   http://127.0.0.1:24051
  phone   http://192.168.1.23:24051
```

用手机浏览器打开那个 `phone` 地址即可。手机和电脑需要在同一局域网，
并且允许 Python 通过 Windows 防火墙（第一次运行时会弹窗）。

## 配置

复制 `.env.example` 为 `.env`，或直接用命令行参数。

| 环境变量 | 命令行 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `HOST` | `--host` | `0.0.0.0` | 监听地址；`0.0.0.0` 才能被手机访问 |
| `PORT` | `--port` | `24051` | 监听端口 |
| `SONGS_FOLDER` | `--songs` | 自动探测 | osu! 的 `Songs` 目录，算 pp 用 |
| `POLL_INTERVAL_MS` | — | `50` | 读取内存的间隔 |
| `PP_INTERVAL_MS` | — | `1000` | 游戏中重算 pp 的间隔 |
| `BANCHO_API_URL` | `--bancho` | 空 | bancho.py 的 API 地址，如 `https://api.example.com` |
| `BANCHO_PLAYER` | `--player` | 空 | 该服务器上的玩家 ID 或用户名 |
| `AVATAR_URL` | — | 空 | 服务器头像地址，如 `https://a.example.com` |
| `BANCHO_INTERVAL_S` | — | `15` | 刷新服务器数据的间隔 |
| `ACCESS_TOKEN` | `--token` | 空 | 设置后所有请求都要带 token |

`SONGS_FOLDER` 留空时会尝试在 `C:/D:/E:` 下常见位置找 `osu!/Songs`。
pp 依赖谱面文件，只在游戏内选到图时才有意义。

### bancho.py 服务器数据

私服玩家想看总 pp 和排名时填这两项（示例对应 bancho.py 的默认子域名布局）：

```
BANCHO_API_URL=https://api.your-server.com
BANCHO_PLAYER=你的用户名
AVATAR_URL=https://a.your-server.com
```

程序会调用公开的 v1 接口 `get_player_info`、`get_player_status` 和
`get_leaderboard`，不需要 API key。

### 访问令牌

如果这台电脑在不受信任的网络里，可以设置 `ACCESS_TOKEN=一串随机字符`。
之后打开 `http://<ip>:24051/?token=那串字符`，页面里也提供了输入框。

## 接口

给想自己写界面的人：

| 路径 | 说明 |
| --- | --- |
| `GET /api/state` | 当前状态的 JSON 快照 |
| `GET /api/events` | SSE 推送，每条 `data:` 是一份和上面同结构的 JSON |
| `GET /api/config` | 生效中的配置 |
| `GET /api/avatar?id=<玩家ID>` | 转发服务器头像（手机只需访问本机） |

`/api/state` 的字段：`connected`、`state`、`play_time`、`audio_length`、
`progress`、`player`、`beatmap`、`mods`、`gameplay`、`key_overlay`、
`hit_errors`、`leaders`、`pp`、`score_pp`、`bancho`。

`pp` 是**当前选中谱面**的实时值（游戏中会随连击和判定刷新）；
`score_pp` 是**已结束的那一局**的值，从缓存的判定数重算 —— 因为 osu! 在
歌曲结束的瞬间就清掉了 gameplay 结构，此时 `pp` 会退化成理论满分值。

## 字母等级怎么定

等级由判定数算出（不是游戏给的），规则与 osu! stable 一致：

- **osu! / taiko**：看 300 占比。`300 占比 = 1` → `X`；`> 0.9` 且 50 占比 `< 0.01`
  且无 miss → `S`；`(> 0.8 且无 miss) 或 > 0.9` → `A`；`(> 0.7 且无 miss) 或 > 0.8` → `B`；
  `> 0.6` → `C`；否则 `D`。
- **catch / mania**：只看准确率，档位分别为 98/94/90/85 与 95/90/80/70。

带 HD 或 FL 时 `S`/`X` 会显示为银色的 `SH`/`XH`，与游戏一致。
从 `.last_score.json` 读回成绩时会**重新计算**等级，所以规则改动后旧缓存不会
继续显示错误等级。

## pp 从哪来

pp 由 **`akatsuki-pp-py`** 计算 —— 这正是 bancho.py 自己在
`app/services/performance.py` 里用的那个库（`pyproject.toml` 里锁的是 `akatsuki-pp-py==1.0.5`，
本项目也用同一版本）。它本身是
[`rosu-pp`](https://github.com/MaxOhn/rosu-pp) 的 Python 绑定（作者 Max Ohn / tsunyoku），
而 rosu-pp 是 osu!lazer 难度与 pp 算法的独立 Rust 实现。

调用方式也和服务器保持一致：只在 `NC` 时补 `DT`（rosupp 不认 NC）、
`acc` 与 `n300/n100/n50` 二选一、结果取三位小数。

**实测一致性**：拿服务器上已存的三张成绩（含 mod、连击、判定数）本地重算，
pp 与服务器**完全相同**：

| 谱面 | 服务器 | 本地重算 | 差 |
| --- | --- | --- | --- |
| Everything will freeze [Extra] | 309.952 | 309.952 | 0.000 |
| Kan Saete Kuyashiiwa [geragera] | 304.329 | 304.329 | 0.000 |
| oddloop [queerish dances] | 265.373 | 265.373 | 0.000 |

（星数会有极小差异，如 6.397 vs 6.374：那是 bancho.py 用 `akatsuki-pp-py`
自己的难度计算、而游戏的星数显示按总长度加权。pp 才是结算依据，两者一致。）

## 客户端兼容性

针对 **osu! stable `b20240820.1`**（Stable40；`Logs/session.log` 里的
`Version 20240820.1`）校准，签名取自 tosu。

注意 `osu!.exe` 的文件版本号和安装目录名都可能与真实版本不符 —— 本机 exe 报
`1.3.3.8`、目录叫 `b20220829`，实际跑的是 2024-08-20 的构建。判断版本请看
`Logs/session.log` 的 `Version` 字段。

osu! 不同版本的结构体会移动，所以程序对几个已知会变的字段做了运行时判别
（见 `osu_live/reader.py` 里 `_gameplay_base` / `_key_overlay_base` /
`_leaderboard_base` / `_profile_layout`）：先读候选偏移，再用结构是否自洽
（分数对象里能否读出玩家名、数组长度是否合理）来确认，选错时自动换另一个。

如果换到别的版本发现某项读不出来，对应地调整这些函数和
`signatures.py` 里的签名即可；单个字段读失败只会让那一项留空，不会影响其它数据。

## 注意

- 只读进程内存（`PROCESS_VM_READ`），不写入、不注入、不改游戏。
- 只支持 osu! **stable**；lazer 的内存结构完全不同。
- 需要游戏和本程序以相同权限运行（一般都以普通用户跑就行）。

## 常见问题

**手机打不开页面**
确认手机和电脑在同一网络，并在 Windows 防火墙里放行 Python。
终端打印的 `phone` 地址就是可以用的那个；如果列了多个网卡地址，优先试第一个。
程序已过滤掉 `169.254.x`（链路本地）和 `198.18/198.19.x`（Clash 之类代理的 TUN
网段），这些手机路由不到。

**pp 一直显示 —**
检查 `SONGS_FOLDER` 是否指向真正的 `Songs` 目录。缓存命中时才不重复解析谱面，
但谱面文件必须在本地存在；程序不会去网上下图。

**页面显示"等待 osu! …"**
程序没找到 `osu!.exe` 进程。游戏退出后就是这个状态，重新开游戏会自动接上，
不需要重启本程序。

**某项数据一直是空的**
多半是该版本 osu! 改了结构。参考上面「客户端兼容性」调整偏移即可，
其它数据不受影响。

## 目录

```
osu_live/
  winmem.py       进程内存读取、AOB 特征扫描
  signatures.py   osu! stable 的签名与状态枚举
  reader.py       偏移定义、指针链解析、状态快照
  pp_engine.py    pp 计算（封装 akatsuki-pp-py）
  bancho_api.py   bancho.py v1 API 客户端（可选）
  state.py        采集线程：合并内存 / pp / 服务器数据
  server.py       HTTP + SSE 服务
  web/            手机端界面（原生 HTML/CSS/JS）
    diag/         诊断与预览页，不是应用的一部分（见 diag/README.md）
tests/            回归测试，无第三方依赖（见 tests/README.md）
```
