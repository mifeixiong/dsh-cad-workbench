# DSH 上的 CAD / SolidWorks 插件调研（2026-11）

调研对象是 DeepSeek Harness（DSH）生态里与 CAD / 机械建模相关的插件、技能与预设。
结论先行：**没有找到「直接可装、覆盖建模+校核+出图+图纸审查」的一站式插件**；
每条路线各强一块，本插件的目标是把它们最强的部分合成一个 host-only bundle。

## 1. 清单

| 项目 | 形态 | 路线 | 关键能力 |
|---|---|---|---|
| [tian-yu200/dsh-solidworks](https://github.com/tian-yu200/dsh-solidworks) | 预设 + 策略插件 + Python MCP + C# 执行服务 | SolidWorks COM | Feature Graph 编译、job/token、文档身份锁、Undo 事务+回滚验证、expect 实测比对、12 个任务级工具、出图 |
| [fyjgtddco/DSH-SW-and-CAD](https://github.com/fyjgtddco/DSH-SW-and-CAD) | 预设 + 12 门禁脚本 + 14 物理模块 | SolidWorks COM + 自研 FEA | 十阶段设计流水线、问题预算、载荷单一来源、疲劳校核、GB/T 出图、CADX 七项图纸校验、多子代理「房间」 |
| [LU0220-code-pro-max/dsh-solidworks-modeling](https://github.com/LU0220-code-pro-max/dsh-solidworks-modeling) | 技能包 | Python win32com | doctor/run/show/status/open/save/export-pdf、swapi 高层建模、等轴测实时截图、mm 约定 |
| [pan-318/dsh-solidworks](https://github.com/pan-318/dsh-solidworks) | 插件（npm） | SolidWorks COM | 11 工具：建模/装配配合/出图/检查/属性/导出/截图/保存 |
| [redstonedust/dsh-solidworks-copilot](https://github.com/redstonedust/dsh-solidworks-copilot) | 插件 + CLI + 侧栏 UI | 模板/需求书 | 16 模板、7 工具、缺参点名、交付门禁、技能部署器 |
| [Negulons/dsh-freecad](https://github.com/Negulons/dsh-freecad) | 插件（npm） | FreeCAD | STEP 读改、第一角法二维加工图、HLR |
| [zhaizhoux/dsh-cad-freecad](https://github.com/zhaizhoux/dsh-cad-freecad) | 插件 + CLI + Web UI | FreeCAD | 声明式 DSL、不可变修订、精确几何事实、统一错误信封、doctor |
| [LAU-MARS/dsh-cad](https://www.npmjs.com/package/dsh-cad) | 插件 | OCCT/WASM | 原生参数化建模全家桶 + 驻留 3D/2D 查看器 + op-log 重放 |
| [Mrdifferent2022/dsh-cad-design](https://www.npmjs.com/package/dsh-cad-design) | 插件 | CadQuery/ezdxf | 代码式建模、能力路由适配器、DXF 修复经验 |
| [cmm00100/dsh-autocad-bridge](https://github.com/cmm00100/dsh-autocad-bridge) | 插件托管 MCP | AutoCAD | 18 个 cad_* 工具 |
| [a494359163-byte/dsh-multicad](https://github.com/a494359163-byte/dsh-multicad) | MCP | AutoCAD/ZWCAD/GstarCAD/BricsCAD | 多 CAD 适配 |
| [billliao/awesome-dsh-plugin](https://github.com/billliao/awesome-dsh-plugin) · dshfind#cad | 目录 | — | 持续收录，观测到 28+ 个 CAD 插件 |

本机事实：SolidWorks 2024 SP0.1 在运行；Python 3.14 带 pywin32/numpy/matplotlib/Pillow/ezdxf；
FreeCAD、CadQuery、AutoCAD 均未安装。

## 2. 借鉴矩阵

| 来源 | 借鉴 | 落地 |
|---|---|---|
| tian-yu200 | Feature Graph；计划期校验；文档身份锁；Undo 事务+回滚复验；expect 容差 max(1 mm, 2%)；trust_level；二次确认；工具面收敛 | lib/graph.js、scripts/graph.py、scripts/sw_build.py、lib/session.js、lib/plan.js、cadw_job |
| fyjgtddco | IRefPlane 变换查轴向；ActiveDoc 禁令；COM 忙退避；模板探测；CADX 七项校验；GB/T；朝向声明 | sw_common.plane_axes_world、retry_busy、find_template、cad_review.py、rules/dxf_rules.json、技能 |
| LU0220 | dynamic Dispatch；doctor；版本枚举；等轴测截图；mm；massprops 慎用 | sw_common.py、cad_doctor.py、sw_capture.py |
| zhaizhoux | 统一错误信封；不可变修订；精确事实；artifact 引用；DSL 不执行代码 | 全部工具、lib/ledger.js、cadw_export |
| pan-318 | 工具覆盖地图 | 9 工具设计、v0.2 装配路线 |
| redstonedust | 模板即数据、缺参点名、验收清单 | 技能流程 |
| dsh-cad-design | 能力路由、LLM 制图缺陷修复清单 | cad_review.py 的 hint |
| 本机 _cad_smoke | SW 2024 COM 建模→SaveBMP→STEP 的可行性基线 | 实机验收 |

## 3. 刻意不借鉴

- **MCP + C# 执行服务**（tian-yu200）：多一层部署与 AGPL 上游，收益不足以抵消维护成本。
- **门禁状态机 + 多子代理房间**（fyjgtddco）：v0.1 用技能里的流程约束，不做第二套运行时状态机。
- **FreeCAD/CadQuery 后端**：本机未安装，v0.1 只探测并如实报 absent。
- **Web UI 查看器**：客户端 bundle 是另一条构建链路，v0.2 再评估。
- **代码复制**：上游含 AGPL-3.0；本项目按公开 API 与本机实测重写，NOTICE 署名思想来源。

## 4. 结论

组合后的差异化在于：**SolidWorks 为唯一后端 + 实测证据链 + 精确回滚 + 图纸/文件审查**，
而不是「再多一个建模工具」。工具名前缀用 cadw_ 避开 cad_* / solidworks_* 的冲突。
