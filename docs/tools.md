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

## 环境变量

| 变量 | 作用 |
|---|---|
| `CADW_PYTHON` | 指定 Python 解释器 |
| `CADW_PART_TEMPLATE` / `CADW_ASM_TEMPLATE` / `CADW_DRW_TEMPLATE` | 指定模板 |
| `CADW_STATE_DIR` | 状态目录（锁/缓存），默认 `%LOCALAPPDATA%\\dsh-cad-workbench` |
| `CADW_LOCK_TIMEOUT_MS` | 等锁上限，默认 120000 |