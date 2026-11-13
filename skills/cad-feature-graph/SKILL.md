---
name: cad-feature-graph
description: "Feature Graph DSL 参考：节点字段、草图实体、引用规则、单位、校验错误与三个完整示例（板/轴/衬套）。"
---

# Feature Graph DSL（v0.1）

单位：长度 **mm**，角度 **度**；`units` 必须是 `"mm"`。
未知字段、未知 op 一律 `GRAPH_INVALID`（带 `node` / `path` / `hint`），不静默用默认值。

## 节点

| op | 必填 | 可选 | 说明 |
|---|---|---|---|
| `sketch` | `entities` | `plane`（默认 front）、`centerline`、`face`、`id` | `plane` 可为 front/top/right、模板里的基准面名、或 `{face:[x,y,z]}` |
| `extrude` | `depth` | `sketch`（缺省用上一个草图）、`direction: blind\|midplane`、`flip`（反向） | 凸台 |
| `cut` | 盲切需 `depth` | `sketch`、`through_all`、`flip` | 切除（`both_directions` v0.1 不支持） |
| `revolve` | 草图含 centerline | `sketch`、`angle`（默认 360）、`type: boss\|cut` | 旋转 |
| `hole` | `at`、`diameter` | `through_all`（默认 true）、`depth` | 面上打孔（糖：草图+切除） |
| `fillet` | `radius`、`edges` | — | 恒定半径圆角 |
| `chamfer` | `distance`、`edges` | `angle`（默认 45） | 角度-距离倒角 |
| `line` | `from`、`to` | `construction`（默认 false） | 辅助线；构造线不参与轮廓 |
| `sweep` | 轮廓草图闭合 | `profile`、`path`（草图 id/列表）、`merge` | 扫掠凸台（路径可开放折线） |
| `linear_pattern` | `feature`、`direction`、`count`、`spacing` | `count2`、`spacing2`、`flip` | 线性阵列（方向为边上一点） |
| `circular_pattern` | `feature`、`axis`、`count` | `angle`、`equal_spacing`、`flip` | 圆周阵列（轴为圆边/直边上一点） |

`sketch` 可写 `id`；后续节点用 `sketch:"<id>"` 引用。`edges` 可以是 `"all"` 或
`[{at:[x,y,z], tol?}]`（毫米世界坐标，点在边上即可）。

## 草图实体

| type | 字段 |
|---|---|
| `rect` | `center?`、`width`、`height` |
| `circle` | `center?`、`radius` |
| `polygon` | `center?`、`radius`、`sides`(3–64)、`rotation?` |
| `polyline` | `points`(≥3)、`closed`（默认 true，首尾自动闭合） |
| `centerline` | `from`、`to`（旋转轴） |
| `arc` | `center`、`radius`、`start_deg`、`end_deg`（实验性） |

草图至少要有一个闭合轮廓（rect/circle/polygon/closed polyline）。

## 示例 1：带孔圆角板

```json
{
  "units": "mm",
  "nodes": [
    { "op": "sketch", "id": "base", "plane": "front",
      "entities": [{ "type": "rect", "width": 120, "height": 80 }] },
    { "op": "extrude", "sketch": "base", "depth": 10 },
    { "op": "hole", "at": [30, 20, 10], "diameter": 10 },
    { "op": "fillet", "radius": 5,
      "edges": [{ "at": [60, 40, 5] }, { "at": [-60, 40, 5] },
                { "at": [60, -40, 5] }, { "at": [-60, -40, 5] }] }
  ]
}
```

验收：`bbox_mm = [120, 80, 10]`；体积 ≈ 120×80×10 − π×5²×10。

## 示例 2：五段阶梯轴（旋转）

在前视基准面画上半截面折线 + 中心线，再旋转 360°：

```json
{
  "units": "mm",
  "nodes": [
    { "op": "sketch", "plane": "front",
      "entities": [
        { "type": "polyline", "closed": true,
          "points": [[0,0],[40,0],[40,15],[70,15],[70,10],[100,10],[100,0]] }
      ],
      "centerline": [[0,0],[120,0]] },
    { "op": "revolve", "angle": 360 }
  ]
}
```

## 示例 3：衬套（旋转 + 通孔）

```json
{
  "units": "mm",
  "nodes": [
    { "op": "sketch", "plane": "front",
      "entities": [
        { "type": "rect", "center": [20, 15], "width": 40, "height": 30 }
      ] },
    { "op": "revolve", "angle": 360 },
    { "op": "hole", "at": [0, 0, 30], "diameter": 20 }
  ]
}
```

> 旋转的草图轮廓必须贴着中心线一侧（半径方向），不要越过轴线。

## 校验错误

| code | 含义 |
|---|---|
| `GRAPH_INVALID` | 字段/范围/闭环/引用有问题；`details.path` 指向具体字段 |
| `BUILD_FAILED` | SolidWorks 执行失败；`details.cause` 是根因，`details.node_index` 是节点序号 |
| `PLAN_MISMATCH` | 实测与 `expect` 不符（JS 侧判定，容差 max(1 mm, 2%)） |

## 辅助线定位孔（推荐）

`sketch.holes` 可直接用两条 `line` 的交点定位孔，避免手算坐标：

```json
{"op":"sketch","plane":"front","entities":[
  {"type":"rect","width":80,"height":60},
  {"type":"line","from":[-25,-30],"to":[-25,30],"construction":true},
  {"type":"line","from":[25,-30],"to":[25,30],"construction":true},
  {"type":"line","from":[-40,-15],"to":[40,-15],"construction":true},
  {"type":"line","from":[-40,15],"to":[40,15],"construction":true}],
 "holes":[{"diameter":6,"intersection":[1,3]},{"diameter":6,"intersection":[1,4]},
          {"diameter":6,"intersection":[2,3]},{"diameter":6,"intersection":[2,4]}]}
```

构造线也可用 `circle` / `arc` / `polyline` 的 `construction:true`（例如分度圆）；`holes[].at` 仍可直接给坐标。

## 扫掠 / 阵列示例

```json
{"op":"sketch","id":"path","plane":"front","entities":[
  {"type":"polyline","closed":false,"points":[[0,0],[60,0]]}]}
{"op":"sketch","id":"profile","plane":"right","entities":[{"type":"circle","radius":5}]}
{"op":"sweep","profile":"profile","path":"path"}
{"op":"hole","id":"h1","at":[30,20,10],"diameter":10}
{"op":"linear_pattern","feature":"h1","direction":{"at":[0,40,10]},"count":4,"spacing":20}
```

## 不支持（会明确报 UNSUPPORTED / GRAPH_INVALID）

`shell`、`slot`、`rounded_rect`、偏移基准面、扫掠切除、放样、`cut.both_directions`、
装配干涉/运动。不要硬拼；用多个 rect/circle/polyline 表达，或如实说明。