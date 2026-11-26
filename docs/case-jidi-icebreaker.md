# 案例：极地号破冰船外观模型的三轮迭代建模方法

> 用途：把一次真实建模工程的「方法」沉淀为可复用文档，不改代码。
> 素材：模型优化清单_v3.md、v4 目录源码与报告、session-4b75bef9 / session-3e2d2707 会话记录。
> 约定：每条数值标注来源；读不到的写「未核实」。

## 1 工程概况

一条 90 m 级极地破冰船外观模型，经历三轮迭代：v3 全船参数化重建 → v4 拆模块分工并行 → v5 按参考帧像素重测修正。

| 轮次 | 阶段 | 产物 STEP | 部件数（来源字段） | 关键动作 |
|---|---|---|---|---|
| v3 | 重建 | jidi_rebuilt_v3.step | 176（模型优化清单_v3.md L4 记载，无 report.json 可核） | rebuild_v3.ps1 全船参数化重建 |
| v4 | 分工 | out/jidi_v4.step | 455（out/jidi_v4_report.json 的 parts 字段） | 拆 hull / superstructure / equipment 三模块并行 |
| v5 | 像素重测修正 | out/jidi_v9.step | 462（out/jidi_v9_report.json 的 parts 字段） | 用户 8 条批注 + 子智能体自查吊舱装反 |
| v5 收尾 | — | out/jidi_v10.step | 460（out/jidi_v10_report.json 的 parts 字段） | 网格/复测收口 |

模块级部件数快照（同一 out/ 目录下的独立构建）：h5_hull_report.json = 5、s_super_report.json = 300、h5_equip_report.json = 150（均读 parts 字段）。

v9 交付快照（v9_verify.md L3、L67）：

| 项目 | 值 | 来源 |
|---|---|---|
| 三角面数 | 88 770 | v9_verify.md L3 |
| 全船包围盒 | (0.000, −8.962, −0.565) → (89.950, 8.962, 35.500) | v9_verify.md L67 |
| hull loft bbox | (0.000, −8.908, −0.017) → (89.950, 8.908, 13.750) | v9_verify.md L68 |
| 几何校验 | BRepCheck 全通过 | v9_report.md L3 |

三轮的共性约束：总长 LOA=89.95 m、型宽 B=17.8 m（半宽 HB=8.9 m）、首楼甲板 DECK_FORE=13.75 m、设计水线 T=5.5 m（spec.py L6-L12）。

## 2 方法骨架

七件事串成闭环：真相源 → 分模块 → 量测 → 对位 → 派工 → 复测 → 裁决。落地文件对照：

| 环节 | 落地文件/脚本 | 产出 |
|---|---|---|
| 真相源 | spec.py | 常量与坐标约定 |
| 分模块 | hull.py / superstructure.py / equipment.py | 各模块 build() |
| 量测 | _mesh.py、geom.export_step | mesh json、report.json bbox |
| 对位 | _grid.py、_compare.py、_profile_check.py | 网格图、并排图、差分表 |
| 派工 | team_task + subagent prompt | 结构化工单 |
| 复测 | _verify.py | 四张核查表 |
| 裁决 | DECISIONS.md | 逐条裁决记录 |

### 2.1 单一真相源 spec.py

做法：把所有几何常量收敛到一个文件 spec.py，子体只读、禁止修改（README.md L25）。

坐标系约定（spec.py L3 注释原文）：x = 0（艉封板）→ 89.95（艏尖）；y 右舷为正；z 基线 0；单位 m。

常量表（spec.py L6-L99 摘录）：

| 常量 | 值 | 含义 |
|---|---|---|
| L | 89.95 | 总长 LOA |
| B / HB | 17.8 / 8.9 | 型宽 / 半宽 |
| T / D | 5.5 / 8.2 | 设计吃水 / 型深（主甲板） |
| DECK_FORE | 13.75 | 首楼甲板（船体外板上缘） |
| DECK_AFT | 9.30 | 艉部作业甲板顶面 |
| DECK_AFT_END | 25.0 | 低位甲板止于 x=25.0 |
| STATIONS | 20 个 (x, 半宽, 龙骨高, 丰满度) | 船体站位线型 |
| SECTION_PROFILE | 4 个 Bezier 控制点 | 剖面形状 |

为什么禁止子体改它：spec.py 是唯一真相源，任何子体改它都会让其他模块的贴面/对位全部漂移（README.md 把 spec.py 划归 lead 只写）。子体数值不够时「在自身模块内以常量补充并加注释」（README.md L25）。

可迁移原则：坐标约定 + 常量 + 边界条件写进一个文件并锁定写权限；其余脚本 import spec 而不是复制数值。

### 2.2 模块化写作用域

做法：三个模块各暴露一个 build()，唯一写作用域各是一个文件（README.md L7-L13）。

| 模块 | 唯一写文件 | 内容 |
|---|---|---|
| hull.py | hull agent | 站位线型 loft、舭部/外飘、艏柱、球鼻艏、艉封板、艉部低位甲板切削 |
| superstructure.py | super agent | 三层甲板室、驾驶室+红顶、烟囱+黑冠、主桅、格构桅、球罩、舷墙、舷窗、栏杆 |
| equipment.py | equip agent | A 架、折臂吊、机库、集装箱、救生艇、吊舱、锚机、艏旗杆、月池围板 |

部件命名规则（README.md L23）：名字唯一、英文小写短语，同时作为 STEP 实体名（TDataStd_Name）与渲染配色键（colors.py RULES 按前缀匹配）。

装配器 _build.py 两个开关（README.md L27-L34、_build.py L10-L19）：
- -Parts a,b：只 import 指定模块，支持单模块自检（例如只建 hull 不建全船）。
- -Out 前缀：换输出前缀，避免多个子体并行写同一个 out/jidi_v4.step 互相覆盖。

build.ps1 链式调用 _build.py → _mesh.py → _render.py，任何一步非零退出即停止（build.ps1 L16-L24）。

可迁移原则：文件即写作用域，函数即接口；并行安全靠「只写自己的文件 + 输出前缀隔离」，不靠锁。

### 2.3 机读真值

做法：STEP 是唯一几何载体，后续量测全部走三角网格和逐部件 bbox，不靠人眼看。

链路：
1. _mesh.py：STEP → 三角网格 json。BRepMesh_IncrementalMesh(s, 20, False, 0.15, True)，逐面 Triangulation，坐标 /1000 换米；names 从 STEP 的 PRODUCT 标签正则提取（_mesh.py L18-L42）。
2. geom.export_step：写 STEP 时顺手把每部件 bbox 存进 report.json（geom.py L96-L111，bbox 单位 mm → 再 /1000 回米）。
3. _measure_targets.py：读 report.json 的 bbox，按 19 项目标表核对，输出 PASS/FAIL（_measure_targets.py L8-L29）。

report.json 结构（geom.py L108-L111）：parts（部件总数）、write_status、units、step、note、bbox（逐部件六元组）。bbox 六元组 = [x0, y0, z0, x1, y1, z1]（geom.py L103 从 CornerMin/CornerMax 取），单位 m，例如 hull loft = (0.000, −8.908, −0.017) → (89.950, 8.908, 13.750)（v9_verify.md L68）。

目标核对表是 19 项（v9_targets.md 实测 20/20 PASS，但 TARGETS 字典本表 19 项，多出的第 20 项是表尾汇总行——见 v9_targets.md L26「部件总数：462；未通过项：无」）。每项带允许区间，例如 loa [89.90, 90.00]、radome_d [3.20, 3.60]（_measure_targets.py L9-L28）。

可迁移原则：把「像不像」翻译成「可判定的 bbox 区间」，交付物 = STEP + 机读报告，评审才能自动化。

### 2.4 参考图像素标定

做法：frame_05 正侧视做双轴独立线性标定，再互校。

| 轴 | 锚点 A | 锚点 B | 比例 | 来源 |
|---|---|---|---|---|
| x | 艉端 35 px ↔ 0 m | 艏端 581 px ↔ 89.95 m | 0.16474 m/px | 模型优化清单_v3.md L5；_compare.py SX_REF=89.95/(581-35) |
| z | 首楼甲板 220 px ↔ 13.75 m | 水线 267 px ↔ 5.50 m | 0.17553 m/px | spec.py L4 z=13.75-(py-220)*0.17553 |

互校：z 轴外推到龙骨 y≈300 px → z≈−0.3 m，近似基线 0，标定自洽（模型优化清单_v3.md L6）。

颜色掩膜（_compare.py L20-L24）：
- 红壳：red = (R>110) & (R-Bl>55) & (R-G>55)。
- 白体：white = 高亮 & 低饱和（max-min < 22）。
- 黄件：yellow = (R>170) & (G>120) & (Bl<120)。
- ROI 过滤：red[0:60, 0:62]=False 去掉画面左上角红标，避免误判为船体上缘。

_profile_check.py 额外做两件事（L17-L33）：用 loose 宽松红掩膜抓水下暗红，loose[301:,:]=False 去掉船影/水面；ztop<=16 时才算船体上缘，排除「红顶/吊臂等上层红色结构不是船体上缘」的伪影。

可迁移原则：标定要双轴独立 + 互校；掩膜 + ROI 阈值全部可复算，别把「取色」做成玄学。

### 2.5 米制网格

做法：模型与参考帧套同一套米制网格，逐格比对（GRID.md L5-L17）。

- 列：x 每 5 m 一个字母，A=0-5 … R=85-90（共 18 列）。
- 行：z 每 5 m 一个数字，1=0-5 … 8=35-40（共 8 行）。
- 格号 = 列+行，如 K5 = x 50-55 / z 20-25（驾驶室上层）。

生成命令：python _grid.py out/jidi_v9.json out/grid（GRID.md L14）。_grid.py main() 产出 8 张图（_grid.py L115-L143）：model_side、model_top、rear_stern、front_bow、section_pod、model_rear、ref_side、zones。多视角覆盖侧视/俯视/艉视/艏视/局部剖面（吊舱 x 4.0-7.0 切片）/艉视。

责任分区 Ⅰ-Ⅶ（GRID.md L28-L38）：

| 区 | 格号范围 | 内容 | 负责模块 |
|---|---|---|---|
| Ⅰ | A1-C3 | 艉部线型、冰刃斜切、双吊舱 | hull + equipment |
| Ⅱ | D3-E4 | 艉部作业甲板、A 架、折臂吊、蓝箱 | equipment |
| Ⅲ | F3-H4 | 机库、集装箱、月池围板 | equipment + superstructure |
| Ⅳ | I4-J6 | 烟囱、黑冠、主桅 | superstructure |
| Ⅴ | K4-M5 | 甲板室、驾驶室、红顶、球罩、格构桅 | superstructure |
| Ⅵ | N4-P4 | 首楼甲板、舷侧走廊开口、舷墙 | superstructure |
| Ⅶ | Q1-R3 | 艏部线型、艏柱、水下斜坡 | hull |

跨区要素（舷窗排、舷墙、栏杆）按「落位在哪个区就归哪个区的模块」（GRID.md L38）。

子体在区内五步（GRID.md L42-L46）：先按 1 m 划小格 → 在 grid_ref_side.png 读参考边界 → 在 report.json 查同格零件 bbox 对比（差 >0.3 m 才动手）→ 改完重跑 _grid.py 叠回网格 → 回报。

回报格式（GRID.md L46）：格号 | 零件名 | 参考(x0,x1,z0,z1) | 实测 | Δ | 处理。

「格号 → 模块 → agent」映射：用户圈注 → 读列字母+行数字 → 查责任分区表 → 找模块与 agent → 写工单（GRID.md L50-L56）。

v9 装配后已建立的对照（GRID.md L62-L75 摘录）：

| 零件 | 格号 | 参考 | v9 |
|---|---|---|---|
| 双吊舱桨盘 | A-B1 | x 4.45~6.3 / z −0.2~2.8 | x 4.40~6.30 / z −0.57~2.85 |
| 黑冠 | I5-J5 | x 40.2~45.8 / z 24.2~27.45 | 同 |
| 球罩 | M4-M5 | Ø3.4 / 心 (59.7, 0, 26.2) | 同 |
| 红顶 | K4-M4 | x 51.0~65.2 / z 22.95~23.6 | 50.4~65.4 / 23.60 |
| 舷侧走廊白条 | N3-O3 | x 61.5~74 / z 11.4~13.2 | 同（外凸 +0.11） |
| 艏柱 | R2-R3 | x 89.95 / z 5.5~13.75 | 89.95 / 7.05~13.75 |

可迁移原则：网格是「定位语言」，让图上的圈注能变成可派工的坐标区间。

### 2.6 图像自检

做法：三张自检图把「像不像」做成可读的对比。

- _grid.py 的 render_grid（L32-L82）：z-buffer 三角投影。逐三角做重心坐标插值，深度 zbuf 只保留更近者（zzz > buf 才覆盖），得到带遮挡的实心投影；配色 color_for 按部件名前缀匹配（hull→红、container→蓝、a frame/crane/propeller→黄、rescue→橙、porthole/window/pod→深色、deck→绿，其余浅灰）。
- _compare.py（L15-L105）：并排图（REFERENCE frame_05 vs MODEL）+ 轮廓叠加图。参考轮廓红、模型轮廓蓝（L103：red = reference / blue = model silhouette）；逐列取轮廓顶点转米坐标，重叠画出。
- _profile_check.py（L58-L90）：逐点包络偏差表。模型 hull 部件 x-z 包络 vs frame_05 像素轮廓，3 m 步长逐点比，偏差 >1.0 m 记「上缘偏/下缘偏」并计数。

实测效果：v4 同口径偏差 >1.0 m 有 11 项，v5（h7_profile.md 口径）降到 2 项（v9_report.md L42）。

可迁移原则：自检图要「同网格、同比例、同配色」，差异一眼可见；再用逐点表把「看得像」量化成「差多少米」。

### 2.7 分工与复测

做法：lead 定规则，建模 agent 并行写，verify agent 只读复测。

- lead：写 spec / 网格 / 目标表 / 装配器 / 渲染（README.md L13）。
- 3 个建模 agent：hull / super / equip，各写一个模块。
- 独立 verify agent：只读复测，明确「不要采信 lead 的表」——v9_verify.md L5 原文：表内数字全部由脚本从网格 / report 现算，未从 lead 的表里转抄；与 lead 表冲突处以本表实测为准。

verify 产出 4 张核查表（v9_verify.md）：

| 表 | 名称 | 判据 |
|---|---|---|
| 表 1 | 主尺度与关键构件 | 44 项，逐项偏差 vs 容差，PASS/临界/FAIL |
| 表 2 | 贴面统计 | 舷窗 54 + 窗 34：|d|≤0.10 贴合、d>0.10 悬空、d<-0.15 埋入、x 越出墙面 >0.30 记越界 |
| 表 3 | 可疑悬空件 | 与其余 461 件 bbox 间隙全部 >0.15 m 才判真悬空 |
| 表 4 | 重复或穿模 | 同名 0；bbox IoU>0.90 的部件对；盒状大件完全包住小件记真埋入 |

v9 复测结论（v9_verify.md L242-L252）：表 1 PASS 43 / 临界 1 / FAIL 0；贴面 88 贴合 / 悬空 0 / 埋入 0 / 越界 0；真悬空 0；真埋入 0；真重复 0（IoU=1 的 14 对是 X 形交叉撑）；终版实质缺陷 0 条。

裁决用 DECISIONS.md 逐条记录（15 行 11 条）。两条真实裁决：

| # | 议题 | 裁决 | 理由 |
|---|---|---|---|
| 1 | 艏柱前倾 31° vs 34° | 接受 34° | 水线艏端 84.3 + 甲板艏端 89.95 是硬约束，两点必给 34.4°；brief 的 31° 作废 |
| 2 | 低位甲板台阶 27.7 vs 22.5 | 改 25.0 | 红线标定 22.5~23、像素直读 25.5，折中；切削位置 26.0 留 1 m 过渡 |

标定分歧单独归类、不计缺陷（v4_verify.md L168-L176）：lattice_top / mast_top / top_any 的参考 30.9（v3 像素标定）vs final 定案 32.2 / 35.5，属标定口径分歧，按 lead 裁决保留实测值供追溯，不当作缺陷重报。

可迁移原则：建模与复测分开，复测必须从「原始几何」重算而非转抄；裁决落文档，保留「提出方/议题/裁决/理由」四列；标定分歧与实质缺陷分开记账。

### 2.8 工单要素

从 session-4b75bef9 的 subagent prompt（L493 及 team_task description）提炼出的工单模板字段：

| 字段 | 内容 | 本工程实例 |
|---|---|---|
| 身份 + 认领 | 角色名 + task board 编号，先 get 再 claim | 「你是 hull agent。任务 board: task-1（先 team_task_get task-1 再 claim）」 |
| 背景 | 项目根、上一版产物 | 「项目根：…cad-output；v3 模型 jidi_rebuilt_v3.step（176 部件）」 |
| 写作用域 | 唯一可写文件，其余只读，禁 git commit | 「只写 v4/hull.py」 |
| 必读/必看 | 接口、常量、helper、参考图清单 | README.md、spec.py、geom.py、rebuild_v3.ps1 L34-60、frame_05/07/09 |
| 坐标系 | 三轴约定 + 关键标高 | 「x=0 艉封板 → 89.95 艏尖；y 右舷正；z 基线 0；首楼甲板 13.75 / 主甲板 8.2 / 水线 5.5」 |
| 逐条改动 | 部件名 → 内容，带数值 | 「球鼻艏椭球」「艏柱 89.95 处半宽 0.55 / 龙骨 10.90」 |
| 自检 + 达标线 | 命令 + 退出码 + 尺寸判据 | 「pwsh -NoProfile -File build.ps1 -Parts hull -Out h_hull 退出码 0；bbox x 0~89.95 / y ±8.9 / z −0.02~13.8」 |
| 硬约束/禁止 | 不允许的动作 | 「禁止修改 spec.py；数值不足在自己模块补常量」 |
| 回报格式 | 结构化回报 | 「格号 | 零件 | 参考 | 实测 | Δ | 处理」 |

可迁移原则：工单要「可认领、可写、可自检、可验收、可回报」五要素齐全，缺一就会漂移。

### 2.9 命名与目录约定

命名约定（README.md L13 + v4 目录实测）：
- 无前缀：spec.py / geom.py / colors.py / hull.py / superstructure.py / equipment.py —— 常量、helper、模块。
- 下划线前缀：_build.py / _mesh.py / _render.py / _grid.py / _compare.py / _profile_check.py / _measure_targets.py / _verify.py —— lead 的装配/量测/自检工具。
- 输出：out/<前缀>.step + <前缀>_report.json + <前缀>_views.png，前缀由 -Out 决定。

## 3 失败模式与教训

### 3.1 子智能体模型不可用（派工前不校验模型）

证据（session-3e2d2707）：
- L123-L124：subagent stern、bridge「failed before it finished. It left no closing message.」。
- L234：失败根因是 403 insufficient balance（billing_error），对应 ai78/gpt-6.2-astra。
- L175：用户回「注意使用有效的子智能体，比如ds官方的」。

对照（session-4b75bef9）：不显式指定 model 的 7 个 agent（hull / super / equip / verify / hull2 / super2 / equip2，subagent/catalog 共 7 条）全部成功，subagent-settled 全部 stopReason=completed，无一条 error。

结论：派工前必须显式校验模型可用性（list_subagent_models）并准备回退；「failed before it finished」不是任务失败，而是计费/路由层失败，别误读成代码问题。

### 3.2 依赖下载失败（无外网）

证据（session-3e2d2707）：
- L245：uv pip install cadquery 失败于「Failed to download vtk==9.6.2」（4 retries / 129.5s，无外网）。
- L292-L293：改用 cadquery-ocp-novtk 成功，装到 cadquery-ocp-novtk==8.0.1.1.0 + cadquery-ocp-proxy==8.0.1.1.0。

教训：VTK 只用于可视化，STEP 建模用不到；无网环境优先装 novtk 变体，别把整个 cadquery 依赖树当硬要求。

### 3.3 坐标系/贴面类缺陷

证据（模型优化清单_v3.md）：
- P2-13：三排舷窗 y 写死 6.81 / 6.41 / 5.81，而各层半宽是 6.4 / 5.8 / 6.8、船体 8.9 → 第一、二排悬空 0.4/0.6 m，第三排埋进驾驶室；红壳那排（z=10.7）整排埋在 8.9 m 宽船体里。
- P2-19：GTransform 整体 X 拉伸 ratio=1.1533 把 parts[2:] 的 X 尺寸一起乘，球罩变 3.0×2.6 椭球、桨叶/窗/吊臂同步变长。

教训：贴面件 y 不能写死，要查 hull_halfbeam(x) / layer_halfbeam(z)；整体变换只缩位置、不缩尺寸，或对球/桨/窗按 1/ratio 反向补偿。

### 3.4 每轮返工都来自「没先量再改」

证据（v9_report.md L11-L22 用户 8 条批注，摘 5 条）：

| 批注（截图原话） | 根因 | 处理 |
|---|---|---|
| 「尾部还得往上翘很多」 | v4 艉端龙骨 3.55 斜切过缓 | 艉端龙骨 3.55→6.60，4 站重排 |
| 「哪来的球鼻艏？」 | v4 加了椭球球鼻 | spec.BULB=None，删球鼻实体 |
| 「反了？」（烟囱） | 黑冠后高前低、白体收分过狠 | 黑冠前高后低、白体下宽上窄 8.80→5.60 |
| 「你模型形状都不对吧」（驾驶室顶） | 格构桅 6.2 m 宽 + 大平台 | 格构桅收 3.0 m、球 Ø3.40、补喇叭基座 |
| 「哪里来的板？」（艉端白板） | 舷墙用中部半宽当外缘 | 外缘改 shell_halfbeam−0.30 |

全部是用户看图指出后，才逐像素重测重改。

教训：开工前先量参考、再改模型；改完叠回网格自检。把「量测」放前面，能省掉大半轮返工。

## 4 可直接复用的检查清单

### 开工前

| 动作 | 命令 / 判据 |
|---|---|
| 定坐标与常量 | 写 spec.py 单文件，锁定写权限 |
| 标定参考帧 | 双轴独立线性标定 + 互校，0.16474 / 0.17553 这类系数落常量 |
| 校验子智能体模型 | list_subagent_models，不可用则换路由/回退，不派工 |
| 校验依赖 | python -c 探测 OCP/novtk，无网先装 novtk |
| 建目标表 | _measure_targets.py 目标区间 + 依据 |

### 每轮迭代

| 动作 | 命令 / 判据 |
|---|---|
| 单模块自检 | pwsh -NoProfile -File build.ps1 -Parts hull -Out h_hull，退出码 0 |
| 叠网格自检 | python _grid.py out/<前缀>.json out/<前缀>_grid，读格号与参考对齐 |
| 并排 + 轮廓 | python _compare.py out/<前缀>_side.png out/<前缀> |
| 逐点包络 | python _profile_check.py out/<前缀>.json，偏差 >1.0 m 项数下降才算改对 |
| 尺寸核对 | python _measure_targets.py out/<前缀>_report.json，PASS/FAIL 全绿 |

### 交付前

| 动作 | 命令 / 判据 |
|---|---|
| 全装配 | build.ps1 无 -Parts 参数，BRepCheck 全通过 |
| 独立复测 | python _verify.py v9，四张表无实质缺陷 |
| 贴面复查 | 舷窗悬空 0 / 埋入 0 / 越界 0（判据 |d|≤0.10 贴合） |
| 悬空/穿模复查 | 真悬空 0、真埋入 0、真重复 0 |
| 裁决归档 | 临界项与口径差写进 DECISIONS.md |

## 5 与工具化的接口

不写新工具，只列这套方法需要哪些原语，供后续插件按「输入 → 输出 → 判据」实现。

| 原语 | 输入 | 输出 | 判据 |
|---|---|---|---|
| 标定 | 参考帧 + 两轴锚点 | 比例系数（x/z） | 双轴互校自洽 |
| 掩膜量测 | 参考帧 + 颜色阈值 + ROI | 轮廓点/特征 bbox | 掩膜不含伪影（红顶≠船体上缘） |
| 网格叠图 | 模型 mesh json + 参考帧 | 同网格侧视/俯视/分区图 | 格号可读、模型与参考对齐 |
| 差分表 | 参考轮廓 + 模型包络 | 逐点 Δ 表 | 偏差 > 阈值项数 |
| 工单生成 | 圈注格号 + 责任分区表 | 结构化工单（九字段） | 五要素齐全 |
| 复测 | report.json + mesh json | 主尺度/贴面/悬空/穿模四表 | 从原始几何重算，不转抄 lead 表 |

## 6 从案例缺陷到九角度审查（映射表）

这张表把案例里的真实缺陷逐条对到 `cadw_audit` 的审查角度 —— 也就是说，**同样的错，下一轮会在交付前被机械地拦住**。

| 案例里的真实缺陷 | 证据 | 审查角度 | 判据 |
|---|---|---|---|
| v3 舷窗三排 y 写死 → 悬空 0.4 / 0.6 m，第三排埋进驾驶室 | 模型优化清单 P2-13 | `float` / `buried` | 底面与支撑面差 > `gap_tol` 0.10；体积 >80% 落在体积大 10 倍的零件内（缩进包含，贴面件不误报） |
| 红壳那排舷窗（z=10.7）整排埋进 8.9 m 宽船体 | 同上 | `buried` | 同上 |
| 舷墙用中部半宽当外缘 → 艉端多出一块白板 | v9 用户批注「哪里来的板？」 | `shape` + `clash` | 轮廓格内 Δ > `profile_tol`；与船体交叠体积 |
| A 架腿 r 0.28 → 侧视像实心管 | v5 工单「细黄杆门架」 | `shape` | 轮廓 Δ（侧视上缘/下缘） |
| 烟囱「反了」（黑冠坡向、白体收分 8.80→5.60） | v9 用户批注 | `shape` | 目标带 dz / dx 越界 |
| 格构桅 6.2 m 宽压到球罩上方 | v9 用户批注 | `clash` + `shape` | 与球罩 bbox 交叠；球罩 Ø 目标带 |
| 462 件流水线生成的同名/重复件 | v9_verify 表 4 | `dup` | 同名零件；bbox IoU > 0.9 |
| 流水线生成的草图欠定义 / 过定义 | cadw_measure sketches | `sketch` | `over_defined` 非 0 → P0；`fully_defined=false` → P1 |
| 「哪来的球鼻艏」 | v9 用户批注 | `shape` + `datum` | 目标带无对应项；包络内多余体 |
| 整体 X 拉伸 1.1533 把球拉成椭球 | 模型优化清单 P2-19 | `shape` | dx vs Ø 目标带（8.8 的 1.15 倍会直接越界） |
| 导出件与当前模型不是同一版 | 多轮覆盖 out/ 的产物目录 | `datum` | STL 三角数/包围盒与 parts 不一致 |

## 7 规范流程关卡（把案例做法变成门禁）

| 阶段 | 关卡 | 不通过就停 |
|---|---|---|
| 开工 | 标定互校（两轴 m/px 差 <10%）+ 主尺度台账 + 责任分区 + 模型可用性校验 | 不许量任何尺寸 |
| 建模 | 单模块自检退出码 0 + 包围盒在允许区 + 实体 BRepCheck 通过 | 不许进差分 |
| 每轮 | `cadw_diff` 本区 FAIL 归零 + `cadw_audit` P0 归零 | 不许调形状/涂装，不许交付 |
| 交付 | 独立复测四表（主尺度/贴面/悬空/重复）+ 裁决归档 + 台账 `finish=delivered` | 不许说「做好了」，只能报 partial |

四道关卡对应四个工具：`cadw_ref`（开工）、`cadw_grid`+`cadw_diff`（每轮）、`cadw_audit`（结构）、`cadw_job`（交付）。

## 附录：关键数值速查

| 数值 | 值 | 来源 |
|---|---|---|
| 总长 LOA | 89.95 m | spec.py L6 |
| 型宽 / 半宽 | 17.8 / 8.9 m | spec.py L8-L9 |
| 设计吃水 / 型深 | 5.5 / 8.2 m | spec.py L10-L11 |
| 首楼甲板 / 艉作业甲板 | 13.75 / 9.30 m | spec.py L12-L13 |
| 低位甲板止点 | 25.0 m | spec.py L14 |
| x 标定 | 0.16474 m/px（35 px↔0，581 px↔89.95） | 模型优化清单_v3.md L5 |
| z 标定 | 0.17553 m/px（220 px↔13.75，267 px↔5.50） | spec.py L4 |
| 网格 | 18 列 A-R × 8 行 1-8，x/z 每 5 m | GRID.md L7-L8 |
| v3 部件数 | 176 | 模型优化清单_v3.md L4 |
| v4 部件数 | 455 | jidi_v4_report.json parts |
| v9 部件数 / 三角面 | 462 / 88 770 | jidi_v9_report.json parts、v9_verify.md L3 |
| v10 部件数 | 460 | jidi_v10_report.json parts |
| 模块部件数 hull/super/equip | 5 / 300 / 150 | h5_hull/s_super/h5_equip 三份 report.json parts |
| v9 全船包围盒 | (0, −8.962, −0.565) → (89.950, 8.962, 35.500) | v9_verify.md L67 |
| 贴面判据 | |d|≤0.10 贴合 / d>0.10 悬空 / d<-0.15 埋入 | v9_verify.md L87 |
| 轮廓包络阈值 | 1.0 m | _profile_check.py L76-L80 |
| 烟囱 / 黑冠顶 | 38.2~47.0 / 27.45 m | spec.py L55-L56 |
| 红顶 / 球罩 / 主桅顶 | 23.60 / Ø3.40 / 35.50 m | spec.py L54/L59/L57 |
| A 架顶 / 桨径 | 17.50 / 3.5 m | spec.py L74/L88 |
| 烟囱白体底/顶宽 | 8.80 / 5.60 m | spec.py L55 |
| 驾驶室 x 范围 | 51.9~64.3 m | spec.py L53 |
| 格构桅顶 / 机库长 | 32.20 / 6.0 m | spec.py L58/L77 |