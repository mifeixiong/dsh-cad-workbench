# dsh-cad-workbench

**机械 CAD 工作台**（DeepSeek Harness 插件）：把本机 SolidWorks 变成一组**确定性工具**——
模型用声明式 Feature Graph 建模，插件负责执行、实测校核、视图自检、工程图/格式导出与作业台账。

> 定位：不是「让模型写 COM 脚本」，而是「模型说清要什么，插件做确定的事，并用实测数字证明做对了」。

## 为什么

让模型直接拼 SolidWorks COM，你会得到：孔位镜像（Top 面草图坐标反了却没报错）、
切除方向切反、保存成"副本"却没切换文档、隐藏的错误弹窗卡住进程……
更麻烦的是，**模型自己说"做好了"没有任何证据**。

这个插件把三类东西固化成基础设施：

1. **声明式 Feature Graph（mm）**：模型不碰 COM；计划期校验 + 执行期逐节点检查；
2. **实测优先**：包围盒/体积/特征数/视图像素统计/文件大小与 sha256，任何"完成"都有证据；
3. **边界纪律**：文档身份令牌、跨进程锁、精确回滚、危险动作确认、缺能力就报 UNSUPPORTED。

## 工具（10 个，前缀 cadw_）

| 工具 | 作用 |
|---|---|
| `cadw_doctor` | 环境自检：Python 依赖、SolidWorks 版本/模板、FreeCAD 探测、能力矩阵、锁状态、修复建议 |
| `cadw_session` | 文档会话 + 已打开窗口管理：status/list、新建/打开/保存/激活、close 批量、close_others、close_all；返回 `doc_id` 令牌 |
| `cadw_model` | 按 Feature Graph 建模：sketch/extrude/cut/revolve/hole/fillet/chamfer；dry_run、逐节点检查、Undo 回滚、expect 实测比对 |
| `cadw_measure` | 包围盒/体积（来源标注）/实面边数/特征树/草图段数与约束状态/基准面轴向（查不到时退回经验表并标注） |
| `cadw_capture` | 4 视图 + 2×2 拼图；像素统计自动标记空白/全黑；图片内联回传 |
| `cadw_export` | step/iges/stl/3mf/pdf/png；逐格式复核 bytes+sha256 |
| `cadw_drawing` | 零件/装配体出工程图：第一角法 4 视图、模型尺寸、pdf/dxf/dwg |
| `cadw_review` | 只读审查 DXF（11 项检查）/STL（三角数/退化/水密）/STEP（schema/单位/启发式包围盒） |
| `cadw_assembly` | 装配体：new / insert（mm 定位、固定）/ mate（重合/距离/平行/垂直/相切/角度，组件基准面）/ fix / list |
| `cadw_job` | 作业台账：start/record/verify/finish/get/list；交付结论 delivered/partial/failed + 信任等级 |

随包 4 个技能：`cad-modeling-workflow`（总控流程与硬关卡）、`solidworks-com`（API 事实与坑）、
`cad-feature-graph`（DSL 参考）、`cad-drawing-gb`（GB/T 制图与 DXF 判据）。

## 快速开始

### 1. 安装为 DSH bundle

**方式 A（推荐）**：在启用了 `plugin_manager` 的「创造模式」会话里：

```
plugin_manager install_bundle target=<本仓库绝对路径>
```

**方式 B（本地开发）**：把本目录 link 进 profile 的 `node_modules`，并在 profile 的
`package.json` 里加依赖与 `dsh.profile.bundles` 条目（与 `dsh-mech-workbench` 同法）。
bundle 变更需要**重启 DSH**。

> 不注册任何客户端 UI、不改 `cordis.patch.yml` 之外的宿主配置；插件行由随包
> `cordis.patch.yml` 插入。

### 2. 自检

重启后在新会话里：

```
cadw_doctor
```

期望：`capabilities.model / capture / drawing` 为 true，SolidWorks 版本与三个模板路径可见。
缺 pywin32 / Pillow / ezdxf / 模板时，doctor 会逐项给修复建议，不会假装可用。

### 3. 建一个零件

```json
{
  "units": "mm",
  "nodes": [
    { "op": "sketch", "plane": "front",
      "entities": [{ "type": "rect", "width": 120, "height": 80 }] },
    { "op": "extrude", "depth": 10 },
    { "op": "hole", "at": [30, 20, 10], "diameter": 10 },
    { "op": "fillet", "radius": 5,
      "edges": [{ "at": [60, 40, 5] }, { "at": [-60, 40, 5] },
                { "at": [60, -40, 5] }, { "at": [-60, -40, 5] }] }
  ]
}
```

调用序列：`cadw_session(new_part)` → `cadw_model(dry_run)` → `cadw_model(expect:{bbox_mm:[120,80,10]})`
→ `cadw_measure` → `cadw_capture` → `cadw_session(save_as)` → `cadw_export` → `cadw_drawing`
→ `cadw_review` → `cadw_job(finish)`。

### 4. 关闭多余窗口

`cadw_session(action:"list")` 默认只列可见窗口：窗口序号、是否当前、是否有未保存修改；
隐藏/参考文档只给数量（大装配体可能有几十个），`include_hidden:true` 才列出，默认不动。只保留装配体、关掉 3–12 号零件窗口：

```
cadw_session(action:"close", indexes:[3,4,5,6,7,8,9,10,11,12], dry_run:true)
cadw_session(action:"close", indexes:[3,4,5,6,7,8,9,10,11,12], confirm:true)
# 或者按“只保留”语义：
cadw_session(action:"close_others", keep:[2], confirm:true)
```

有未保存修改时默认返回 `CONFIRM_REQUIRED`；`confirm:true` 丢弃（QuitDoc，不弹保存框），
`save_unsaved:true` 先保存再关。`close_all` / `close_others` 需要 `confirm:true`。

## 安全边界

- **文档身份令牌**：所有写操作必须带 `doc_id`；写入前复核路径/标题，防止特征落到用户另一个文档。
- **跨进程锁**：OS 级文件锁串行化 SolidWorks（Windows `msvcrt.locking` / POSIX `flock`）；进程崩溃由内核释放，锁超时给 `DOC_BUSY`。
- **精确回滚**：构建前记录特征快照，失败时删除本次新增（含失败节点的残留草图）并复验特征数；
  不碰用户原有特征，回滚不完成返回 `ROLLBACK_FAILED`。
- **危险动作确认**：覆盖已有 `.sldprt/.sldasm/.slddrw`、`close_all`/`close_others`、丢弃未保存修改需要 `confirm:true`。
- **窗口保护**：默认只关 `Frame.ModelWindows` 可见窗口；脏文档默认拒绝，`confirm` 才走 `QuitDoc` 丢弃，
  `save_unsaved` 先保存；隐藏/参考文档要 `include_hidden:true`，避免关掉后装配体丢引用。
- **附着不拉起**：`status`/`list` 遇到 `GetActiveObject` 的 `MK_E_UNAVAILABLE` 时，先确认 `SLDWORKS.exe` 进程在运行，
  再用 `Dispatch` 回退；不会因为查询而启动 SolidWorks。
- **路径守卫**：默认只写 会话工作区；拒绝系统目录与 SolidWorks 安装目录。
- **无网络、无遥测**；不执行用户代码（Feature Graph 是数据，不是脚本）。

## 实机验收（本机 SolidWorks 2024 SP0.1）

`python test/live_smoke.py` → **31/31 PASS**，包括：

- 120×80×10 板 + Ø10 通孔 + R5 四角圆角；包围盒逐轴 120/80/10；
- 体积 95214.6 mm³（与理论值 96000 − 785.4 差 < 0.01%）；
- 4 视图 + 拼图非空白；STEP/STL/PDF 均非空；A3 工程图 4 视图 + PDF/DXF/DWG；
- DXF 审查（SolidWorks 中文图层别名）WARN=缺标注、STEP PASS、STL PASS；
- 失败图精确回滚（特征数 23 → 23，删除 凸台-拉伸2/草图3）；
- 过期令牌 `DOC_NOT_FOUND`；锁竞争 `DOC_BUSY`（等待 2.1 s）。

离线测试：`python test/run_all.py` → 5 项全绿（JS 11 例、common 18 例、session 47 例、图校验 37 例、文件审查含 MTEXT）。
实机回归：`python test/live_regressions.py` → 10/10（Top 面孔位、倒角角度、闭合 polyline、回滚、open/save）。
高级实机：`python test/live_advanced.py` → 14/14（扫掠、线性/圆周阵列、装配插入/配合/固定/保存）。
窗口实机：`python test/live_windows.py` → 18/18（窗口清单、脏文档拒绝/丢弃、CloseDoc/QuitDoc、批量关闭、清理）。

## 能力边界（v0.3.0）

支持：零件建模（草图/拉伸/切除/旋转/孔/圆角/倒角）、测量、截图、导出、工程图、DXF/STEP/STL 审查、
作业台账、已打开窗口清单/批量关闭/隐藏文档保护。

**不支持**（会明确报错，不静默降级）：装配干涉检查/运动仿真、FreeCAD/CadQuery 后端、`shell`/
`slot`/`rounded_rect`、偏移基准面、扫掠切除/放样、`cut.both_directions`、Web UI 3D 查看器、
CAD 模式预设、窗口平铺/层叠、跨多个 SolidWorks 实例的窗口管理。`cut` 盲切必须显式给 `depth`；`extrude.flip` 已支持。见 [docs/design.md](docs/design.md#roadmap)。

## 环境要求

- Windows 10/11 + 已授权 SolidWorks（已实测 2024 SP0.1；2018–2025 理论可用，未逐一实测）。
- Python 3.10+：COM 需要 `pywin32`；截图拼图需要 `Pillow`；DXF 审查需要 `ezdxf`。
  可用 `CADW_PYTHON` 指定解释器。
- DSH `>=0.2.2-rc.1 <0.3.0`（host-only bundle，无构建步骤）。

## 许可与致谢

MIT。设计思想来源见 [NOTICE.md](NOTICE.md)：tian-yu200/dsh-solidworks（Feature Graph、
文档身份、事务回滚、expect 实测比对、信任等级）、fyjgtddco/DSH-SW-and-CAD（基准面变换查询、
CADX 图纸校验、GB/T）、LU0220-code-pro-max/dsh-solidworks-modeling（win32com 桥与 doctor）、
zhaizhoux/dsh-cad-freecad（声明式 DSL、不可变修订、统一错误信封）等。
**只借思想与公开 API 事实，未复制代码**（上游含 AGPL-3.0 项目）。