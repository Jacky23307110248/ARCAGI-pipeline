# CP0 LS20 规则、机关与物件审计

**状态：CP0 已通过所有补充双重验收。** 基础机制审计、补给动作免扣步、计数零边界、生命恢复以及 Tycho wrapper 有效 RESET 策略均已由独立验收和主 agent 复核通过。当前进入 CP1 M0 v0.4 重新独立审查；M1、关卡生成和 CP2 尚未启动。

## 版本与证据边界

- 规则真值：Tycho 缓存游戏 `ls20-9607627b`，源码 `ls20.py` SHA-256 `67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e`，metadata SHA-256 `bc919750fba3d0549e12faf552a4a2999530ef4d02d048c0bbc3a9c72c3da4bd`。
- Duck 中的同游戏 ID 运行能佐证动作、关卡推进、重置和终局行为，但没有将历史运行绑定到上述源码哈希；不声称同源码构建。DSH 历史不在本次 CP0 范围。
- Tycho 旧运行记忆只作线索。逐条核对后，ring 不改变形态而是补回步数；目标要求坐标精确相同且形状/颜色/旋转全匹配；L2 彩色对象是颜色触发而非钥匙/门；形态、颜色和旋转是不同循环状态。详见 `CP0_TYCHO_MEMORY_RECONCILIATION.md`。
- 运行探针均加载 Tycho 本地 ARC engine，在原关卡的内存 clone 上通过 `perform_action` 或明确标注的 UI/source 状态核验运行；不修改 Tycho 缓存、Duck 项目或同步来源。不调用网络、模型或 `.env`。

## 规则结论

### 棋盘、动作与状态

- 棋盘为 64×64 像素；玩家和格子式对象为 5×5。`ACTION1/2/3/4` 分别向上、下、左、右移动一个 5 像素单位。
- 玩家有独立的形态、颜色、旋转三个状态；初值由关卡数据决定。形态循环 6 种，颜色循环 4 种，旋转循环 4 个角度。
- 进入对应触发格时，该属性向前循环一步。未进入触发格时不触发；离开后再次进入会再次触发。同格形态+颜色触发可在一次动作中都生效；相邻触发则在分别进入时逐个生效。

### 目标与反馈

- 目标成功条件是玩家左上坐标与目标坐标完全相同，且形态、颜色、旋转三项全部匹配；多目标关必须完成所有目标，完成次序不限。
- 目标错配会挡住移动且不消耗步数。真实 ACTION4 复现了目标反馈层在五帧内改变、下一帧恢复；这层反馈不改变碰撞和胜利规则。
- `kvynsvxbpi` 是配置成目标属性的图形样例；目标与其样例完成后被移除并在复位时恢复。目标框 `vjotnebuqo` 是展示层；带 `vfkkzdgxzx` 的那一框在目标完成时隐藏。L6 实际 ACTION 完成第二目标时标记由可见变为隐藏，且第一目标保持未完成。
- L1 有额外提示：当前属性与未完成目标匹配时，屏幕提示框和目标旁标记出现；条件消失或短反馈结束时隐藏。提示由实际旋转触发动作令状态匹配后出现，返回帧中的两个提示区均记录到五帧显示、随后清除。探针使用真实 L1 布局和触发器，但为隔离这次状态转变而将玩家放在触发格前一格、预设目标要求的形态/颜色及差一档的旋转；并非从原始出生点完整游玩的轨迹。

### 步数、补给、墙与生命

- 每关按 `StepCounter` 初始化步数；通常每次方向动作按关卡的 `StepsDecrement` 扣步（未设置时为 2，部分关为 1）。墙阻挡仍扣步；目标错配阻挡不扣步。**若本次动作拾取到 `npxgalaybz`，该补给先恢复计数到初始值，并豁免这一步扣数。**
- `npxgalaybz` 是一次性步数补给：拾取后将计数恢复到该关初始值、该次拾取动作不另扣步，并移除；死亡/RESET 时恢复。通关不要求拾取。
- 步数计数从正数降至 0 后仍未耗尽；后续一次扣步使计数低于 0 时才触发耗尽。如果该次动作完成目标，则胜利检查先于耗尽处理。
- 每次关卡初始化（包括进入新关）和显式 level RESET 都将生命恢复到 3。普通耗尽只消耗一条命，非终局时回到本关起点并恢复玩家属性、步数、拾取物、目标状态和路径状态，剩余生命保留；第三次耗尽进入 `GAME_OVER`。
- Tycho 包装器 `tycho/__init__.py` 会设置 `ONLY_RESET_LEVELS=true`，这才是本项目的 Tycho 规则真值：非 `WIN` 时 RESET 始终重置当前关，包括新关 `action_count=0` 和 `GAME_OVER`；`WIN` 时仍执行 full reset 回到 campaign L1。裸 `ARCBaseGame` 在未导入 Tycho 的环境中会根据 `action_count==0` 选择 full reset；该默认分支已与 Tycho 有效策略区分，不作为本项目运行行为。

### 推压器

- `gbvqrjtaqo` 的名称后缀 `_t/_b/_l/_r` 决定方向。实际四方向动作都复现了玩家被推压器向外携带一格、推压器随后独自回到起点的完整过程；一个动作对步数只扣一次。单格推压动作返回 17 个内部帧，动画队列清空后才返回调用者。
- 推压距离由沿方向射线遇到的最近墙或目标决定，射线坐标从推压器起点沿方向偏移 1 像素，再按 5 像素格采样；中间可用空格数为移动距离。墙与目标均可停止推压。
- 前方紧贴阻挡物（零空格）或射线上没有墙/目标时，推压动画不启动；玩家停在接触位，与推压器坐标重合。靠近地图右缘的零射线夹具停在边界内，没有越界。
- Arcengine 的 `perform_action` 是同步调用：会运行并返回整次动作的帧序列；只有返回后才能进行下一次普通调用。未测试并发线程是否会被另行拒绝，这不是可观察的游戏规则。

### 路径联动机关

- 不可见 `xfmluydglp` 像素掩码会与其覆盖的形态/颜色/旋转触发格建立联动。方向动作前路径上的触发格先移动一步；若玩家目的格被墙阻挡，路径移动回滚；失败复位和显式 RESET 会把路径位置、方向还原。
- 原版掩码拓扑已逐格枚举：L5 一条 11 格直线；L6 两条 21 格直线和一个 40 格闭环；L7 一条 29 格直线。没有分支或交叉；L6 闭环拐角的真实 ACTION 轨迹确认会沿唯一可用方向转弯。原版范围不需要虚构分支测试。

### 雾化与其余对象

- `Fog=True` 只出现在 L7。它由接口渲染层按玩家中心（坐标加 1.5）的半径遮蔽；实测在 19.506 像素样本仍可见、20.506 样本已变为雾色，符合源码严格 `distance > 20` 的条件。探针测的是实际 UI overlay，不把它误称为单纯棋盘 sprite。
- `eqatonpohu` 为目标错配短反馈面；`ghizzeqtoh` 与 `hoswmpiqkw` 是仅首关出现的全局/目标旁反馈框；`sqpmiygfvh` 是耗尽生命时动态加入的全屏死亡覆盖层。
- 六个无 tag 的 3×3 模板是玩家形态显示调色板，不是地图对象。`nszegiawib` 和 `xvrpzkggig` 是无规则引用的可见装饰/棋盘背景；无 gameplay tag、不进入规则目标/触发器分支。它们的设计者命名意图未知，但没有未决的状态转移功能。
- 全 36 个 sprite 模板的尺寸、可见/阻挡/交互层属性、七关出现位置和分类见 `CP0_SPRITE_INVENTORY.txt`；目标/HUD/装饰源码语义见 `CP0_VISUAL_OBJECT_AUDIT.md`。

## 七关机制组成

| 关卡 | 已确认组成 |
|---|---|
| L1 | 单目标；旋转触发；独有的属性匹配提示；无形态/颜色触发 |
| L2 | 单目标；旋转触发；两个步数补给 |
| L3 | 单目标；颜色、旋转触发；两个补给；两只推压器 |
| L4 | 单目标；形态、颜色触发；两个补给；八只推压器；无旋转触发 |
| L5 | 单目标；三类触发；三个补给；八只推压器；一条形态联动路径 |
| L6 | 两个属性要求不同的目标；三类触发；三个补给；两只推压器；三条路径（含闭环） |
| L7 | 单目标；三类触发；六个补给；三只推压器；一条路径；开启雾化 |

逐关数据表（颜色为源码 palette code；`StepsDecrement` 未设置时引擎默认扣 2）：

| 关卡 | 起点 | 起始 (形态,颜色码,旋转°) | 目标 (坐标; 形态,颜色码,旋转°) | 步数 / 每步扣数 | 触发 (形态/颜色/旋转) | 补给/推压器/路径 | Fog |
|---|---|---|---|---|---|---|---|
| L1 | (34,45) | (5,9,270) | (34,10; 5,9,0) | 42 / 1 | 0/0/1 | 0/0/0 | 否 |
| L2 | (29,40) | (5,9,0) | (14,40; 5,9,270) | 42 / 2 | 0/0/1 | 2/0/0 | 否 |
| L3 | (9,45) | (5,12,0) | (54,50; 5,9,180) | 42 / 2 | 0/1/1 | 2/2/0 | 否 |
| L4 | (54,5) | (4,14,0) | (9,5; 5,9,0) | 42 / 1 | 1/1/0 | 2/8/0 | 否 |
| L5 | (49,40) | (4,12,0) | (54,5; 0,8,180) | 42 / 2 | 1/1/1 | 3/8/1 | 否 |
| L6 | (24,50) | (0,14,0) | (54,50; 5,9,90); (54,35; 0,8,180) | 42 / 1 | 1/1/1 | 3/2/3 | 否 |
| L7 | (19,15) | (1,12,0) | (29,50; 0,8,180) | 42 / 2 | 1/1/1 | 6/3/1 | 是 |

每行原始字段可由 `cp0_level_data.py` 重新生成，逐物件位置与 tag 数量见 `CP0_SOURCE_INVENTORY.txt` / `CP0_SPRITE_INVENTORY.txt`。

## 碰撞顺序与可达叠放

实际动作循环及受控 clone 复现了以下顺序敏感行为：

- 触发格先于墙被扫描时，动作虽被墙挡住但触发状态仍变化；墙先被扫描时会提前结束扫描，后面的触发不生效。
- 错配目标与触发重叠时，目标仍挡住移动，测试的两种列表次序都触发属性变化；该拒绝不扣步。
- 补给先于墙时补给会被拾取并恢复步数，即使之后被墙挡住；墙先于补给时扫描终止，补给保留且步数扣除。
- 独立成本夹具：初始计数20、当前计数7、无补给对照执行普通动作后变6；进入补给后变20（不是19），证明补给动作豁免当步扣数。补给先于同格墙后停在原位、移除补给并保持20；墙先于补给后停在原位、补给保留且计数变6。
- 同格双触发、相邻触发、未进入、离开再进入的动作轨迹已覆盖；不可能组合未做穷举。

## 主要可复现证据索引

- 全源/关卡/哈希盘点：`cp0_source_inventory.py`、`CP0_SOURCE_INVENTORY.txt`、`cp0_sprite_inventory.py`、`CP0_SPRITE_INVENTORY.txt`。
- 逐关起点/目标属性/步数/机制数据：`cp0_level_data.py`、`CP0_LEVEL_DATA_EVIDENCE.txt`。
- 补给动作成本例外：`cp0_refill_action_exemption_probe.py`、`CP0_REFILL_ACTION_EXEMPTION_EVIDENCE.txt`。
- 计数零边界、耗尽复位与生命初始化：`cp0_life_reset_probe.py`、`CP0_LIFE_RESET_EVIDENCE.txt`、`CP0_LIFE_RESET_FINDING.md`；Tycho 包装器有效 RESET 策略：`cp0_tycho_reset_policy_probe.py`、`CP0_TYCHO_RESET_POLICY_EVIDENCE.txt`。
- 基本动作、循环、pickup、胜利、生命、fog：`cp0_runtime_suite.py`、`CP0_RUNTIME_SUITE_EVIDENCE.txt`、`cp0_focused_clean.py`、`CP0_FOCUSED_RUNTIME_CLEAN_EVIDENCE.txt`。
- 碰撞顺序、目标复位、路径回滚：`cp0_collision_reset_probe.py`、`CP0_COLLISION_RESET_EVIDENCE.txt`、`cp0_path_trigger_actions.py`、`CP0_PATH_TRIGGER_ACTION_EVIDENCE.txt`。
- 路径拓扑/闭环：`cp0_path_topology.py`、`CP0_PATH_TOPOLOGY_EVIDENCE.txt`、`cp0_l6_cycle_action.py`、`CP0_L6_CYCLE_ACTION_EVIDENCE.txt`。
- 四向推压完整动作：`cp0_integrated_pusher_action.py`、`CP0_INTEGRATED_PUSHER_EVIDENCE.txt`、`CP0_INTEGRATED_PUSHER_REPORT.md`。
- 推压边界/API时序：`cp0_pusher_edge_lock_probe.py`、`CP0_PUSHER_EDGE_LOCK_EVIDENCE.txt`、`CP0_PUSHER_EDGE_LOCK_REPORT.md`。
- 雾区、错配反馈、目标完成标记和首关提示：`cp0_visual_controlled.py`、`CP0_VISUAL_CONTROLLED_EVIDENCE.txt`、`cp0_natural_hint.py`、`CP0_NATURAL_HINT_EVIDENCE.txt`。
- Duck 版本/运行交叉核对：`CP0_DUCK_CROSSCHECK.md`；对象显示语义：`CP0_VISUAL_OBJECT_AUDIT.md`；旧 Tycho 记忆冲突：`CP0_TYCHO_MEMORY_RECONCILIATION.md`。

## 主 agent 对原版 CP0 的历史复核记录

主 agent 曾重跑 clean runtime、真实 pusher、反馈/marker、path trigger/rollback 脚本，退出码均为 0；记录到的源 SHA-256 均与基线一致。基础复核记录见 `CP0_MAIN_REVIEW.md`（历史记录）；补给与生命/RESET 补充签署分别见 `CP0_SUPPLEMENTAL_MAIN_REVIEW.md` 和 `CP0_LIFE_RESET_MAIN_REVIEW.md`。执行记录和个别早期废弃夹具错误保留在 `CP0_FOCUSED_RUNTIME_DEV_LOG.txt`，不作为接受证据。
