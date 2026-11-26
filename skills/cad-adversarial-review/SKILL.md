---
name: cad-adversarial-review
description: "建模对抗审查：从悬空/埋入/穿插/重复/欠定义/过定义/形状/体量/命名/基准九个角度机械地找模型错误，给 P0/P1/P2 与可执行的改法，P0 清零前不许谈交付。"
---

# 对抗审查（cadw_audit）

> 九个角度：datum / float / buried / clash / dup / sketch / shape / mass / naming。

> 定位：你是审查者，不是作者的复读机。**只吃事实**（bbox、草图约束状态、三角网格、差分表），
> 每条结论必须能落到"哪个零件、哪个量、差多少、改哪里"。
> 反面教材：v3 模型舷窗三排 y 写死，第一二排悬空 0.4/0.6 m、红壳那一排整排埋进船体，
> 但四视图看起来"没问题"——没有机械审查，这种错只能等人看出来。

## 0. 什么时候跑

| 时机 | 目的 |
|---|---|
| 建模完成、截图之前 | 结构自检：悬空/埋入/过定义/破面 |
| 参考图差分出现大片 FAIL 时 | 判断是形状错还是口径错 |
| 装配体加完零件后 | 穿插、重复、支撑关系 |
| 交付前 | P0 必须为 0；否则只能报 partial |

## 1. 九个角度与判据

| 角度 | 抓什么 | 默认判据 | 严重度 |
|---|---|---|---|
| `datum` | 单位/原点/总包络/实体数；STL 与模型是否一致 | 超 `model_bbox`（容差 `bbox_slack` 0.05）、`solid_count==0` | P0/P1 |
| `float` | 悬空：零件底面不落在任何支撑面 | 与支撑标高（`support_planes` ∪ 其它零件顶面）差 > `gap_tol` 0.10，且不在 `cantilever` 白名单 | P0 |
| `buried` | 埋入：零件大部分体积在别的体里 | 体积 >80% 落在体积大 10 倍以上的零件内 | P0 |
| `clash` | 穿插：一件几乎整件插进另一件 | 交集体积 > `clash_vol` 0.5 m³ **且**占小件体积 > `clash_frac` 0.8，且不在 `clash_allow` | P1 |
| `dup` | 重复 | 同名 → P1；bbox IoU > `dup_iou` 0.9 只给 **P2 提示**（交叉撑/对角杆共包络，IoU 也是 1.00，不能直接判重复） | P1/P2 |
| `sketch` | 草图层：过定义/欠定义 | `over_defined` 非 0 → P0；`fully_defined=false` → P1 | P0/P1 |
| `shape` | 形状：与参考/目标带不符 | diff FAIL 行；轮廓 \|Δ\| > `profile_tol` 1.0 | P1 |
| `mass` | 体量：空壳/破面/布尔失败 | 体积/包围盒 < `fill_min` 0.02 → P0；> `fill_max` 0.999 → P2（近乎满包络，怀疑切除没生效） | P0/P2 |
| `naming` | 命名：重名/大写/中文/空格 | 违反"英文小写短语、全局唯一" | P2 |

阈值全部在 `rules/audit_rules.json`，可在调用时用 `rules` 参数覆盖（只覆盖要改的字段）。

## 2. 事实从哪来

```json
cadw_audit(
  parts: [{"name":"funnel casing","bbox":[38.2,-4.3,13.75,47.0,4.3,24.2]}],
  facts: <cadw_measure(scope:"all") 的返回值>,
  diff:  <cadw_diff 的返回值>,
  stl:   "cad-out/funnel.stl",
  support_planes: [0, 9.30, 13.75, 19.15, 22.95],
  grid: {"u_step":5,"v_step":5})
```

- `parts` 可以是数组、报告 JSON 路径、或 `{name: bbox}` 映射（单位 m；`units:"mm"` 自动换算）。
- `facts` 直接用 `cadw_measure` 的输出：`bbox_mm/volume_mm3/solid_count/sketches[]/features[]`。
- `diff` 可以直接把 `cadw_diff` 的返回值塞进来，审查会把 FAIL 行汇总成 `shape` 发现。

## 3. 读报告的顺序

1. **P0 全列一遍**，按"会不会让后续每一轮都错"排序：
   - 过定义/欠定义 → 参数改了不更新、云图会漂
   - 悬空/埋入 → 装配与干涉分析全废
   - 空壳/破面 → 导出 STEP/STL 到了下游才发现
2. **P1**：穿插、重复、形状偏差。
3. **P2**：命名、体量可疑。

**P0 未清零前，禁止进入"调形状/调涂装"阶段**。理由很实际：悬空 0.4 m 的零件，你在侧视图上改它的形状，
改的其实是"离甲板多远"，越改越乱。

## 4. 每条发现怎么用

发现对象里有：`angle / severity / subject / cell / expect / actual / delta / evidence / hint`。

- `hint` 直接写"改哪个文件的哪个量"。照它改，不要重新发明。
- `cell` 是格号，用来派工：区内的 P0 归该区 agent（见 `cad-reference-match` §6）。
- 同角度同一原因的一批项会汇总成一条（例：40 个舷窗全部悬空），先改**排布常量**而不是逐个改零件。

## 5. 修完怎么证明修好了

1. 重跑 `cadw_audit`，同一角度的条数必须降到 0（不是"少了"）。
2. 重跑 `cadw_diff`，确认没有因为这次修改引入新的 FAIL。
3. 把两次审查的 id 列表做个差集，写进交付说明（"修掉 A2-001…A2-013，新增 0"）。

## 6. 规则怎么扩（含船型/建筑型调参）

```json
// 机械装配（默认）：clash_frac 0.35，交叠占小件 35% 就当穿插
// 船型/建筑型：大体量件互相"穿过"是建模方式，要放宽 + 拉贴面名单
{"tolerances": {"clash_frac": 0.8},
 "surface_parts": ["glazing","window","porthole","rail","panel","rib","stiffener","plate","door","cap"]}
```

破冰船 460 件实测：默认参数出 41 条 clash P1（横贯全宽的栏板/玻璃带与甲板室 bbox 交叠），
按上面调参后降到 4 条 —— 且这 4 条都是真问题（舷墙∩锚机电机 0.62 m³、舷墙∩救生艇艇体 8.73 m³、
船体∩舷梯纵梁 2.92 m³×2）。**调参不是为了让表好看，是为了让剩下的每条都值得看。**

- 新增白名单（某些零件本来就悬臂）：`rules.cantilever` 加关键词；
- 允许的穿插对：`rules.clash_allow` 加 `["strut","hull"]` 这种名字对；
- 关掉某角度：`rules.angles.float=false`（只在"这一批确实不适用"时用，并在交付里写明）；
- 支撑标高随项目变：`support_planes` 换成该产品的真实甲板标高。

## 7. 常见误判

| 现象 | 不是缺陷的情况 | 处理 |
|---|---|---|
| 桅杆/天线/吊臂"悬空" | 本来就是悬臂件 | 必须命中 `cantilever` 关键词 |
| strut 穿船壳 | 本来就要穿 | 进 `clash_allow` |
| 舷窗"埋入" | 贴在曲面上，bbox 相交是正常的 | 判据应是"外表面偏差"，用 `cadw_diff` 的贴面项，不要用 bbox 埋入 |
| 甲板设备"埋入" | 船体/线型件的 bbox 是覆盖半条船的大空盒，甲板上的一切都在它里面 | `buried_host_allow` 只放**盒状大件**（机库/集装箱/棚/舱）；船体不当宿主 |
| 体积/包络低 | 桁架、栏杆、薄壳 | 关掉 `mass` 或按零件名分组判 |
| 镜像件"重复" | 左右对称 | 名字带 `port/starboard` 的不报 `dup` |