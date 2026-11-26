# 设计

## 分层

	模型
	 │ cadw_*（DSH 工具注册表，返回值统一 toLossless）
	 ▼
	index.js ─ lib/graph.js（形状）─ lib/plan.js（期望比对）─ lib/ledger.js（台账）
	 │           lib/python.js（解释器 + spec 临时文件 + 输出落文件）
	 ▼
	scripts/*.py（独立进程；stdout 只印一个 JSON）
	 ├── SolidWorks COM（sw_common.py）
	 └── ezdxf / 纯 Python（cad_review.py）
	 ▼
	<cad-output>/*.sldprt|.step|.png|.pdf|.dxf …
	<workspace>/.cad-workbench/jobs/<job_id>/{job.json,revision-NNNN.json}

**为什么不常驻进程**：每次工具调用一个 Python 进程，COM 用后期绑定重新附着。
好处是崩溃隔离、无生命周期管理；代价是每次几百毫秒启动。建模本身是秒级，可接受。
跨进程互斥由文件锁保证。

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 执行层 | 纯 Python win32com | 本机已验证；无需 MCP/C#/编译 |
| 建模接口 | 声明式 Feature Graph（mm） | 可计划期校验、可 diff、可复用、不执行用户代码 |
| 校验实现 | 单一 Python 实现（scripts/graph.py） | 避免 JS/Python 两套规则漂移 |
| 期望比对 | JS（lib/plan.js） | 纯函数、易测；结果写回台账 |
| 文档身份 | 路径/标题的 sha256 前 16 位 | 不依赖 COM 指针生命周期；Save As 后令牌会变（显式） |
| 窗口清单 | Frame.ModelWindows + GetDocuments | 区分可见窗口与隐藏/参考文档；用户说"关窗口"通常只指前者 |
| 关闭策略 | 干净 CloseDoc / 脏 QuitDoc | CloseDoc 对脏文档可能弹保存框卡住调用方；QuitDoc 明确不保存关闭 |
| 附着 | GetActiveObject → 进程校验后 Dispatch | SW2024 偶发 ROT 未注册（MK_E_UNAVAILABLE）；先查进程，避免 list/status 拉起 SW |
| 回滚 | 按名删除「当前 - 构建前快照」 | EditUndo2 会撤到用户特征；DeleteSelection2(0) 精确，失败节点残留也能清 |
| 锁 | OS 文件锁（msvcrt/flock） | 进程崩溃由内核释放；无 stale 接管竞态 |
| 图片 | SaveBMP→PNG + 像素统计 | 不依赖窗口截屏；自动识别空白/全黑 |
| 错误 | ok:false + error{code,message,details} | 模型能按 code 决策；细节不丢 |
| 工具面 | 10 个，cadw_ 前缀 | 覆盖全流程，避免与生态冲突 |

## 参考图对位子系统（v0.4）

	data/图片 ─ cadw_ref（标定 + 掩膜量测）─→ .cad-workbench/refkit/<project>.json
	                │                              ▲
	模型 STL/网格/逐件 bbox ─ cadw_grid ─→ out/refkit/<project>/*.png + *_cells.json
	                │                              │
	                └─ cadw_diff ─→ 差分表 md/json ─┴─ cadw_audit ─→ findings（P0/P1/P2）
	                                                    │
	                                          cadw_dispatch ─→ .cad-workbench/dispatch/<batch>/*.md

| 决策 | 选择 | 理由 |
|---|---|---|
| 脚本划分 | `cad_refkit.py`（标定/量测/叠图/差分）+ `cad_audit.py`（纯计算审查）+ `lib/dispatch.js`（文本工单） | 像素活与判定活分开；工单是纯字符串处理，放 JS 省一次进程且好测 |
| 坐标系 | 工程里存 `axes{u,v,w}`，默认 u=x/v=z/w=y | 换产品（机架、建筑）不用改代码 |
| 掩膜 | 声明式规则（color/dark/ink）+ **米制**区域过滤 | 像素过滤换帧就要重调；米制过滤跨帧通用 |
| 轮廓 | 模型侧优先三角网格沿边采样，bbox 只作降级 | bbox 把被布尔切掉的部位按满高算，产生 ±4 m 假偏差 |
| 口径 | 掩膜 ↔ `model_filter` 显式对齐，`compare` 只比可靠的一边 | 否则恒定系统偏差（舷墙被算进船体上缘） |
| 埋入判据 | 缩进包含（inset contain），不是 bbox 相交 | 贴面件（舷窗）与壳体 bbox 必然相交，相交判据会全量误报 |
| 审查阈值 | 数据驱动 `rules/audit_rules.json` | 不同产品甲板标高/悬臂件不同，不要把阈值焊进代码 |
| 工单 | 九段固定结构，写作用域与模型策略写成硬规则 | 工单缺写作用域就会两个 agent 互踩；缺模型校验就会整批失败 |
| 递归 | 子区默认 `write_scope: []`（只读出补丁） | 同一模块文件不能并发写；子代理只分析，写由本级执行 |

## 信任等级

- verified：有 expect 且逐轴/体积/特征数全部通过，产物齐全；
- measured-only：有实测但没有批准方案可比（或未给 expect）；
- unverified：没有任何实测证据。

cadw_job.finish 只在这三种状态上给 delivered/partial/failed；partial 必须列阻塞项。

## 失败处理

| 阶段 | 失败 | 行为 |
|---|---|---|
| 图校验 | 字段/引用/闭环 | GRAPH_INVALID，不碰 SolidWorks |
| 执行 | 选面/选边/API 返回 None | BUILD_FAILED + 节点序号 + 根因；按名回滚已建特征 |
| 回滚 | 删除失败 | ROLLBACK_FAILED 标记，保留现场，不静默关档 |
| 实测 | 与 expect 不符 | PLAN_MISMATCH，列出逐轴差值 |
| 导出 | 缺转换器/授权 | UNSUPPORTED，逐格式报告，不伪造文件 |
| 并发 | 锁被占 | DOC_BUSY，带持锁 pid/工具名 |
| 装配 | 组件缺基准面/配合过定义 | UNSUPPORTED/BUILD_FAILED + AddMate5 error_status，不静默 |

## 目录

	index.js                 工具注册、技能 provider、错误收口
	lib/tooling.js           无损 JSON、失败信封、脚本结果合并
	lib/images.js            附件引用与内容块
	lib/python.js            解释器解析、脚本调用（文件通道）
	lib/session.js           工作区、路径守卫、状态目录
	lib/graph.js             图形状检查、规范哈希
	lib/plan.js              expect vs measured
	lib/ledger.js            台账与不可变修订
	scripts/sw_common.py     COM/模板/身份/锁/草图/特征/保存/图像
	scripts/graph.py         唯一图校验器
	scripts/sw_session.py    新建/打开/保存/激活 + 已打开窗口清单与关闭
	scripts/sw_build.py      执行 + 回滚 + 实测
	scripts/sw_measure.py    几何事实
	scripts/sw_capture.py    视图 + 拼图 + 像素统计
	scripts/sw_export.py     多格式导出
	scripts/sw_drawing.py    工程图
	scripts/sw_assembly.py   装配体（插入/配合/固定/列表）
	scripts/cad_review.py    DXF/STL/STEP 审查
	scripts/cad_doctor.py    环境能力
	scripts/cad_refkit.py    参考图：标定/掩膜量测/网格叠图/差分
	scripts/cad_audit.py     对抗审查九角度（纯计算）
	lib/dispatch.js          分区工单（九段 + 递归 + 模型策略）
	rules/dxf_rules.json     可覆盖的图纸规则
	skills/                  4 个随包技能

## Roadmap

- v0.2 ✅：sweep / linear_pattern / circular_pattern；cadw_assembly（插入 + 平面配合 + 固定）。
- v0.3.0 ✅：辅助线/交点孔、圆柱面同心配合、已打开窗口管理（list 清单、close 批量、close_others、close_all；脏文档/隐藏文档保护）。
- v0.4：装配干涉检查与运动；shell 节点；偏移基准面；slot/rounded_rect；扫掠切除。
- v0.5：可选 FreeCAD/CadQuery 后端（能力路由）；CAD 模式预设；Web 端只读查看器。
- v0.4 ✅：参考图对位（`cadw_ref`/`cadw_grid`/`cadw_diff`）、对抗审查（`cadw_audit`）、分区派工（`cadw_dispatch`）、
  两个新技能；差分表与审查发现可直接喂给派工单。