# Changelog

## 0.4.0 (2026-11-26) · 参考图对位 + 对抗审查 + 分区派工

来源：一次真实的 90 m 级破冰船外观还原（v3 重建 → v4 分工 → v5 像素重测），把其中可复用的方法固化成工具。
案例复盘见 [docs/case-jidi-icebreaker.md](docs/case-jidi-icebreaker.md)，数据契约见 [docs/reference-modeling.md](docs/reference-modeling.md)。

### 新增工具（5 个）

- `cadw_ref`：参考图工程。两轴两点线性标定（`checks` 判两轴 m/px 比例差 >10%、v 斜率符号、基准点越界）；
  颜色/深色/墨迹掩膜（区域过滤用**米**，换帧不重调）；四种量测 `profile/bbox/extent/coverage`；
  量测项可带 `target` 把"参考值 ± tol"固化成目标带。工程存 `.cad-workbench/refkit/<project>.json`。
- `cadw_grid`：米制网格叠图。side/top/front/rear 多视角，模型（STL z-buffer 投影或带名三角网格）与参考帧
  套**同一套格号**（列 A 起每 u_step、行 1 起每 v_step）；责任分区色块；逐件格号索引 `cells`；
  参考帧放大后叠网格，用户圈注即可读格号。
- `cadw_diff`：差分表。目标带行（精确名优先、`re:` 正则、`matched` 列出实际并集）+
  轮廓行（逐格上下缘，模型侧优先用三角网格沿边采样，bbox 只作降级）；输出
  `格号|指标|零件|分量|参考带|实测|Δ|判定` 的 md 报告与 PASS/WARN/FAIL/MISSING 计数。
- `cadw_audit`：对抗审查九角度 —— `datum`（单位/包络/实体数/STL 一致性）、`float`（悬空）、`buried`（埋入）、
  `clash`（穿插）、`dup`（重复/IoU）、`sketch`（过定义 P0 / 欠定义 P1）、`shape`（差分 FAIL 汇总）、
  `mass`（空壳/破面）、`naming`（命名约定）。阈值数据驱动 `rules/audit_rules.json`，可局部覆盖。
- `cadw_dispatch`：分区工单。按 zone 把 diff/audit 的发现分组，生成九段式工单（身份与任务板、先看差异、
  环境与**唯一写作用域**、必读、坐标、任务、自检与达标线、**子区拆分与递归**、**模型选择与回报**），
  写盘 `.cad-workbench/dispatch/<batch>/<owner>.md`，并给出可直接传给 `subagent` 的 prompt。

### 技能

- 新增 `cad-reference-match`：标定→量测→网格→差分→派工的完整流程、掩膜选择表、口径三条铁律、迭代节奏。
- 新增 `cad-adversarial-review`：九角度判据、P0 优先原则、读报告顺序、规则扩写、常见误判。
- `cad-modeling-workflow`：新增 M4 参考图模式、§4.6 分区并行与子智能体派工（写作用域/模型校验/递归/独立复测）、
  §4.7 参考图对位；自检关卡加入 `cadw_audit` P0 清零；失败表补 `REF_INVALID`、整列同号口径问题、欠定义。

### 关键设计（踩过的坑）

- **口径一致性**：参考掩膜量的是"红壳"，模型侧必须 `model_filter:"hull loft"`，否则舷墙被算进上缘，
  整张表恒定 +1.0 m 假偏差 —— 差分表因此保留 `compare:["vmax"]` 只比可靠的一边。
- **轮廓不能用 bbox**：被布尔切掉的尾部在 bbox 里仍是满高；`mesh` 沿边采样后，破冰船 24 格轮廓从
  "全 FAIL" 变成 17 PASS / 7 FAIL（7 条都是真实差异或掩膜伪影）。
- **精确名优先**：`part:"bridge"` 若只做子串匹配会把 `bridge roof red`/`bridge glazing` 并进来，
  驾驶室长度从 12.4 m 变成 15.0 m；现在同名部件优先，其余并集并回显 `matched`。
- **派工不校验模型 = 白等一轮**：真实事故里 4 个子智能体因指定了不可用 provider/model 全部
  "failed before it finished"；工单现在把"先 `list_subagent_models`、缺省继承父会话、失败回退、
  深度上限 2、子代理原样继承"写成硬规则。

### 独立复核后补的修正（v0.4.0 内）

- `cad_audit` 的 `float`：只从"底面之下的支撑"里取最近者（原先会把一个夹在 0 与 9.3 之间的零件判成"低于 9.3 面 4.3 m"）；
  容差取 `max(gap_tol, buried_tol)` + 1e-9，球罩坐进基座 0.1 m 不再算悬空；向下的负 gap 只在对方**更大**时才报。
- `cad_audit` 的 `units`：`mm` 时 `support_planes` / `model_bbox` / `grid` 一并换算；`facts.bbox_mm` 按"尺寸三元组"读。
- `cad_audit` 的 `sketch`：认 `cadw_measure` 的真实字段 `constrained_status`/`constrained_status_text`（原先只认插件根本不产出的
  `over_defined`，过定义会被误判成欠定义 P1）；新增 `no_solution`/`invalid_solution` → P0。
- `cadw_dispatch` 新增 `model_policy`：`{preferred, exclude, note}`，`exclude` 以"禁用清单（不得派给）"写进每张工单，
  只剩禁用项时要求回报 lead —— 来自真实运维约束（某个模型被明确停用）。
- `cadw_diff`/`cadw_grid` 支持"只有逐件 bbox"时也出模型侧网格图（包围盒+网格，标注为降级模式）。

### 第二轮独立复核（cad_refkit.py）后补的修正

- **分箱**：`profile`/`coverage`/`model_profile` 的箱数改用 `ceil(span/step)`（原来 `round` 会丢尾格，
  `[0,10] step=4` 只测到 8 m 而 `u_range` 仍写 0..10）；采样上限 64 → 512，消除"模型与参考等大也报 MISSING"。
- **视图轴**：`side/top/front/rear` 各用自己的 u/v 轴画零件框、标签与格号（原来统一按 x/z，
  俯视/正视的框会画到画布外面）；新增 `cells_by_view`；越界格号改 `N<列>R<行>` 不再与 `C-13` 混读。
- **参考帧叠图**认 `u_origin/v_origin`（原来与格号不同源，整体错位）。
- **目标带判定**：WARN 阈值改为 `warn_factor × 半带宽`（原来乘整带宽，阈值放宽一倍）；`warn_factor: 0` 生效。
- **差分**：允许"只有 STL/网格"的纯轮廓差分；`model_filter` 匹配不到部件时报 MISSING 而不是静默退回整网。
- **量测**：缺 `id` 直接 `BAD_ARGS`；顶层 `save_targets`+`tol` 生效；`extent` 的 `band: 0` 生效、`pixels` 统计真实像素并补齐 `u0/u1/v0/v1`；
  `coverage` 空箱计入 `gaps`。
- **其它**：`field_value` 的 `zmax/ymax` 改为字面值（绝对值另给 `zabs/yabs`）；`render` 先校验视图再写盘（不留半套产物）；
  `frame.slice` 不再是死字段；二进制 STL 允许尾部多余字节；标题移到画布底部留白，不再压住列标签；MISSING 行补 `margin: null`。

### 验证

- 离线 `python test/refkit_cases.py` → 28/28（合成参考图：标定/四种量测/目标带/网格/差分 PASS 与 FAIL/MISSING，另含两轴比例告警、未知掩膜/未知视图错误路径）。
- 离线 `python test/audit_cases.py` → 23/23（九角度、贴面件不误报埋入、船体不当宿主、clash 相对阈值、IoU 只作 P2、rules 开关、幂等、STL 交叉核对）。
- Node `node --test test/selftest.mjs` → 12 例（工具注册、参数面、技能 provider）；`test/dispatch_cases.mjs` → 17 例（含跨 Z 列号、交界冲突、区界反转、格号排序、prompt 形态）。
- 真实帧回归：frame_05 标定 → 量测（烟囱 38.2~47.0、球罩 Ø3.4、驾驶室 ~12 m、A 架顶 17.7）→
  与 v10 装配报告差分：目标带 8/8 PASS（总长 89.95、半宽 8.914、首楼甲板 13.75、烟囱 dx 8.8…）。


## 0.3.0 (2026-11-13) · 辅助线驱动建模 + 圆柱面同心配合 + 窗口管理

- Feature Graph 新增 `line` 实体与 `construction: true`（构造线/辅助线，不参与拉伸轮廓）；polyline/circle/arc 也可标记构造。
- sketch 新增 `holes`：孔位可用 `intersection:[lineA,lineB]` 由两条辅助线交点给出，不必手算坐标。实测 80×60×5 板 4×Ø6 孔体积 23434.513 mm³（误差 0）。
- `cadw_assembly` 新增 `hole` 选择器：按零件局部坐标 + 半径找圆柱孔壁，映射到装配体上下文做同心配合（`MateConcentric`）。
- 修复动态分派下圆柱面选择：`GetSurface`/`IsCylinder`/`CylinderParams` 是属性；圆柱面实体必须用 `Select4` 才能进入选择集。
- 实机：11 零件高保真 LeARM 复刻（框形连杆 + 关节孔 + 舵机盒），包围盒 160×460×300 mm；7 个同心关节配合创建成功。

### 已打开窗口管理（cadw_session）
- `list` / `status` 默认只列可见窗口：1-based 窗口序号、`active`、`saved`；隐藏/参考文档只给
  `hidden_count`，`include_hidden:true` 才列进 `documents[]`（大装配体可能有几十个隐藏引用，默认不刷屏）。
  另返回 `document_count` / `window_count` / `dirty_count` 与人读摘要。
- `close` 支持 `doc` / `docs` / `indexes` 批量关闭；新增 `close_others`（`keep` 必填）与更安全的 `close_all`。
- 未保存文档默认 `CONFIRM_REQUIRED`；`confirm:true` 走 `QuitDoc` 明确丢弃（不弹保存框），
  `save_unsaved:true` 先保存再关（无磁盘路径报 `SAVE_FAILED`）。
- 默认只动 `Frame.ModelWindows` 可见窗口；隐藏/参考文档需 `include_hidden:true`，避免装配体丢引用。
- `dry_run:true` 预览计划；关闭后轮询 `GetDocuments` 复核，未消失报 `CLOSE_FAILED`，部分失败 `partial:true`。
- 附着修复：SW2024 偶发 `GetActiveObject` 返回 `MK_E_UNAVAILABLE`（ROT 未注册），改为先枚举
  `SLDWORKS.exe` 进程、确认已运行后用 `Dispatch` 回退；list/status 仍然只附着不拉起。

### 窗口管理验证
- 离线 `test/session_cases.py`：47 例（窗口清单、批量关闭、脏文档、隐藏文档、dry-run、保存失败）。
- 实机 `test/live_windows.py`：18/18（清单、脏文档拒绝/丢弃、CloseDoc/QuitDoc、批量关闭、清理）。
- `python test/run_all.py` → 5 项全绿（JS 11 例、common 18 例、session 47 例、图校验 37 例、文件审查）。

## 0.2.1 (2026-11-13) · 装配坐标语义

- 明确并文档化：`cadw_assembly insert` 的 x/y/z 是**组件几何中心**（SW2024 实测），不是零件原点。
- 用该语义完成 LeARM 8 零件参数化复刻（160×456×300 mm，7 个重合配合）。

## 0.2.0 (2026-11-13) · 扫掠 / 阵列 / 装配

### 新增 Feature Graph 节点
- `sweep`：轮廓草图 mark=1 + 路径草图 mark=4，调用 InsertProtrusionSwept4；轮廓必须闭合，路径允许开放折线（2+ 点）。实机 Ø10 × 60 体积 4712.39 mm³。
- `linear_pattern`：方向边 mark=1 + 种子特征 mark=4，FeatureLinearPattern5；支持 count/spacing/count2/spacing2/flip。实机 4 孔体积 92858.41 mm³。
- `circular_pattern`：轴边 mark=1 + 种子特征 mark=4，FeatureCircularPattern5；支持 count/angle/equal_spacing/flip。实机 5 孔体积 92073.01 mm³。
- 草图闭合校验从「sketch 节点」推迟到「使用处」，sweep 路径草图可以是开放折线。

### 新工具 `cadw_assembly`
- new / insert / mate / fix / list；AssemblyDoc.AddComponent5 插入零部件（mm），平面配合通过 Component2.GetCorresponding + AddMate5（coincident/distance/parallel/perpendicular/tangent/angle），MateGroup 子特征枚举配合。
- 实机：40×40 板 + Ø10 销，装配、重合配合（重合1）、固定/浮动、保存全通过。

### 验证
- 离线 4 套全绿（graph 35 例，含 sweep/pattern 正反例）。
- 实机 `test/live_advanced.py`：11/11 PASS（扫掠/线性/圆周/装配/配合/保存）。
- 原 smoke 31/31、回归 10/10 保持。
## 0.1.2 (2026-11-13) · 收窄平台承诺

- CI 只保留 Windows（插件依赖 SolidWorks COM；Ubuntu 只跑离线单测，不能验证产品）。
- 删除路径守卫里的 POSIX 分支与 Windows-绝对路径跨平台判断，规则回到 Windows-only；行为不变。

## 0.1.1 (2026-11-13) · 审计修复

三个子代理并行审计后修复（静态 bug 审查 / SolidWorks 实机对抗 / 效率基准）。

### P0
- 孔位坐标变换：_world_to_sketch 把 model→sketch 变换当成了逆变换且用错矩阵布局，非前视平行面孔会静默偏 20 mm。改为按列主序 s = R·p + t；新增离线单测与实机回归（孔心/质心/体积）。
- 倒角角度单位：InsertFeatureChamfer 角度参数是弧度，之前直接传度（45° 实际 45 rad）。改为 math.radians；实机去料 125.000 mm³。
- 跨进程锁竞态：O_EXCL + stale 接管的 check-then-delete 可让两个进程同时持锁。改为 OS 文件锁（Windows msvcrt.locking / POSIX flock），崩溃由内核释放；新增双进程互斥单测。
- 残留旧文件冒充成功：SaveAs3 失败时若目标已存在，旧文件会被当新产物。导出前把旧目标改名为 .bak，成功判据加 mtime/size 变化与文档路径一致；失败恢复旧文件。新增 mock 单测。

### P1
- 构建失败只捕获 CadError → 其它异常绕过回滚；现在统一捕获并回滚，回滚不完成返回 ROLLBACK_FAILED。
- 失败节点已建的部分特征（如孔草图）未回滚；现在按「构建前特征快照」删除全部残留并复验。
- PLAN_MISMATCH 现在真的返回 ok:false；缺实测也归为 mismatch，不再误报 measured-only。
- cadw_measure 的草图审计从占位变为真实：进入草图编辑模式读段数/点数/约束状态（只读，退出不改几何）。
- 台账并发：per-job 文件锁 + 修订号取目录最大值 + wx 写入；重复 start 返回 JOB_EXISTS；partial 必带 blockers，mismatch 不能 delivered。
- 闭合 polyline 多画一条零长线（off-by-one）修复。
- DXF MTEXT 字高读错字段（char_height）导致假阴性修复；重复线对数公式修复。
- graph_hash 统一以 Python 校验后规范化图为准；原文 hash 另存 graph_hash_input。
- OpenDoc6 补 Configuration 参数；save_as 返回保存后的 doc 身份（原为保存前状态）。

### 效率/健壮性
- 草图实体创建启用 AddToDB（跳过推理求解）、结束草图不再 ZoomToFit；特征 diff 从每节点 2 次全量枚举降为 1 次。
- 路径守卫跨平台（POSIX 系统目录、Windows 大小写不敏感、拒绝 Windows 绝对路径）；basename 统一消毒。
- 工具层统一兜底异常为 {ok:false,error}；parseJsonObject 取最后一个 JSON；子进程临时目录清理；Python 输出 NaN→null。
- cadw_capture 支持 confirm 覆盖；视图去重、尺寸上下限；cadw_export 全失败返回错误；cadw_drawing 模板图幅不匹配/scale>1 给警告；cadw_measure scope 白名单。

### 验证
- 离线 4 套：JS 10 例、common 14 例、graph 33 例、review 用例（含 MTEXT）全绿。
- 实机回归 test/live_regressions.py：10/10 PASS（Top 面孔位、倒角 125、闭合 polyline、回滚、open/save）。
- 原实机 smoke 31/31 保持通过。
- CI：Windows + Ubuntu 离线矩阵通过（修复 UTF-8 与平台路径守卫）。

## 0.1.0 (2026-11-13)

首个可用版本。