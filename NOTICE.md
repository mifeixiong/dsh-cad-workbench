# NOTICE · 设计思想来源与许可说明

本插件（dsh-cad-workbench）为**独立实现**：代码按 SolidWorks 公开 API 文档与本机实机行为重写，
**未复制**下列项目的源代码。列出它们是因为设计思想、字段命名或已验证的 API 事实来自公开资料。

## 思想与协议来源

| 项目 | 许可 | 借鉴内容 |
|---|---|---|
| [tian-yu200/dsh-solidworks](https://github.com/tian-yu200/dsh-solidworks) | AGPL-3.0（上游 SolidPilot） | Feature Graph 作为 CAD 中立意图、计划期校验、文档身份锁、Undo 事务与回滚复验、`expect` 实测比对与容差、`finish_job` 的 trust_level、危险动作二次确认、工具面收敛 |
| [fyjgtddco/DSH-SW-and-CAD](https://github.com/fyjgtddco/DSH-SW-and-CAD) | MIT | `IRefPlane::Transform` 查基准面轴向、禁止用 ActiveDoc 当文档身份、COM 忙退避、模板探测、CADX 式图纸校验、GB/T 制图、朝向/原点声明 |
| [LU0220-code-pro-max/dsh-solidworks-modeling](https://github.com/LU0220-code-pro-max/dsh-solidworks-modeling) | 未声明 | win32com 后期绑定、`doctor` 自检、版本自适应枚举、等轴测 + 每步截图、mm 约定、massprops 可能卡死的告警 |
| [zhaizhoux/dsh-cad-freecad](https://github.com/zhaizhoux/dsh-cad-freecad) | 未声明 | 声明式 DSL 不执行用户代码、不可变修订、精确几何事实、`{ok:false,error:{code,message,details}}` 信封 |
| [pan-318/dsh-solidworks](https://github.com/pan-318/dsh-solidworks) | MIT | 工具覆盖地图（建模/装配/出图/检查/导出/截图） |
| [redstonedust/dsh-solidworks-copilot](https://github.com/redstonedust/dsh-solidworks-copilot) | MIT | 模板即数据、缺参点名、交付门禁清单 |
| [Negulons/dsh-freecad](https://github.com/Negulons/dsh-freecad) | MIT | STEP 只读检查与第一角法出图思路 |
| [Mrdifferent2022/dsh-cad-design](https://github.com/Mrdifferent2022/dsh-cad-design) | MIT | 能力路由适配器、LLM 制图缺陷修复清单 |
| [LAU-MARS/dsh-cad](https://github.com/LAU-MARS/dsh-cad) | MIT | 参数化建模与查看器的产品形态参考（本项目未实现 UI） |

## 第三方运行时

- **pywin32**：SolidWorks COM 后期绑定。
- **Pillow**：BMP→PNG、拼图、像素统计。
- **ezdxf**：DXF 审查。
- SolidWorks、SOLIDWORKS 是 Dassault Systèmes SolidWorks Corporation 的商标。
  本项目为社区项目，与 Dassault Systèmes 无隶属或背书关系。

## 许可

本项目以 MIT 许可发布（见 [LICENSE](LICENSE)）。
