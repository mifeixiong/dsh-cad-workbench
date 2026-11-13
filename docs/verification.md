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
