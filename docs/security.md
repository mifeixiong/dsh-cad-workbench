# 安全与纪律

> 运行平台：仅 Windows（依赖 SolidWorks COM）。CI 也只在 Windows 上跑离线单测，不做跨平台承诺。

## 文档身份

- 每个文档返回 `doc_id = sha256(realpath 或 "unsaved:"+title)[:16]`。
- 所有写操作必须带 `doc_id`；执行前重新枚举已打开文档复核。
- **绝不使用 ActiveDoc 作为操作对象**：它只是"此刻前台是谁"。
- Save As 会改变路径 → `doc_id` 变化；工具会返回新令牌，调用方必须更新。
- 找不到令牌返回 `DOC_NOT_FOUND`；执行中途身份变化返回 `DOC_IDENTITY_MISMATCH`。

## 并发

- 锁文件 `%LOCALAPPDATA%\\dsh-cad-workbench\\sw.lock` 只承载元信息；真正的互斥是 **OS 文件锁**
  （Windows `msvcrt.locking` / POSIX `flock`），等待默认 120 s，超时 `DOC_BUSY`。
- 进程崩溃由内核自动释放锁：没有 pid/TTL 启发式，也没有 check-then-delete 竞态。
- 多会话/子代理同时建模时串行执行，不会把特征画到对方文档。

## 回滚

- 建模前记录完整特征名快照；失败时删除「当前 - 快照」的全部特征（含失败节点残留的草图）。
- 按名删除（`SelectByID2 → DeleteSelection2(0)`），再 `ForceRebuild3` 复验特征数。
- 只删本次新增，不碰用户原有特征；删除失败才退到 `EditUndo2`，且有界。
- 回滚失败返回 `ROLLBACK_FAILED` 并保留现场，不静默关闭文档。

## 危险动作

需要 `confirm:true`：

- `save_as` 覆盖已存在的 `.sldprt/.sldasm/.slddrw`；
- `close` 一个有未保存修改的文档；
- `close_all`；
- `cadw_export` / `cadw_drawing` 覆盖已存在产物。

## 路径

- 默认只写会话工作区；`allow_outside:true` 才允许越界。
- 拒绝 `C:\\Windows`、`C:\\Program Files*`、`C:\\ProgramData`。
- 导出/保存先写到目标目录（不存在则创建），写完检查存在与非空。
- 读操作（`cadw_review`）可读工作区外文件。

## 数据

- 无网络请求、无遥测、无上传。
- Feature Graph 是**数据**：不执行用户代码、不做表达式求值。
- 台账只写路径、哈希、数值与时间；不写凭据。

## 能力缺失

缺 pywin32 / Pillow / ezdxf / 模板 / 转换器时，逐项报 `UNSUPPORTED` 或 `COM_UNAVAILABLE`，
不静默降级、不伪造文件、不把"看起来成功"当成功。
