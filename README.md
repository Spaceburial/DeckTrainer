# DeckTrainer

运行在 **SteamOS / Steam Deck** 上的通用游戏内存修改器：**自带运行时的独立可执行文件**，零依赖、零下载，用手机浏览器边玩边改数值。

支持 **任意游戏**：Steam / Proton / Wine 游戏、非 Steam 游戏、原生 Linux 游戏、模拟器。

> 仅用于单机游戏；请勿用于联机对战或绕过反作弊。

## 特性

- **免 Python**：`DeckTrainer` 是打包好的独立可执行文件，解压即用。
- **自动识别**：游戏启动后自动附加，识别游戏名、引擎（Unity/Unreal/REDengine/Godot/Source）、32/64 位、Steam AppID，并给出属性预设。
- **启动保护**：配 Steam 启动选项时，游戏先启动、修改器后初始化；端口冲突自动换端口，修改器故障也不会挡住游戏。
- **免启动选项的自动模式**：`后台模式.sh` 先跑修改器，再正常开游戏，自动发现并附加（需一次性 ptrace 授权，`开放权限.sh`）。
- **手机遥控**：自带局域网网页界面，游戏模式下用手机浏览器操作。
- **全类型搜索**：一次同时尝试 4/8 字节整数、单/双精度小数。
- **模糊快照**：血条没有数字时，用「快照 → 减少/增加」定位血量、氧气、燃料。
- **锁定**：每 50ms 自动回写，等于无限血量 / 金钱。
- **收藏自动重定位**：保存「模块 + 偏移」，下次开游戏附加时自动复原；堆地址按旧值校验。
- **诊断工具**：`诊断.sh` 一键收集系统、进程、端口、日志、启动选项。

## 原理

直接读写 `/proc/<pid>/mem`，不暂停目标进程（不会像 Cheat Engine / ptrace 那样冻结游戏），扫描可写匿名内存与游戏模块私有数据段，多轮筛选收敛到真实地址后改值或周期回写。

## 安装

1. 从 [Releases](https://github.com/Spaceburial/DeckTrainer/releases/latest) 下载 `DeckTrainer-v2.2.zip`，解压得到 `DeckTrainer/`，放到 `/home/deck/DeckTrainer/`。
2. 赋予执行权限：

   ```bash
   chmod +x ~/DeckTrainer/DeckTrainer ~/DeckTrainer/*.sh
   ```

3. （可选）右键 `DeckTrainer.desktop` → 属性 → 允许作为程序执行。

## 使用

### 方式 A：Steam 启动选项（推荐，免密码）

Steam → 游戏属性 → 通用 → 启动选项：

```
bash /home/deck/DeckTrainer/start.sh %command%
```

游戏一定会先启动；修改器随后启动并自动附加。手机打开 `http://<Deck IP>:8766`。

### 方式 B：后台自动识别（不加启动选项）

```bash
bash ~/DeckTrainer/后台模式.sh
```

然后正常启动任意游戏，自动识别并附加。若提示权限不足，运行一次 `bash ~/DeckTrainer/开放权限.sh`（需密码；deck 默认无密码，先 `passwd`）。

### 方式 C：桌面模式

双击 `DeckTrainer.desktop`（或 `bash ~/DeckTrainer/start.sh`），浏览器打开 `http://127.0.0.1:8766`。

### 基本流程

1. 点属性预设（或选类型），输入游戏里的数值 → 全类型搜索
2. 数值变化后 → 精确值 / 增加 / 减少 反复筛选
3. 剩 1~3 个候选 → 写入或锁定
4. 成功后点 ★ 收藏，下次自动恢复

详细说明见 [DeckTrainer/使用说明.md](DeckTrainer/使用说明.md)。

## 文件结构

```
DeckTrainer/
├── DeckTrainer           # 独立可执行文件（免 Python）
├── trainer.py            # 源码
├── start.sh              # 启动脚本（游戏模式 / 桌面模式）
├── 后台模式.sh            # 后台自动识别（不需要启动选项）
├── 开放权限.sh            # 一次性 ptrace 授权（后台模式用）
├── DeckTrainer.desktop   # 桌面快捷方式
├── 诊断.sh               # 一键诊断报告
└── 使用说明.md
```

## 关于 Windows .exe

SteamOS 上的 Windows 程序经 Proton 跑在独立容器里，无法读取另一款游戏的内存，所以本工具提供的是 Linux 原生可执行文件；它可以直接在 Steam「添加非 Steam 游戏」里选择，像 .exe 一样从游戏模式启动。

## 已知限制

- 地址每次启动都会变，首次仍需现场搜索 2~3 轮。
- 模糊快照有容量上限（默认约 32MB 可写匿名内存）。
- 数值加密存储的游戏无效。
- 无指针链扫描；纯堆地址的收藏重启后可能失效，需重搜。

## License

MIT
