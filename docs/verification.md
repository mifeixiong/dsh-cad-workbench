# 实机验收记录

环境：Windows 11 / SolidWorks 2024 SP0.1 / Python 3.14.6（pywin32、Pillow、numpy、ezdxf）。

命令：

	python test/live_smoke.py --workspace E:\code\_cad_verify\live

结果：**31/31 PASS**（2026-11-13）。

## 关键证据

| 项 | 实测 |
|---|---|
| 板包围盒 | 120.0 × 80.0 × 10.0 mm（逐轴精确） |
| Ø10 通孔后体积 | 95214.6 mm³（理论 96000 − 785.4，误差 < 0.01%） |
| 四角 R5 圆角后体积 | 95000.0 mm³（理论 95214.6 − 4×53.65，逐项吻合） |
| 四视图 | iso/front/top/right，stddev 15–80，unique_colors 452–1076，无空白 |
| STEP/STL/PDF | 26168 / 12284 / 244306 字节，sha256 已记录 |
| 工程图 | A3 四视图，PDF/DXF/DWG 均非空 |
| DXF 审查 | WARN（缺标注尺寸）；图层别名解析通过；零长线/重复线通过 |
| STEP 审查 | PASS；单位 mm；启发式包围盒 120×80×10 |
| STL 审查 | PASS；244 三角面；退化 0；水密启发式通过 |
| 回滚 | 失败图删除 凸台-拉伸2 / 草图3，特征数 23 → 23 |
| 过期令牌 | DOC_NOT_FOUND |
| 锁竞争 | DOC_BUSY，等待 2.1 s |

产物目录：`E:\code\_cad_verify\live\{captures,exports,drawings}`。

## 复跑

1. 启动 SolidWorks 到主界面。
2. `python test/run_all.py`（离线，应 3 项全绿）。
3. `python test/live_smoke.py`（实机）。
4. 安装副本：`node test/verify_installed.mjs`。

## 已知与实测差异

- `Save3(1,1,0)` 在动态分派下报"类型不匹配"，改用 `Save()`/`SaveAs3`。
- `CreateDrawViewFromModelView3` 英文视图名静默失败，必须用中文命名视图。
- `EditUndo2` 前几次为空操作，且可能撤到用户特征；改用按名删除回滚。
- `EditDelete` 不生效，`Extension.DeleteSelection2(0)` 有效。
- `os.kill(pid,0)` 在 Windows 不可靠，改用 OpenProcess 判存活。
## 审计回归（2026-11-13，v0.1.1）

三个子代理并行审计后，按报告修复并回归：

| 项 | 结果 |
|---|---|
| Top 平面拉伸件 y=10 面 Ø10 孔 @ (20,10,-15) | 孔心质心 x=-0.1650 / z=+0.1237（解析值 -0.16496 / +0.12372） |
| 倒角 45° / 距离 5 / 一条竖边 | 去料 125.000 mm³（解析 125） |
| 闭合 polyline 拉伸 | 体积 200.000 mm³，无零长段 |
| hole 选面失败回滚 | features 20 → 20，rollback.done=true |
| open 已保存 sldprt + save | 返回 doc_id，保存成功 |
| 双进程抢锁 | 30 轮无重叠（OS 文件锁），等待方 DOC_BUSY |
| 导出目标预存在 + SaveAs 无操作 | 抛 UNSUPPORTED，旧文件内容/mtime 恢复，无 .bak 残留 |
| DXF MTEXT char_height=1.0 | text_height WARN（不再假阴性） |
| 草图审计 | 段数 4 / 点数 4 / constrained_status=2（under_defined） |

命令：

	python test/run_all.py
	python test/live_regressions.py --workspace E:\code\_cad_verify\live_reg3
	python test/live_smoke.py --workspace E:\code\_cad_verify\live2

审计报告（仓库外）：`E:\code\_cad_verify\reviews\audit-static.md`、`audit-live.md`、`efficiency.md`。
## 高级特征与装配（v0.2.0）

| 项 | 实测 |
|---|---|
| 扫掠 Ø10 × 60 路径 | 体积 4712.39 mm³（π·5²·60 = 4712.39） |
| 线性阵列 4 孔 / 20 mm | 体积 92858.41 mm³（96000 − 4×785.40） |
| 圆周阵列 4 份 / 360° | 体积 92073.01 mm³（96000 − 5×785.40） |
| 装配：40×40 板 + Ø10 销 | 2 组件，重合配合「重合1」，mate_count=1 |
| 装配固定/浮动 | 通过；保存/重开可读 |

命令：`python test/live_advanced.py --workspace E:\code\_cad_verify\live_adv` → **11/11 PASS**。
