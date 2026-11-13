# 工具参考

所有工具返回对象；失败为 `{ok:false,error:{code,message,details?}}`。

## cadw_doctor
参数：`probe_com?: boolean`、`workspace?`。
返回：`platform`、`modules`、`module_versions`、`solidworks{running,revision,year,templates,documents}`、
`freecad`、`lock`、`state_dir_writable`、`workspace_writable`、`capabilities`、`suggestions`、`summary`。

## cadw_session
参数：`action`（status/new_part/new_assembly/open/activate/save/save_as/close/close_all/list）、
`path?`、`template?`、`doc?`、`confirm?`、`visual?`、`allow_outside?`。
返回：`doc{doc_id,title,path,type,saved,active}`、`documents[]`。
Save As 后 `doc_id` 会变；危险动作未确认时返回 `CONFIRM_REQUIRED`。

## cadw_model
节点：sketch/extrude/cut/revolve/hole/fillet/chamfer/sweep/linear_pattern/circular_pattern。
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
type 支持 coincident/concentric/perpendicular/parallel/tangent/distance/angle。
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