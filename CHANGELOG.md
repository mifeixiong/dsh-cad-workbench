# Changelog

## 0.1.0 (2026-11-13)

首个可用版本。

### 工具
- `cadw_doctor`：解释器/依赖/SolidWorks/模板/FreeCAD/锁/能力矩阵自检。
- `cadw_session`：doc_id 令牌、新建/打开/保存/关闭、危险动作确认。
- `cadw_model`：Feature Graph（sketch/extrude/cut/revolve/hole/fillet/chamfer），
  计划期校验、dry_run、逐节点检查、精确回滚、expect 实测比对。
- `cadw_measure`：包围盒、体积（来源标注）、实/面/边数、特征树、草图与基准面信息。
- `cadw_capture`：4 视图 + 2×2 拼图，空白/全黑检测，图片内联。
- `cadw_export`：STEP/IGES/STL/3MF/PDF/PNG，bytes + sha256。
- `cadw_drawing`：A0–A4 工程图，第一角法布局，PDF/DXF/DWG。
- `cadw_review`：DXF 7 项检查（含 SolidWorks 中文图层别名）、STL 水密启发式、STEP 头/单位/包围盒。
- `cadw_job`：不可变修订台账与 delivered/partial/failed 结论。

### 技能
- `cad-modeling-workflow`、`solidworks-com`、`cad-feature-graph`、`cad-drawing-gb`。

### 验证
- 实机 SolidWorks 2024 SP0.1：31/31 PASS。
- 离线：JS 10 例、图校验 29 例、文件审查用例全绿。

### 已知边界
- 不含装配配合、FreeCAD/CadQuery、shell/pattern、扫掠/放样、Web UI、CAD 模式预设。
