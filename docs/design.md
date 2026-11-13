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
| 回滚 | 按名删除本次新增特征 | 实测 EditUndo2 前几次空操作且会撤到用户特征；DeleteSelection2(0) 精确 |
| 锁 | 文件 O_EXCL + pid 存活 + 900 s 失效 | 多会话/子代理串行；Windows 用 OpenProcess 判存活 |
| 图片 | SaveBMP→PNG + 像素统计 | 不依赖窗口截屏；自动识别空白/全黑 |
| 错误 | ok:false + error{code,message,details} | 模型能按 code 决策；细节不丢 |
| 工具面 | 9 个，cadw_ 前缀 | 覆盖全流程，避免与生态冲突 |

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
	scripts/sw_build.py      执行 + 回滚 + 实测
	scripts/sw_measure.py    几何事实
	scripts/sw_capture.py    视图 + 拼图 + 像素统计
	scripts/sw_export.py     多格式导出
	scripts/sw_drawing.py    工程图
	scripts/cad_review.py    DXF/STL/STEP 审查
	scripts/cad_doctor.py    环境能力
	rules/dxf_rules.json     可覆盖的图纸规则
	skills/                  4 个随包技能

## Roadmap

- v0.2：装配体插入 + 配合 + 干涉检查；shell/pattern 节点；偏移基准面；slot/rounded_rect。
- v0.3：可选 FreeCAD/CadQuery 后端（能力路由）；PDF 图纸视觉复核。
- v0.4：CAD 模式预设（限制工具面）；Web 端只读查看器。
