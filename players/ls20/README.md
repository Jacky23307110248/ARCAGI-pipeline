# pseudo-LS20 人工游玩播放器

这是独立的本地窗口播放器：读取仓库里的三套 pseudo-LS20 公共关卡，每套七关，使用随附的原 LS20 游戏源码和 ARCEngine 执行移动、碰撞和通关。**不需要安装 ARCAGI Pipeline、不需要 API 密钥、Tycho/Duck 仓库，也不使用新 main 工作机。**

## 下载与安装

下载整个仓库（GitHub 页面绿色 **Code → Download ZIP**，解压），或者 git clone。只下载一个 JSON 无法游玩。保留 `players/ls20/` 与 `outputs/ls20/seed22/` 的相对位置。

需要 **Python 3.13**（已验证 3.13.5）及 Tk 图形界面支持。Windows 推荐 python.org 的标准安装版，安装时保留 Tcl/Tk；Linux 通常需要系统包 `python3-tk`，并使用有桌面的会话。远程无显示环境不能直接弹出窗口。

在**仓库根目录**打开终端，Windows PowerShell 执行：

```powershell
py -3.13 -m venv .venv-ls20
.\.venv-ls20\Scripts\python.exe -m pip install -r players/ls20/requirements.txt
.\.venv-ls20\Scripts\python.exe players/ls20/play.py
```

macOS / Linux（确认 `python3.13 -m tkinter` 能打开窗口）：

```sh
python3.13 -m venv .venv-ls20
.venv-ls20/bin/python -m pip install -r players/ls20/requirements.txt
.venv-ls20/bin/python players/ls20/play.py
```

下载依赖时需要联网，安装后游玩完全离线。无需运行 `pip install -e .`。依赖列表只供这个播放器使用，不要覆盖其他实验环境。

## 操作与游玩规则

| 操作 | 功能 |
|---|---|
| 方向键或 W/A/S/D | 上、左、下、右；每次按键交给原引擎执行一次动作 |
| R /「重置当前关」 | 非最终 WIN 时按原引擎重置当前关，保持已过关进度；最终 WIN 后重置回第一关 |
|「整套重新开始」| 新建当前单元的游戏，从第一关开始，清零界面输入计数 |
| 1 / 2 / 3 或顶部下拉框 | 切换游戏单元，并从该单元第一关开始；不保存原单元进度 |
| Esc / 关闭窗口 | 退出；下次启动从第一关开始 |

- 默认单元 1。关卡目标由实际游戏判断；满足后自动进入下一关，七关全部完成才显示 **整套通关 WIN**。没有人为跳关或自动解题按钮。
- **GAME OVER** 后不能继续移动，按 R 重试或整套重新开始。
- 当前关内的步数、状态等以游戏画面为准；下方“已输入”只是播放器累计收到的动作次数，**不是剩余步数或评分**。撞墙等操作是否消耗资源由引擎决定。
- 游戏不是按时间自动推进，观察画面不消耗动作。长按键可能触发系统连发，建议逐次按键。若按键没响应，先点击游戏画面。
- 这里不提前解释关卡机关，保留自行观察与推断的体验。播放器不读取解答：只加载 `unit1_public.json`～`unit3_public.json`。想盲玩时请勿查看 `unit*_supervisor.json`、`report.json` 或运行解答回放测试。
- 这三套是固定拓扑样例，不代表任意 seed 都会生成不同地图；游戏数据中有可见/迷雾设置，播放器不额外揭示隐藏状态。

## 启动参数

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/play.py --unit 2 --scale 10
```

`--unit` 选择 1～3；`--scale` 设置像素放大倍数（3～16，默认 8）。数据放在其他目录时使用 `--packages "关卡文件夹路径"`，该目录须包含三份 `unit*_public.json`。默认路径根据播放器文件定位，与当前终端目录无关。

## 排查与检查

- 找不到 `numpy` / `pydantic`：确认使用上面同一个 `.venv-ls20` 解释器安装及启动。
- 找不到 `_tkinter`：补装带 Tcl/Tk 的 Python 或对应系统 Tk 包；它不是 `pip install tkinter`。
- 找不到关卡：下载整个仓库，检查 `outputs/ls20/seed22/packages/`，或显式指定 `--packages`。
- 源码指纹不一致：不要修改 `game/ls20.py`；重新取得同一份播放器组件。

不读答案的窗口烟测：

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/play.py --smoke-test
```

它检查三套加载、绘制、移动和重置后退出，**不代表通关**。可加 `--screenshot preview.png` 导出初始画面。开发验收另有 `test_player.py`：会读取公开报告里的答案，检查三套真实引擎连续七关 WIN、重置与 Tk 键盘事件；如需检查请在游玩之后运行：

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/test_player.py
```

## 实现边界与许可

这是将生成关卡装载到原引擎的适配环境，不是原版关卡合集。地图由公共 JSON 构造，路径初始方向在关卡装载/重置后按公共配置初始化；后续动作使用原源码。该设置与历史验证流程一致。界面使用固定 16 色调色板显示引擎像素，画面缩放不改变碰撞或状态。

来源、原文件及交付文件哈希见 `PROVENANCE.json`；项目播放器使用本目录 MIT `LICENSE`。LS20 源码保留 ARC Prize Foundation MIT 声明，ARCEngine 0.9.3 许可见 `vendor/arcengine/LICENSE-MIT.txt`。NumPy、Pydantic 等依赖适用各自许可。只新增这个独立播放器目录，不更新 main 工作机。
