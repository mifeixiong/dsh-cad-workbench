---
name: solidworks-com
description: "SolidWorks COM 自动化事实与坑：后期绑定、模板探测、中英文基准面/视图名、Save As 选项、Undo/删除、单位换算、文档身份。"
---

# SolidWorks COM 事实与坑

> 这些是本机 SW 2024 + pywin32 实测结论。插件脚本已经封装，正常建模不需要你写 COM；
> 但排障、扩展脚本、理解错误码时需要这一页。

## 连接

- 用 `win32com.client.dynamic.Dispatch('SldWorks.Application')`（后期绑定），
  不用 `gencache.EnsureDispatch`：类型库注册不完整时前者仍可用。
- 只附着不拉起：`win32com.client.GetActiveObject('SldWorks.Application')`。
- 无参 COM 成员在动态分派下按**属性**访问：`sw.RevisionNumber`、`model.GetPathName`；
  需要参数的方法才加括号。
- COM 忙（`RPC_E_CALL_REJECTED` 等）要原地退避重试，**不要重跑整个脚本**。
- 一次只允许一个会话写 SolidWorks；插件用 `%LOCALAPPDATA%\\dsh-cad-workbench\\sw.lock` 串行化。

## 单位

- SolidWorks API 长度单位是**米**；插件对外一律 **mm**，在 `sw_common` 边界处 ×0.001。
- 角度 API 用弧度（旋转特征），插件对外用度。

## 文档身份

- **不要用 `sw.ActiveDoc` 判断"我要操作谁"**。用 `doc_id` 令牌：路径（未保存时用标题）的 sha256 前 16 位。
- 每次写前复核 `GetPathName` / `GetTitle`；不符就拒绝。
- **Save As 后 doc_id 会变**：令牌按路径算，保存成功要重新取令牌。

## 模板

- 优先 API 用户首选项（零件 101 / 装配 102 / 工程图 103），再扫
  `C:\\ProgramData\\SolidWorks\\SOLIDWORKS *\\templates`，最后扫其他盘。
- 中文模板名：`gb_part.prtdot`、`gb_assembly.asmdot`、`gb_a3.drwdot`。
- 可用 `CADW_PART_TEMPLATE` / `CADW_ASM_TEMPLATE` / `CADW_DRW_TEMPLATE` 覆盖。

## 基准面与草图

- 中文模板的基准面名是 `前视基准面/上视基准面/右视基准面`；英文是 `Front Plane/...`。
  插件中英都试；你自己写脚本也要两边都试。
- 选面：`Extension.SelectByID2("", "FACE", x, y, z, append, 0, VARIANT(None), 0)`
  （坐标单位米）。
- **开始草图**：先选中基准面/面，再 `model.SketchManager.InsertSketch(True)`。
- **草图实体创建走 `model.SketchManager`**（ISketchManager），不是 `ActiveSketch`；
  `ActiveSketch` 只用来读 `ModelToSketchTransform`。
- 结束草图：再 `InsertSketch(True)`；结束前可 `ActiveSketch.MergePoints(0.0005)` 合并缝隙。
- 在世界坐标给孔位时，用 `ActiveSketch.ModelToSketchTransform.ArrayData` 求逆变换，
  否则 Top 面/自定义面会镜像（外形尺寸对、孔跑到反面，SW 不报错）。

## 特征

- 拉伸：`FeatureExtrusion3(...)`；两侧对称枚举 2020+ 为 6、2018 为 5。
- 切除：`FeatureCut3(...)`；完全贯穿枚举为 1；方向反了用 flip。
- 旋转：`FeatureRevolve2(...)`，草图中必须有中心线。
- 圆角：`FeatureFillet3(2, r, 0, 0, 0, 0, 0, None×7)`（Options=2 恒定半径）。
- 倒角：优先 `InsertFeatureChamfer(0, 1, dist, angle, 0,0,0,0)`，失败回退 `FeatureChamferType`。
  **angle 参数是弧度**：45° 要传 `math.radians(45)`；传 45 会变成 45 rad（实测去料翻倍）。
- 失败时先看返回值是不是 None；不要假设"没抛异常就成功"。

## 特征树

- 用 `model.FeatureManager.GetFeatures(True)` 一次拿全部特征；
  链式 `FirstFeature → GetNextFeature` 也可用，但动态分派对返回值不要再 callable() 调用。
- 草图特征类型是 `ProfileFeature`（中文名 `草图N`）。
- 特征数里包含默认基准面/文件夹，所以比对用**前后差值**，不要用绝对值。

## 保存 / 导出

- `SaveAs3(path, 0, options)`：options=1 是静默另存并切换文档路径；options=2 是**保存副本**，
  文档路径不变（容易误判"保存成功但身份没变"）。
- 无参保存用 `model.Save`；`Save3(1,1,0)` 在动态分派下可能"类型不匹配"。
- `OpenDoc6(path, type, options, Configuration, Errors, Warnings)` 的 Configuration 不能省（传 `''`）。
- 面草图世界坐标换算：`ModelToSketchTransform` 是 model→sketch，ArrayData 0..8 按**列主序**：
  `s = R·p + t`；用逆变换/转置会把非前视平行面的孔镜像（实测 Top 面偏 20mm）。
- 导出 STEP/IGES/STL/PDF 也是 `SaveAs3`，由扩展名决定格式；写完必须检查文件存在且非空。
- 导出 DXF/DWG 需要工程图文档 + 转换器授权；失败要报 `UNSUPPORTED`，不要伪造文件。

## 视图 / 截图 / 工程图

- 截图：`model.SaveBMP(path, width, height)`；没有它再退回窗口截图。
- 命名视图：英文 `*Front/*Top/*Right/*Isometric`；**中文 SolidWorks 上要用**
  `*前视/*上视/*右视/*等轴测`，否则 `CreateDrawViewFromModelView3` 静默失败。
- `CreateDrawViewFromModelView3` 动态分派下常返回 None，但创建成功；
  以"视图数是否增加"判定，别拿返回值当失败。
- 未保存的新图纸用 `GetFirstView` 遍历不到视图；先保存、再按路径取新指针。
- `InsertModelAnnotations3` 返回 None；用 `view.GetAnnotationCount` 数注解。

## 回滚

- `EditUndo2(1)` 有时前几次是空操作；不要无界循环，容易把用户之前的特征也撤掉。
- 回滚用「构建前特征快照」，删除当前比快照多出来的全部特征（失败节点的残留草图也要删）。
- 精确做法：记录本次新增的特征名 → 逆序 `SelectByID2(name, "BODYFEATURE") → Extension.DeleteSelection2(0)`；
  这次实测有效。`EditDelete` 在这台机器上不生效。
- 回滚后必须 `ForceRebuild3(False)` 并复验特征数。

## 质量属性

- `model.GetMassProperties`（动态下是属性）返回
  `[cogX, cogY, cogZ, volume, surface_area, mass, ...]`（米制）。
- 个别机器上会卡住；插件默认不调用，doctor 只做能力探测。
