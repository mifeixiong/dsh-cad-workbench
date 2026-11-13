# 排障

## doctor 报 COM_UNAVAILABLE

1. 手动启动 SolidWorks 到主界面（首次安装后必须启动一次）。
2. `cadw_doctor` 看 `solidworks.registered` 与 `running`。
3. 仍失败：确认 Python 带 pywin32（`python -c "import win32com"`），或设 `CADW_PYTHON`。

## 找不到零件模板

- doctor 的 `solidworks.templates.part.path` 为空。
- 在 SolidWorks 里新建一次零件并保存默认模板，或设 `CADW_PART_TEMPLATE`。
- 工程图模板需要 `.drwdot`（如 `gb_a3.drwdot`）。

## 基准面选不中

- 中文模板是 `前视基准面`，英文是 `Front Plane`；插件中英都试。
- 自建基准面用完整名称。

## 草图坐标镜像 / 孔在反面

- Top 面草图 y 轴对应世界 −Z；插件用 `ModelToSketchTransform` 求逆变换处理。
- 若绕过插件写脚本，必须自己换算，否则 SolidWorks 不报错但几何镜像。

## 拉伸/切除失败

- 看 `error.details.cause` 与 `node_index`。
- 切除方向反了用 `flip:true`；贯穿用 `through_all:true`。
- 草图必须闭合；polyline 首尾会自动闭合。

## 回滚失败

- 返回 `ROLLBACK_FAILED` 时特征可能残留；看 `details.rollback.failed`。
- 手动在 SolidWorks 里删除残留特征，再 `cadw_measure` 复验。

## 截图空白

- `cadw_capture` 的 `blank:true` 说明像素方差过低。
- 检查文档是否可见、视图是否被切到空白、模型是否在视口外。
- 可先 `cadw_session({action:"activate"})` 再截图。

## 工程图只有 1 个视图

- 看 `warnings`：逐视图插入失败会退化到 SolidWorks 自动布局。
- 中文 SolidWorks 必须用 `*前视/*上视/*右视/*等轴测`；插件已内置候选。

## DXF 审查 WARN

- `layers_required`：SolidWorks 导出常用中文图层，规则已做别名映射。
- `dimensions`：模型没有标注尺寸时 DXF 尺寸为 0 → WARN 合理。
- 需要更严/更松可传 `rules` 覆盖，例如 `{"dimensions":{"min_count":0}}`。

## 锁一直不释放

- doctor 看 `lock`；持锁进程已死或锁龄 > 900 s 会被自动接管。
- 也可删除 `%LOCALAPPDATA%\\dsh-cad-workbench\\sw.lock`（确认没有建模进程在跑）。

## 安装后工具没出现

- bundle 变更必须**重启 DSH**。
- 确认 profile 的 `package.json` 里有 `dsh-cad-workbench` 依赖与 `dsh.profile.bundles` 条目，
  且 `node_modules/dsh-cad-workbench` 指向本目录。
