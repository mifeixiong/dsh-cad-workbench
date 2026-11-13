# Changelog

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