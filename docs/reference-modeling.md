# 参考图对位建模：规范、网格、差分与对抗审查

> 本文件是 v0.4 新增能力的**契约**：五个工具的数据结构、判定语义与调用顺序。
> 实现（`scripts/cad_refkit.py`、`scripts/cad_audit.py`、`lib/dispatch.js`）与本文件冲突时，以本文件为准并同步修改。

## 0 为什么需要这一层

v0.3 的能力是"单零件、给定尺寸、建了量一下"。真实任务更像极地号破冰船那次：
**只有参考图（视频截帧/照片/图纸扫描），要还原外形与布置，然后多轮找差异、改模块、再复测。**

那次的成功与失败都很清楚（见 [case-jidi-icebreaker.md](case-jidi-icebreaker.md)）：

| 做对的 | 代价 |
|---|---|
| 先把参考帧标定成米，再逐像素量关键尺寸 | 标定错一次，全线返工 |
| 模型与参考帧套同一套米制网格（A–R × 1–8），差异用格号说话 | 没有网格时，"这里不对"无法转成工单 |
| 三个模块三个写作用域，一个 lead 定常量 | 无 |
| 独立复测 agent 只读、不许采信 lead 的表 | 无 |
| 失败：舷窗 y 写死 → 悬空 0.4 m / 埋入船体；整体 X 拉伸 1.15 把球拉成椭球 | 没有"悬空/埋入/过定义"这类机械审查，只能靠人看图 |
| 失败：子智能体指定了不可用的 provider/model，四个 agent 全部 "failed before it finished" | 派工没有模型可用性校验与回退 |

结论：**参考图建模缺的不是"画得像"，是四件事——标定、网格、差分、机械审查。**
本层把四件事做成工具，并且让"差异"直接变成可派给子智能体的工单。

## 1 主流程（五步 + 两个返工环）

```
参考图 ─ cadw_ref(calibrate) ─→ 标定好的帧 + 掩膜
            │
            ├─ cadw_ref(measure) ─→ 参考真值表（可固化为 targets 目标带）
            │
模型 ─ cadw_export(stl) / cadw_measure / 逐件 bbox 报告
            │
            ├─ cadw_grid ─→ 多视角米制网格叠图 + 格号索引（模型 vs 参考，同格可比）
            ├─ cadw_diff ─→ 差分表：格号 | 零件 | 参考 | 实测 | Δ | 判定
            └─ cadw_audit ─→ 对抗审查：悬空/埋入/穿插/重复/欠定义/过定义/形状/体量/镜像
                        │
                        └─ cadw_dispatch ─→ 分区工单（含递归分配条款与模型选择策略）
                                              │
                                    子智能体建模（各自写作用域）
                                              │
                                    回到 cadw_grid / cadw_diff / cadw_audit 复测
```

**返工环 A（形状）**：`cadw_diff` 有 FAIL 行 → `cadw_dispatch` 按格号派工 → 改模块 → 重出模型 → 再 diff。
**返工环 B（结构）**：`cadw_audit` 有 P0 行 → 先修 P0（悬空/埋入/过定义/破面），再谈像不像。**结构错的时候不要调形状。**

## 2 坐标与视图约定

- 模型：`u = x`（纵向，艉→艏）、`v = z`（高度，向上）、`w = y`（横向，右舷为正），单位 m。
  可用 `axes` 改写；默认值写在工程里，后续所有量测/差分都按它换算。
- 参考图：`u` 沿图像**列**方向（左→右递增），`v` 沿图像**行**方向（下→上递增）。
  因此 `v` 的标定斜率通常为负（图像 y 向下、模型 z 向上）。
- 五个标准视图（`view` 字段）：

| view | 横轴 u | 纵轴 v | 用途 | 可选切片 |
|---|---|---|---|---|
| `side` | x | z | 侧视轮廓、上层建筑、烟囱/桅 | 无 |
| `top` | x | y | 俯视布置、宽窄、左右对称 | 无 |
| `front` | y | z | 艏/艉正视、横向跨距、双吊舱 | `slice: {axis:"x", from, to}` |
| `rear` | -y | z | 从艉向艏看（左右镜像） | 同上 |
| `section` | y | z | 局部剖面（吊舱、机库门） | 同上，必填（`spec.slice` 或标定时写在帧上的 `slice`） |

## 3 工程文件：`.cad-workbench/refkit/<project>.json`

```jsonc
{
  "version": 1,
  "id": "jidi",
  "units": "m",
  "axes": { "u": "x", "v": "z", "w": "y" },
  "model_bbox": [0, -8.9, -0.6, 89.95, 8.9, 35.5],     // 可选：验收包络
  "grid": { "u_step": 5, "v_step": 5, "w_step": 2, "u_origin": 0, "v_origin": 0, "w_origin": 0 },
  "frames": {
    "frame_05": {
      "path": "…/640_frames/frame_05.png",
      "view": "side",
      "image_size": [616, 400],
      "u": { "px0": 35,  "m0": 0,     "px1": 581, "m1": 89.95 },   // 两点线性标定
      "v": { "px0": 300, "m0": -0.3,  "px1": 220, "m1": 13.75 },
      "scale": { "m_per_px_u": 0.16474, "m_per_px_v": -0.17553 },
      "created": "2026-11-26T11:00:00"
    }
  },
  "masks": {
    "hull":  { "type": "color", "r_min": 110, "r_minus_b_min": 55, "r_minus_g_min": 55,
               "exclude_px": [[0,0,62,60]], "u": [2, 90], "v": [null, 16] },
    "white": { "type": "color", "r_min": 185, "g_min": 185, "b_min": 185, "range_max": 22,
               "exclude_px": [[0,0,62,60]] },
    "dark":  { "type": "dark", "r_max": 70, "g_max": 70, "b_max": 80 }
  },
  "measures": {
    "funnel_body": { "frame": "frame_05", "kind": "bbox", "rule": {…}, "result": {…}, "at": "…" }
  },
  "targets": [
    { "name": "funnel_len", "part": "funnel casing", "field": "dx",
      "ref": [8.6, 8.9], "unit": "m", "note": "烟囱纵向 38.2→47.0，像素实测" }
  ]
}
```

### 3.1 掩膜规则（mask）

| type | 判据 | 关键字段 |
|---|---|---|
| `color` | 通道阈值组合 | `r_min/r_max/g_min/…`、`r_minus_b_min`、`r_minus_b_max`、`r_minus_g_min`、`range_max`（max-min ≤ 值，用于判"灰白"） |
| `dark` | 三通道上限 | `r_max/g_max/b_max` |
| `ink` | 与背景中位色差 | `tol`（默认 18，按通道绝对差之和判定） |

所有掩膜都支持米制区域过滤：`u: [lo, hi]`、`v: [lo, hi]`（可 `null` 表示不限）、`exclude_px: [[x0,y0,x1,y1], …]`。
**区域过滤用米而不是像素**，这样同一套掩膜换帧不用重调。

### 3.2 量测（measure）四种 kind

| kind | 语义 | 规则字段 | result |
|---|---|---|---|
| `profile` | 沿 u 分箱，取掩膜的 v 范围（轮廓） | `scan:"column"`、`u:[a,b]`、`step`、`v_filter:[lo,hi]` | `points:[[u, vmin, vmax], …]` |
| `bbox` | 区域内掩膜的米制包围盒 + 质心 | `roi:{u:[a,b], v:[lo,hi]}` | `{u0,u1,v0,v1,du,dv,cu,cv,pixels}` |
| `extent` | 单条扫描线处的范围 | `at_u` 或 `at_v`、`band`（像素容差，默认 2） | `{u0,u1,v0,v1}` |
| `coverage` | 区间内每箱有掩膜像素的比例（查"连续无断口"） | `u:[a,b]`、`step`、`v_filter` | `points:[[u, ratio],…], min_ratio, gaps:[[u0,u1],…]` |

`measure` 的每个条目必须有 `id`（缺失直接 `BAD_ARGS`）；结果写进 `project.measures[id]`。
条目里带 `target:{…}` 时按该定义固化；顶层 `save_targets: true` + `tol` 时，对**没有** `target` 的条目按
`ref = [值 − tol, 值 + tol]` 自动追加 `project.targets`（名字 `<id>_<字段>`，字段取 `bbox→du`/`extent→dv`）。

`coverage` 的空箱（画面里没有该段）返回 `ratio: null` 并计入 `gaps`，不会"凭空消失"。
`extent` 结果统一给 `u0/u1/v0/v1`（未扫的那个方向给同一个值），`band` 缺省 2，`band: 0` 表示只看该列/行。

## 4 差分表（cadw_diff）

行 = 一个可判定的量。两种来源：

1. **目标带**（`targets`）：主尺度、构件 bbox 分量。
2. **轮廓**（`profile`）：某帧的 `profile` 量测 vs 模型 STL 三角网格的逐箱包络。

行结构：

```jsonc
{ "cell": "I4", "name": "funnel_len", "part": "funnel casing", "field": "dx",
  "ref": [8.6, 8.9], "ref_note": "像素实测", "model": 8.80, "delta": 0.0,
  "verdict": "PASS",              // PASS | WARN | FAIL
  "hint": "" }
```

判定：`ref[0] ≤ model ≤ ref[1]` → PASS；越界但 `|delta| ≤ warn_factor × (带宽/2)` → WARN（`warn_factor` 缺省 0.5，
即半个带宽）；否则 FAIL。
`delta` 定义：落到带内为 0（并给出离最近边的余量 `margin`），带外为**同号超差值**（正=偏大）；
MISSING 行的 `margin` 为 `null`（消费方按字段取值不会 KeyError）。

字段口径（`field`）：`x0/x1/y0/y1/z0/z1/dx/dy/dz/cx/cy/cz` 按字面；`zmax=z1`、`ymax=y1`（**不是**绝对值），
绝对值另有 `zabs=max(|z0|,|z1|)`、`yabs=max(|y0|,|y1|)` —— 水下件（吊舱、桨叶）必须用 `zabs`。
格号由 bbox 中心落在 `grid` 的哪一列/行决定（列 = u 每 `u_step` 一个字母 A 起始；行 = v 每 `v_step` 一个数字 1 起始）。

轮廓行按格汇总：

```jsonc
{ "cell": "K4", "u_range": [50, 55],
  "ref": { "vmax": 22.31, "vmin": 0.0 }, "model": { "vmax": 22.40, "vmin": 0.0 },
  "delta_vmax": 0.09, "delta_vmin": 0.0, "verdict": "PASS" }
```

## 5 对抗审查（cadw_audit）——**九个角度机械审查，模型说了不算**

审查只吃事实：零件 bbox 表、草图约束状态、STL 网格、差分表。九个角度，全部可判定：

| # | 角度 | 判据（默认阈值来自 `rules/audit_rules.json`） | 严重度 |
|---|---|---|---|
| A1 | `datum` 基准/单位 | 总包围盒在 `model_bbox` 允许区内；单位声明；原点落在预期角点 | P1 |
| A2 | `float` 悬空 | 零件的支撑面（`support_planes` 里的 z 值列表、或其它零件顶面）与零件底面 `z0` 的差 > `gap_tol`(0.1 m)，且不在 `cantilever` 白名单 → 悬空 | P0 |
| A3 | `buried` 埋入 | 小件以 `buried_tol` 内缩后**完全**落在宿主内（缩进包含，贴面件不误报），宿主限 `buried_host_allow`（只放盒状大件：机库/集装箱/舱） | P0 |
| A4 | `clash` 穿插 | 交集体积 > `clash_vol`(0.5 m³) **且**占小件体积 > `clash_frac`(0.35；船型/建筑型建议 0.8)，且不在 `clash_allow`；`surface_parts`（玻璃带/舷窗/栏杆/角肋/加强筋）直接跳过 | P1 |
| A5 | `dup` 重复 | 同名零件 → P1；bbox IoU > `dup_iou`(0.9) 仅 **P2 提示**（交叉撑/对角杆共包络，IoU 也是 1.00，不能直接判重复）；镜像件（port/starboard）跳过 | P1/P2 |
| A6 | `sketch` 草图定义 | `cadw_measure(scope:"sketches")` 里 `fully_defined=false` 的段数 > 0 → 欠定义；`over_defined` → 过定义 | P0（过定义）/ P1（欠定义） |
| A7 | `shape` 形状 | `cadw_diff` 的 FAIL 行；或轮廓 `|Δv| > profile_tol`(1.0 m)；或目标带外 | P1 |
| A8 | `mass` 体量/破面 | 体积/包围盒体积 < `fill_min`(0.02) → 空壳或破面（P0）；> `fill_max`(0.999) → 近乎满包络（P2）；实体数 0 归 `datum` | P0/P2 |

每条发现：

```jsonc
{ "angle": "float", "severity": "P0", "subject": "porthole row 1 · 3", "cell": "H4",
  "expect": "底面贴 9.30", "actual": "9.70", "delta": 0.40,
  "evidence": { "part": "porthole …", "bbox": [...] },
  "hint": "改 superstructure.py 里舷窗排的 y/z 常量，或改用 shell_halfbeam(z)" }
```

汇总：`verdict` = 有 P0 → `FAIL`；有 P1 → `WARN`；否则 `PASS`。
**P0 未清零前，形状类结论一律不算数。**

### 5.1 事实输入的形状

```jsonc
// parts：逐件 bbox（来自 STEP 报告、装配体清单或人工整理）
[ { "name": "funnel casing", "bbox": [38.2, -4.3, 13.75, 47.0, 4.3, 24.2] } ]

// sketch：直接抄 cadw_measure({scope:"sketches"}) 的 sketches[]（真实字段）
[ { "name": "Sketch1", "segments": 12, "points": 13,
    "fully_defined": false, "constrained_status": 2, "constrained_status_text": "under_defined" } ]
// 审查认 constrained_status_text（under/over/no_solution/invalid_solution），
// 也兼容 fully_defined 布尔与旧键 over_defined

// diff：cadw_diff 的输出（可直接把工具返回值塞进来）
{ "rows": [ … ], "profile_rows": [ … ] }
```

## 6 工单（cadw_dispatch）——把格号变成子智能体任务

一个区（zone）一张工单，字段固定：

| 段 | 内容 |
|---|---|
| 身份 | 角色名、task board 占位、claim/complete 指令 |
| 背景 | 本区未通过项（`cadw_diff` FAIL + `cadw_audit` P0/P1），逐条带格号与 Δ |
| 环境 | 项目根、**唯一写作用域**、只读清单、禁 git commit、禁改 spec/真相源 |
| 必读 | 真相源文件、几何 helper、参考帧（帧 id + 路径） |
| 坐标 | u/v/w 约定、单位、关键标高 |
| 任务 | 按格号排序的改动项（带参考边界与现模型边界） |
| 自检 | 构建命令模板（带 `-Out <前缀>`）、diff/audit 复测命令、达标线 |
| 硬约束 | 命名唯一、贴面、实体有效、不许改别人模块 |
| 递归 | 允许再拆子区（给出子区划分规则与最大深度/子代理数），子代理同样具备本能力 |
| 模型 | 先 `list_subagent_models` 校验可用；缺省继承父会话；失败回退顺序；不许盲指定 provider |
| 回报 | `格号 \| 零件 \| 参考 \| 实测 \| Δ \| 处理`，外加"仍需 lead 裁决"清单 |

**递归分配与模型选择的硬规则**（写进每张工单）：

1. 子智能体先看自己的格区，若单区内可判定项 > `split_threshold`（默认 6）条，**必须**再拆子区，用 `subagent` 分给下级，并在工单里写清下级写作用域（不得与本级重叠）。
2. 分配前必须调 `list_subagent_models`；只使用返回列表里的 provider/model 组合。列表里没有显式指定的，**继承父会话的默认模型**（历史事故：指定了不可用组合，4 个子智能体全部 "failed before it finished"）。
3. 子代理失败时按 `fallback` 顺序重派（默认：继承默认 → 列表内同 provider 的另一模型 → 把任务拆小再派）。
4. 深度上限 `max_depth`（默认 2：lead → 区代理 → 子区代理）。到底后自己干，不再下派。
5. 任何一级都不许改真相源（`spec.py`/参数表）、不许 git commit、不许跨写作用域。
6. **禁用清单**：`model_policy.exclude` 里的组合不得派给任何一级；若 `list_subagent_models` 只剩禁用项，回报 lead，不要硬派。

## 7 与既有能力的关系

| 既有 | 新增 |
|---|---|
| `cadw_measure` 给单文档几何事实 | `cadw_ref` 把参考图变成米制真值 |
| `cadw_capture` 给四视图位图 | `cadw_grid` 给"同格可比"的网格叠图 + 格号索引 |
| `cadw_job` 记作业与信任等级 | `cadw_diff`/`cadw_audit` 给可判定的失败项 |
| `cadw_assembly` 装零件 | `cadw_dispatch` 把失败项派成有写作用域的工单 |

## 8 常见误用

| 误用 | 后果 | 正确做法 |
|---|---|---|
| 没标定就先"看图估尺寸" | 全线返工，且无法证伪 | 先 `calibrate`，用两个已知尺寸互校 |
| 网格只做侧视 | 俯视/艉视的横向错误永远查不出 | 至少 side + top + 一个正视图 |
| 用 bbox 中心当"零件位置"，不读 Δ | 改错方向 | 差分表给格号 + 分量 Δ，按分量改 |
| 先修"像不像"，后修"悬空/过定义" | 改十遍还是错 | 先清 P0 结构项 |
| 派工不写唯一写作用域 | 两个 agent 互踩文件 | 一模块一 agent，写作用域写进工单 |
| 拿 200 MB STL 直接渲染 | 内存与时间失控 | 10 MB / 20 万面 ≈ 8 s；`render` 超过 40 万面自动抽稀并标 `triangles`，需要精度就分切片（`slice`） |
| 只有整船网格却要叠局部参考帧 | 糊成一片 | 用 `slice:{axis,from,to}` 裁到艉部/艏部/吊舱段 |
| 盲目指定 provider/model | 子代理直接失败 | 先 `list_subagent_models`，否则继承 |