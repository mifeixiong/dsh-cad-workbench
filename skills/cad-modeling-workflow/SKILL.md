---
name: cad-modeling-workflow
description: "SolidWorks 机械 CAD 总控流程：需求→参数表→批准→Feature Graph 建模→实测校核→截图→工程图→交付台账；含硬关卡、问题预算与失败处理。"
---

# CAD 建模总控流程

> 目标不是「把模型画出来」，而是**交付一个有实测证据的零件/图纸**。
> 任何一步没有证据，就不能说"做好了"。

## 0. 开工前（硬关卡）

1. 调 `cadw_doctor`。确认 `capabilities.model`、`capture`、`step` 为 true；
   工程图还要 `capabilities.drawing`。
2. 读 `solidworks-com` 技能（API 事实与坑）与 `cad-feature-graph` 技能（DSL）。
3. 如果用户给了图纸/照片：先用可用的读图工具转写尺寸，做「尺寸台账」（见 §2），
   不要凭印象报数。

**没有参数表不许建模；没有实测不许交付；没有明确批准不许改用户已有文件。**

## 1. 判断任务类型

| 模式 | 触发 | 做法 |
|---|---|---|
| M1 单零件 | 一个零件、几何简单 | 单线程：参数表 → 建模 → 实测 → 交付 |
| M2 多零件/机构 | 装配、传动、多个零件 | 先拆零件清单，每个零件单独 M1；配合/装配体 v0.1 不做，如实说明 |
| M3 图纸重建 | 用户发图纸/截图 | 读图 → 尺寸台账 → 一轮提问 → Feature Graph → 实测比对 |

## 2. 需求与参数表

**只问真正缺的**，每题必须附推荐值。问题预算：

- 简单零件 5–10 题；
- 严格校核（载荷/公差/材料都要依据）10–50 题，但分批问，不要开局全抛。

参数表模板（写进消息正文，不塞进问题弹窗）：

```
零件：<名称>
材料：<材料>（默认：未指定 → 不写材料属性，只报几何）
单位：mm
关键尺寸：
  外形   length × width × height = ...
  特征   <孔/槽/圆角> 位置 + 尺寸 + 数量
  基准   原点在 <位置>，上方向 <+Z>，主轴 <...>
验收：
  包围盒 [x, y, z] mm（容差 max(1 mm, 2%)）
  体积（可选）
  必须通过：4 视图非空白、STEP 导出非空、尺寸台账全部落实
未知/假设：<逐条列出>
```

尺寸台账四分类（图纸重建必做）：**图上明示 / 可唯一推导 / 用户给值 / 真正未知**。
只有第四类才提问。

## 3. 批准

把参数表 + 建模计划（节点序列）+ 验收标准放在**消息正文**，然后用
`ask_user_question` 只放「批准 / 修改」两个选项。用户明确批准后才能建模。

## 4. 建模

1. `cadw_session({action:"new_part"})` 或 `open` 用户文件（只读场景用 open）。
2. `cadw_model({doc, graph, dry_run:true})` 先校验图，检查计划文本。
3. `cadw_model({doc, graph, expect:{bbox_mm}})` 正式建模。
   **整张图一次提交**（不要把 sketch/extrude/hole 拆成多次 cadw_model：每次多 1.5–2 s 进程+附着+重建）。
   - 失败会自动回滚；看 `rollback.done` 与 `error.details.created`。
   - `verification.status` 必须是 `verified`；`mismatch` 就改图重来，不要嘴硬。
4. 扫掠：先画路径草图（开放 polyline 可只有 2 点）与闭合轮廓草图，再 `{"op":"sweep","profile":"轮廓id","path":"路径id"}`。
5. 阵列：先建种子特征（孔/凸台等）并记 id；线性阵列给方向边上一点 `{"at":[x,y,z]}`，圆周阵列给轴边/圆边上一点。
   方向/轴的符号取决于所选边的朝向；结果不符时改 `flip` 或换一条边。
6. 需要"看得见"时传 `visual:true`（慢，每步置前）。

**一次只操作一个文档**。`doc` 令牌变了（Save As 后 doc_id 会变）就重新取。

## 4.5 装配（cadw_assembly）

1. `cadw_assembly({action:"new"})` 新建装配体（或 `cadw_session new_assembly`）。
2. `insert`：`[{path,x,y,z,fixed}]`（mm）。**x/y/z 是组件的几何中心**（SW2024 实测；不是零件原点），布局时按中心算坐标。零件必须先保存到磁盘；脚本会自动打开引用零件以便配合。
3. `mate`：`a/b` 用 `{component:"plate-1", plane:"top"}`；type 用 coincident/distance/parallel/perpendicular/tangent/angle。
   固定件建议先 `fixed:true`；运动件不要三面锁死。
4. 验证：`list` 看组件与配合；`mate_count` 必须增加；保存用 `cadw_session save_as`。
5. 干涉/运动仿真 v0.2 不支持，如实说明。

## 5. 自检（必做）

1. `cadw_measure({doc, scope:"all", with_massprops:true})`：
   包围盒/体积/实体数/草图审计/基准面轴向。
2. `cadw_capture({doc, views:["iso","front","top","right"], contact_sheet:true})`：
   看图确认不是空白、孔位没有镜像、切除方向没反。
   4 视图里至少等轴测 + 俯视要能看出特征。
3. 有疑问就量：不要靠"看起来对"。

## 6. 工程图与导出

- 先 `cadw_session({action:"save_as", path:"cad-output/<name>.sldprt"})`（工程图需要磁盘文件）。
- `cadw_drawing({doc, sheet:"A3", views:["front","top","right","iso"], export:["pdf","dxf","dwg"]})`。
  返回的 `views` 必须是 4 个；`warnings` 里出现"退化/失败"要在交付里说明。
- `cadw_export({doc, formats:["step","stl"]})`。
- `cadw_review({paths:["...dxf","...step","...stl"]})`；DXF 的 WARN 要解释（例如"没有标注尺寸"），
  不要当没看见。

## 7. 交付台账

1. `cadw_job({action:"start", name, plan:{expect...}, doc})`。
2. 建模/导出/出图时带 `job_id`，产物自动入账。
3. `cadw_job({action:"finish", job_id})`：
   `delivery.status` 只有 `delivered` 才能说交付；`partial` 要列阻塞项。
4. 最终回复：给文件路径、实测数字、未解决项与假设。不要只贴 JSON。

## 8. 失败时

| 现象 | 处理 |
|---|---|
| `COM_UNAVAILABLE` | 先手动启动 SolidWorks 到主界面，再 `cadw_doctor` |
| `DOC_BUSY` | 等另一个会话结束；不要同时开两个建模会话 |
| `BUILD_FAILED` | 看 `details.cause`；先 `dry_run` 查图，再检查边/面选点是否在实体上 |
| `PLAN_MISMATCH` | 工具返回 ok:false：实测与批准方案不符；改图或报告偏差，不得改 expect 蒙混 |
| `UNSUPPORTED` | 如实说明能力边界（如装配配合、FreeCAD 后端 v0.1 不支持） |

## 9. 必须遵守

- 不覆盖用户已有 `.sldprt/.sldasm/.slddrw`，除非 `confirm:true` 且明确告知路径。
- 不把 `ActiveDoc` 当操作对象；只用 `doc` 令牌。
- 不静默降级：缺模板、缺转换器、缺 pywin32 都要说出来。
- 不在没批准的情况下改参（尤其是孔径/壁厚/载荷）。