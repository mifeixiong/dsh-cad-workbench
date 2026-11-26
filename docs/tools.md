# 工具参考

所有工具返回对象；失败为 `{ok:false,error:{code,message,details?}}`。

## cadw_doctor
参数：`probe_com?: boolean`、`workspace?`。
返回：`platform`、`modules`、`module_versions`、`solidworks{running,revision,year,templates,documents}`、
`freecad`、`lock`、`state_dir_writable`、`workspace_writable`、`capabilities`、`suggestions`、`summary`。

## cadw_session
参数：`action`（status/list/new_part/new_assembly/open/activate/save/save_as/close/close_others/close_all）、
`path?`、`template?`、`doc?`、`docs?`、`indexes?`、`index?`、`keep?`、`dry_run?`、
`save_unsaved?`、`include_hidden?`、`confirm?`、`visual?`、`allow_outside?`。

### 窗口清单（status / list）
默认**只列可见窗口**；`include_hidden:true` 时 `documents[]` 才包含隐藏/参考文档。
每项：`{index,doc_id,title,path,type,saved,window,active}`。
- `index`：1-based 窗口序号，仅在本次调用内有效；关闭后序号会变，重新 list 再取；隐藏文档为 null；
- `window`：是否为可见模型窗口（`IFrame.ModelWindows`）；false 是装配/工程图引用但未显示的隐藏文档；
- `saved`：false 或 null 表示有未保存修改/读不到保存标记（按脏处理）。
另返回 `document_count`（全部已加载文档数）、`window_count`、`hidden_count`、`dirty_count`、
`active_doc_id`、`include_hidden`、`summary`。大装配体常有几十个隐藏引用文档，默认不刷屏。

### 关闭（close / close_others / close_all）
- `close`：`doc`（单个 doc_id）、`docs`（多个 doc_id）、`indexes`（窗口序号）可并用，自动去重；
- `close_others`：`keep` 必填（要保留的 doc_id 或序号），关闭其余可见窗口；
- `close_all`：关闭全部可见窗口；`close_others` / `close_all` 需要 `confirm:true`。
- 有未保存修改的文档默认拒绝（`CONFIRM_REQUIRED`）；`confirm:true` 用 `QuitDoc` 丢弃（不弹保存框），
  `save_unsaved:true` 先保存再关（无磁盘路径 → `SAVE_FAILED`）。
- 默认只动可见窗口；隐藏/参考文档需要 `include_hidden:true`，否则 `HIDDEN_DOC` 或列进 `skipped`。
- `dry_run:true` 只返回 `would_close` / `dirty` / `plan` / `requires_confirm`，不改状态。
- 返回 `closed_documents[]`、`saved_documents[]`、`discarded[]`、`failed[]`、`skipped[]`、`partial`、`remaining`；
  关闭后轮询 `GetDocuments` 复核，没消失报 `CLOSE_FAILED`，部分失败 `partial:true`，不谎报。

`activate` 支持 `doc` 或 `index`（窗口序号），只激活一个文档。

Save As 后 `doc_id` 会变；危险动作未确认时返回 `CONFIRM_REQUIRED`。

## cadw_model
节点：sketch/extrude/cut/revolve/hole/fillet/chamfer/sweep/linear_pattern/circular_pattern；草图支持构造线（`line`/`construction`）与 `holes` 交点定位。
参数：`doc`、`graph`、`dry_run?`、`visual?`、`expect?{bbox_mm,volume_mm3,feature_count}`、
`rollback_on_fail?`（默认 true）、`job_id?`。
返回：`created[]`、`features_before/after`、`measurements`、`verification{status,checks,failures}`、
`rollback{attempted,done,deleted,failed,restored_count}`、`graph_hash`。
错误码：`GRAPH_INVALID`、`DOC_NOT_FOUND`、`DOC_BUSY`、`BUILD_FAILED`、`UNSUPPORTED`。

## cadw_measure
参数：`doc`、`scope?: summary|mass|sketches|planes|tree|all`、`with_massprops?`、`deep_sketches?`（默认 true，进入草图编辑模式只读约束状态）。
返回：`bbox_mm`、`bbox_min_mm`、`bbox_max_mm`、`volume_mm3`（`volume_source: massprops|not_requested|unavailable`）、
`surface_area_mm2`、`centroid_mm`、`solid_count`、`face_count`、`edge_count`、`feature_count`、
`features[]`、`sketches[]`、`planes[]`。

## cadw_capture
参数：`doc`、`views?`（iso/front/top/right/left/back/bottom）、`width?`、`height?`、
`contact_sheet?`、`out_dir?`、`basename?`、`confirm?`（覆盖同名截图）。
返回：`images[{view,path,width,height,stddev,unique_colors,white_ratio,blank}]`、`contact_sheet`、
`image`/`imageNote`（等轴测内联）、`extraImage`/`extraImageNote`（拼图内联）。

## cadw_export
参数：`doc`、`formats`（step/iges/stl/3mf/pdf/png）、`out_dir?`、`basename?`、`confirm?`、`job_id?`。
返回：`artifacts[{format,path,bytes,sha256}]`、`failures[{format,code,message}]`。

## cadw_drawing
参数：`doc`、`sheet?`、`scale?`、`views?`、`first_angle?`、`insert_model_dims?`、
`export?`（pdf/dxf/dwg）、`out_dir?`、`basename?`、`confirm?`、`job_id?`。
返回：`drawing_path`、`views[{name,annotations,dimensions,position_m,scale}]`、`view_count`、`dimension_count`、`annotation_count`、
`artifacts`、`warnings`。

## cadw_review
参数：`paths?` 或 `dxf/stl/step`、`rules?`。
返回：`files[{path,kind,verdict,checks[],stats}]`、`verdict`。
DXF 检查：units、layers_required、layers_forbidden、layer_names（可选）、linetypes、
zero_length、duplicates、dimensions、text_height、text_empty、extents。

## cadw_assembly
参数：`action`（new/insert/mate/fix/list）、`doc?`、`components?`、`mates?`、`component?`、`fixed?`。
`components`：`[{path,x?,y?,z?,fixed?}]`（mm；**x/y/z 是组件几何中心**）；`mates`：`[{type,a:{component,plane},b:{component,plane},distance?,angle?,align?,flip?,lock_rotation?}]`，
type 支持 coincident/concentric/perpendicular/parallel/tangent/distance/angle；关节选择器可用 `hole:{at:[x,y,z],radius}` 做同心配合。
返回：`doc`、`components[{name,path,fixed}]`、`mates[{type,name,a,b,error_status}]`、`mate_count`、`component_count`。

## cadw_job
参数：`action`（start/record/verify/finish/get/list）、`job_id?`、`name?`、`plan?`、`doc?`、
`event?`、`artifacts?`、`verification?`、`accept_unverified?`、`limit?`。
返回：`job`（含 `events`、`artifacts`、`verification`、`delivery`、`revisions`）。

## cadw_ref

参数：`action`（calibrate/measure/list/get）、`project`、`frame`、`masks`、`grid`、`frame_id`、`measures`。

### calibrate
`frame:{id, path, view, u:{px0,m0,px1,m1}, v:{px0,m0,px1,m1}}`：两轴各两点线性标定（v 斜率通常为负）。
`masks:{名字:规则}`：`type=color/dark/ink`，可带 `u/v` 米制范围与 `exclude_px`；`grid:{u_step,v_step,u_origin,v_origin}`。
返回 `frame.scale{m_per_px_u,m_per_px_v,px_per_m_u,px_per_m_v}`、`checks[]`（两轴比例差 >10%、v 斜率符号、基准点越界）。

### measure
`measures:[{id, kind, mask, ...}]`，四种 kind：

| kind | 规则字段 | result |
|---|---|---|
| `profile` | `u:[a,b]`、`step`、`v_filter`、`model_filter`、`compare:["vmax"/"vmin"]` | `points:[[u,vmin,vmax]]` |
| `bbox` | `roi:{u:[],v:[]}` | `u0,u1,v0,v1,du,dv,cu,cv,pixels` |
| `extent` | `at_u`/`at_v`、`band`（缺省 2，0=只看该列）、`v_filter` | `u0,u1,v0,v1` + 未扫方向的同名值 |
| `coverage` | `u:[a,b]`、`step`、`v_filter` | `points:[[u,ratio]]`、`min_ratio`、`gaps` |

条目里写 `target:{name,part,field,model_field,tol,note}` 会把"量测值 ± tol"存成 `project.targets`。
返回 `measures[]`（含 result）、`targets[]`、`warnings[]`。

## cadw_grid

参数：`project?`、`parts?`、`report?`、`stl?`、`stl_units?`、`mesh?`、`units?`、`views?`、`zones?`、
`grid?`、`scale?`、`label_parts?`、`ref_scale?`、`out_dir?`、`basename?`。

- 模型侧：`stl`（单色 z-buffer 投影）或 `mesh`（`{names:[],tris:[[]]}`，按部件配色）；`parts/report` 画逐件包围盒与名字。
- 参考侧：工程里每一帧按自己的标定叠同一套网格；`zones` 画责任分区色块。
- 返回：`model_views[]`、`ref_views[]`、`zones_image`、`contact_sheet`、`cells{零件:{cell,cells,bbox}}`、`images[]`（含 `blank` 判定）。
- 格号：列 = u 每 `u_step` 一个字母（A 起），行 = v 每 `v_step` 一个数字（1 起）；越界格号写作 `N<列>R<行>`。
- 每个视图各有一套轴（side=x/z、top=x/y、front·rear=y/z），标注/零件框/格号按各自视图走；
  结果里 `cells` 用工程轴（默认 x/z），`cells_by_view[view]` 给该视图的轴与逐件格号。覆盖到的格号按列/行区间全量列出（上限 24）。

## cadw_diff

参数：`project`、`parts?`、`report?`、`targets?`、`mesh?`、`stl?`、`units?`、`profile?`、
`profile_measures?`、`tolerance?`、`out_dir?`。

- 目标带行：`{cell,name,part,field,ref:[lo,hi],model,delta,margin,verdict,matched[],hint}`；
  零件名**精确相等**优先，否则子串/正则（`re:`）并集，`matched` 列出实际并进来的部件。
- 轮廓行：`{cell,u_range,ref{vmax,vmin},model{vmax,vmin},delta_vmax,delta_vmin,compare,verdict,method}`；
  `method=sampled`（三角网格沿边采样）或 `bbox`（逐件包围盒，粗）。
- 判定：带内 PASS；越界但 `|Δ| ≤ warn_factor × 带宽`（默认 0.5）WARN；否则 FAIL；模型无匹配零件/该段无几何 MISSING。
- 返回：`verdict`、`rows[]`、`profile_rows[]`、`counts`、`profile_counts`、`report_path`（md）。

## cadw_audit

参数：`parts?`（数组/报告路径/`{name:bbox}`）、`facts?`（`cadw_measure` 输出）、`diff?`（对象或路径）、
`stl?`、`rules?`（只覆盖要改的字段）、`units?`、`grid?`、`model_bbox?`、`support_planes?`、`out_dir?`、`write_report?`。

九角度：`datum` / `float` / `buried` / `clash` / `dup` / `sketch` / `shape` / `mass` / `naming`。
`units:"mm"` 时 parts / support_planes / model_bbox / grid 一并换算；`facts.bbox_mm` 按"尺寸三元组"读。
返回：`verdict`（有 P0 → FAIL）、`counts{P0,P1,P2,total}`、`findings[{id,angle,severity,subject,cell,expect,actual,delta,evidence,hint}]`、
`by_angle`、`rules_used`、`report_path`。阈值在 [rules/audit_rules.json](../rules/audit_rules.json)。

## cadw_dispatch

参数：`project?`、`zones`（`[{code,label,u0,u1,v0,v1,owner,module,write_scope,fill}]`）、`diff?`、`audit?`、
`agents?`、`brief?`（`project_root/must_read/frames/coordinate_note/conventions/build_cmd/verify_cmds/task_board`）、
`recursion?`（`allow/max_depth/split_threshold/max_children`）、
`model_policy?`（`{preferred:[], exclude:[], note}`：`exclude` 会写进每张工单的"禁用清单（不得派给）"，只剩禁用项时要求回报 lead）、`out_dir?`、`batch?`。

返回：`batch`（`dispatch-YYYYMMDD-HHMMSS`）、`count`、`orders[{owner,module,writeScope,zone,cells,findings,findingCounts{P0,P1},children,path,markdown,prompt}]`、
`warnings[]`（未分配项、责任区重叠、缺 agent、未提供差分）、`policy`（模型选择与递归硬规则文本）、`summary`。
`cells` 按列序号再按行号排序；列名用双射 26 进制（A…Z、AA…），负列记 `NEG-1`。

## 环境变量

| 变量 | 作用 |
|---|---|
| `CADW_PYTHON` | 指定 Python 解释器 |
| `CADW_PART_TEMPLATE` / `CADW_ASM_TEMPLATE` / `CADW_DRW_TEMPLATE` | 指定模板 |
| `CADW_STATE_DIR` | 状态目录（锁/缓存），默认 `%LOCALAPPDATA%\\dsh-cad-workbench` |
| `CADW_LOCK_TIMEOUT_MS` | 等锁上限，默认 120000 |