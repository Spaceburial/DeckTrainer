# DeckTrainer

一个运行在 **SteamOS / Steam Deck** 上的通用游戏内存修改器：**自带运行时的独立可执行文件**，零依赖、零下载，用手机浏览器就能边玩边改数值。

支持 **任意游戏**：Steam / Proton / Wine 游戏、非 Steam 游戏、原生 Linux 游戏、模拟器——附加任意正在运行的进程即可。

> 仅用于单机游戏；请勿用于联机对战或绕过反作弊。

## 特性

- **免 Python**：`DeckTrainer` 是打包好的独立可执行文件，解压即可运行。
- **手机遥控**：自带局域网网页界面，游戏模式下用手机浏览器操作，不用切桌面。
- **自动挂接 + 自动附加**：配好 Steam 启动选项后，修改器随游戏启动，并自动识别附加游戏进程，无需 sudo、无需手动选进程。
- **全类型搜索**：一次同时尝试 4/8 字节整数、单/双精度小数，省去猜数值类型。
- **模糊快照**：血条没有数字显示时，用「快照 → 减少/增加 → 筛选」定位血量、氧气、燃料。
- **锁定**：每 50ms 自动回写，等于无限血量 / 无限金钱。
- **收藏自动重定位**：保存常用数值的「模块 + 偏移」，下次开游戏附加时自动复原地址；堆地址按旧值校验。
- **内置游戏配方**：影之刃零、古墓丽影：崛起、星际拓荒、赛博朋克 2077 的数值类型与搜法提示。
- **诊断工具**：`诊断.sh` 一键收集系统、进程、端口、日志、启动选项，方便排障。

## 原理

直接读写 `/proc/<pid>/mem`，不暂停目标进程（不会像 Cheat Engine / ptrace 那样冻结游戏），扫描可写匿名内存与游戏模块的私有数据段，多次筛选收敛到真实地址后改值或周期性回写。

## 安装

1. 从 [Releases](https://github.com/Spaceburial/DeckTrainer/releases/latest) 下载 `DeckTrainer-v2.1.zip`，解压得到 `DeckTrainer/` 文件夹，放到 `/home/deck/DeckTrainer/`。
2. 赋予执行权限：

   ```bash
   chmod +x ~/DeckTrainer/DeckTrainer ~/DeckTrainer/start.sh ~/DeckTrainer/诊断.sh
   ```

3. 右键 `DeckTrainer.desktop` → 属性 → 允许作为程序执行。

## 使用

### 游戏模式（推荐，免密码）

Steam → 右键游戏 → 属性 → 通用 → 启动选项：

```
bash "/home/deck/DeckTrainer/start.sh" %command%
```

启动游戏后修改器会自动附加，手机与 Deck 连同一 Wi-Fi，浏览器打开 `http://<Deck 的 IP>:8766`。

### 桌面模式

双击 `DeckTrainer.desktop`（或 `bash ~/DeckTrainer/start.sh`），浏览器打开 `http://127.0.0.1:8766`。
SteamOS 默认 deck 用户没有密码，桌面模式附加需要先 `passwd` 设置密码（或用上面的游戏模式方式）。

### 基本流程

1. 输入游戏内看到的数值 → 「全类型搜索」
2. 回游戏让数值变化 → 「精确值 / 增加 / 减少」反复筛选
3. 候选剩 1~3 个 → 写入或「锁定」
4. 定位成功后点 ★ 收藏，下次开游戏自动恢复

详细说明见 [DeckTrainer/使用说明.md](DeckTrainer/使用说明.md)。

## 文件结构

```
DeckTrainer/
├── DeckTrainer           # 独立可执行文件（免 Python）
├── trainer.py            # 源码（可读/可改，需 Python 3）
├── start.sh              # 启动脚本（游戏模式挂接 / 桌面模式启动）
├── DeckTrainer.desktop   # 桌面快捷方式
├── 诊断.sh               # 一键诊断报告
└── 使用说明.md
```

## 已知限制

- 地址每次启动游戏都会变，首次需要现场搜索 2~3 轮。
- 模糊快照有容量上限（默认约 32MB 可写匿名内存）。
- 对数值加密存储的游戏无效。
- 无指针链扫描；纯堆地址的收藏在重启后可能失效，需重搜一次。

## License

MIT
