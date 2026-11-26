# -*- coding: utf-8 -*-
"""cad_audit.py —— 建模对抗审查：九个角度机械地找错误，给 P0/P1/P2 与可执行的改法。

定位：审查者不是作者的复读机。只吃事实（逐件 bbox、草图约束状态、三角网格、差分表），
每条发现必须能落到"哪个零件、哪个量、差多少、改哪里"。

角度（可用 rules.angles 单独关闭）：
  A1 datum  单位/原点/总包络/实体数/STL 与模型一致性
  A2 float  悬空：零件底面不落在任何支撑标高或其它零件顶面
  A3 buried 埋入：零件大部分体积落在体积远大于它的零件内部
  A4 clash  穿插：两零件 bbox 交集体积过大
  A5 dup    重复：同名零件，或 bbox IoU 过高
  A6 sketch 草图层：过定义（P0）/ 欠定义（P1）
  A7 shape  形状：差分表 FAIL 行 / 轮廓超差
  A8 mass   体量：空壳/破面（体积/包络过低）
  A9 naming 命名：违反"英文小写短语、全局唯一"

退出码：0 成功（无论 verdict）；2 BAD_ARGS；1 其它。
"""
import json
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(PLUGIN_ROOT, 'rules', 'audit_rules.json')
ANGLES = [
    ('datum', 'A1'), ('float', 'A2'), ('buried', 'A3'), ('clash', 'A4'), ('dup', 'A5'),
    ('sketch', 'A6'), ('shape', 'A7'), ('mass', 'A8'), ('naming', 'A9'),
]
ANGLE_CODES = dict(ANGLES)
SEVERITY_ORDER = {'P0': 0, 'P1': 1, 'P2': 2}
COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


# --------------------------------------------------------------------------
# 规则与输入
# --------------------------------------------------------------------------
def load_rules(overrides):
    rules = {}
    try:
        with open(DEFAULT_RULES, 'r', encoding='utf-8') as handle:
            rules = json.load(handle)
    except Exception:
        rules = {}
    rules.setdefault('tolerances', {})
    rules.setdefault('support_planes', [])
    rules.setdefault('cantilever', [])
    rules.setdefault('buried_allow', [])
    rules.setdefault('clash_allow', [])
    rules.setdefault('angles', {})
    if overrides is not None and not isinstance(overrides, dict):
        raise common.CadError('BAD_ARGS', 'rules 必须是对象（只给要覆盖的字段）')
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(rules.get(key), dict):
            merged = dict(rules[key])
            merged.update(value)
            rules[key] = merged
        else:
            rules[key] = value
    return rules


def tolerance(rules, name, default):
    try:
        return float((rules.get('tolerances') or {}).get(name, default))
    except Exception:
        return float(default)


def angle_on(rules, name):
    angles = rules.get('angles') or {}
    return bool(angles.get(name, True))


def normalize_parts(spec, workspace):
    parts = spec.get('parts')
    if isinstance(parts, str):
        path = parts if os.path.isabs(parts) else os.path.abspath(os.path.join(workspace, parts))
        with open(path, 'r', encoding='utf-8') as handle:
            parts = json.load(handle)
    if parts is None and spec.get('report'):
        raw = str(spec['report'])
        path = raw if os.path.isabs(raw) else os.path.abspath(os.path.join(workspace, raw))
        with open(path, 'r', encoding='utf-8') as handle:
            parts = json.load(handle)
    if isinstance(parts, dict):
        if isinstance(parts.get('bbox'), dict):
            parts = [{'name': name, 'bbox': box} for name, box in parts['bbox'].items()]
        elif isinstance(parts.get('parts'), list):
            parts = parts['parts']
        else:
            parts = [{'name': str(name), 'bbox': box} for name, box in parts.items()
                     if isinstance(box, (list, tuple)) and len(box) == 6]
    if not isinstance(parts, list):
        return []
    factor = 0.001 if str(spec.get('units') or 'm').lower() == 'mm' else 1.0
    cleaned = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        box = item.get('bbox')
        if not isinstance(box, (list, tuple)) or len(box) != 6:
            continue
        try:
            values = [float(value) * factor for value in box]
        except Exception:
            continue
        cleaned.append({'name': str(item.get('name') or 'part'), 'bbox': values})
    return cleaned


def load_diff(spec, workspace):
    diff = spec.get('diff')
    if isinstance(diff, str):
        path = diff if os.path.isabs(diff) else os.path.abspath(os.path.join(workspace, diff))
        with open(path, 'r', encoding='utf-8') as handle:
            diff = json.load(handle)
    if isinstance(diff, dict):
        return diff
    return {}


def load_facts(spec):
    facts = spec.get('facts')
    if isinstance(facts, str):
        try:
            return json.loads(facts)
        except Exception:
            return {}
    return facts if isinstance(facts, dict) else {}


def stl_summary(path):
    """二进制/ASCII STL 的三角数与包围盒（只用标准库）。"""
    if not path or not os.path.isfile(path):
        return None
    with open(path, 'rb') as handle:
        raw = handle.read()
    count = None
    corners = []
    if len(raw) >= 84:
        triangles = struct.unpack('<I', raw[80:84])[0]
        if len(raw) == 84 + triangles * 50:
            count = triangles
            view = memoryview(raw[84:])
            for index in range(triangles):
                offset = index * 50 + 12
                for vertex in range(3):
                    base = offset + vertex * 12
                    corners.append(struct.unpack('<fff', view[base:base + 12]))
    if count is None:
        text = raw.decode('utf-8', 'ignore')
        found = re.findall(r'vertex[ \t]+([-0-9.eE+]+)[ \t]+([-0-9.eE+]+)[ \t]+([-0-9.eE+]+)', text)
        if not found:
            return None
        count = len(found) // 3
        corners = [(float(a), float(b), float(c)) for a, b, c in found]
    if not corners:
        return {'triangles': count, 'bbox': None}
    xs = [item[0] for item in corners]
    ys = [item[1] for item in corners]
    zs = [item[2] for item in corners]
    return {'triangles': count, 'bbox': [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]}


# --------------------------------------------------------------------------
# 几何小工具
# --------------------------------------------------------------------------
def volume(box):
    return max(0.0, box[3] - box[0]) * max(0.0, box[4] - box[1]) * max(0.0, box[5] - box[2])


def overlap(a, b):
    box = [max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]),
           min(a[3], b[3]), min(a[4], b[4]), min(a[5], b[5])]
    if box[3] <= box[0] or box[4] <= box[1] or box[5] <= box[2]:
        return [0.0] * 6
    return box


def overlap_volume(a, b):
    return volume(overlap(a, b))


def footprint_overlap(a, b):
    return (min(a[3], b[3]) > max(a[0], b[0])) and (min(a[4], b[4]) > max(a[1], b[1]))


def union_bbox(boxes):
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes), min(box[2] for box in boxes),
            max(box[3] for box in boxes), max(box[4] for box in boxes), max(box[5] for box in boxes)]


def center(box):
    return ((box[0] + box[3]) / 2.0, (box[1] + box[4]) / 2.0, (box[2] + box[5]) / 2.0)


def cell_of(box, grid):
    if not isinstance(grid, dict):
        return None
    u_step = float(grid.get('u_step') or 5.0)
    v_step = float(grid.get('v_step') or 5.0)
    u_origin = float(grid.get('u_origin') or 0.0)
    v_origin = float(grid.get('v_origin') or 0.0)
    u_value, _y, v_value = center(box)
    column = int(math.floor((u_value - u_origin) / u_step))
    row = int(math.floor((v_value - v_origin) / v_step))
    letter = COL_LETTERS[column] if 0 <= column < len(COL_LETTERS) else 'C%d' % column
    return '%s%d' % (letter, row + 1)


def token_hit(name, keywords):
    """按词元匹配（避免 'port' 命中 'support' 这类子串误伤）。"""
    tokens = [item for item in re.split(r'[ _.-]+', (name or '').lower()) if item]
    lowered = (name or '').lower()
    for keyword in keywords or []:
        text = str(keyword).lower().strip()
        if not text:
            continue
        if ' ' in text:
            if text in lowered:
                return True
        elif text in tokens:
            return True
    return False


def keyword_hit(name, keywords):
    lower = (name or '').lower()
    return any(str(key).lower() in lower for key in keywords or [])


def pairs(names_a, names_b):
    for left in names_a:
        for right in names_b:
            yield left, right


def pair_allowed(name_a, name_b, rules):
    a = (name_a or '').lower()
    b = (name_b or '').lower()
    for first, second in rules.get('clash_allow') or []:
        first = str(first).lower()
        second = str(second).lower()
        if (first in a and second in b) or (second in a and first in b):
            return True
    return False


# --------------------------------------------------------------------------
# 审查
# --------------------------------------------------------------------------
class Collector(object):
    def __init__(self, rules, grid):
        self.rules = rules
        self.grid = grid
        self.findings = []
        self.counters = {}

    def add(self, angle, severity, subject, expect='', actual='', delta=None, evidence=None, hint=''):
        code = ANGLE_CODES.get(angle, 'A0')
        index = self.counters.get(code, 0) + 1
        self.counters[code] = index
        finding = {
            'id': '%s-%03d' % (code, index),
            'angle': angle,
            'severity': severity,
            'subject': str(subject),
            'expect': expect,
            'actual': '' if actual is None else str(actual),
            'evidence': evidence or {},
            'hint': hint,
        }
        if delta is not None:
            finding['delta'] = round(float(delta), 3)
        box = (evidence or {}).get('bbox')
        if box and self.grid:
            finding['cell'] = cell_of(box, self.grid)
        self.findings.append(finding)


def audit_datum(collector, parts, facts, spec, stl):
    rules = collector.rules
    slack = tolerance(rules, 'bbox_slack', 0.05)
    units = str(spec.get('units') or 'm').lower()
    if units not in ('m', 'mm', 'meters', 'millimeters'):
        collector.add('datum', 'P1', 'units', 'm 或 mm', units,
                      hint='spec.units 只能是 m 或 mm；混用会让所有判据差 1000 倍')
    boxes = [part['bbox'] for part in parts]
    # cadw_measure 的 bbox_mm 是"尺寸三元组"（dx,dy,dz），不是六元包围盒；
    # 按原点在 0 建盒，只用于包络/STL 交叉核对（v0.4 首版按 6 元读，导致静默跳过）。
    facts_extent = facts.get('bbox_mm')
    facts_box = None
    if isinstance(facts_extent, (list, tuple)) and len(facts_extent) == 3:
        facts_box = [0.0, 0.0, 0.0,
                     float(facts_extent[0]) * 0.001,
                     float(facts_extent[1]) * 0.001,
                     float(facts_extent[2]) * 0.001]
    elif isinstance(facts_extent, (list, tuple)) and len(facts_extent) == 6:
        facts_box = [float(value) * 0.001 for value in facts_extent]
    if facts_box:
        boxes.append(facts_box)
    envelope = spec.get('model_bbox')
    if envelope and boxes:
        merged = union_bbox(boxes)
        for axis, index in (('x', 0), ('y', 1), ('z', 2)):
            low = float(envelope[index])
            high = float(envelope[index + 3])
            if merged[index] < low - slack:
                collector.add('datum', 'P1', 'packing envelope ' + axis,
                              '>= %.2f' % low, '%.2f' % merged[index], merged[index] - low,
                              evidence={'bbox': merged},
                              hint='模型在 %s 方向超出允许包络：核对主尺度/原点，或放宽 model_bbox' % axis)
            if merged[index + 3] > high + slack:
                collector.add('datum', 'P1', 'packing envelope ' + axis,
                              '<= %.2f' % high, '%.2f' % merged[index + 3], merged[index + 3] - high,
                              evidence={'bbox': merged},
                              hint='模型在 %s 方向超出允许包络：核对主尺度/原点，或放宽 model_bbox' % axis)
    solid_count = facts.get('solid_count')
    if solid_count is not None and int(solid_count) == 0:
        collector.add('datum', 'P0', 'solid count', '>= 1', '0', 0,
                      hint='文档里没有实体：检查特征是否全部失败，或草图未闭合导致拉伸为空')
    if stl and not parts and facts_box:
        parts = [{'name': 'facts envelope', 'bbox': facts_box}]
    if stl and parts:
        merged = union_bbox([part['bbox'] for part in parts])
        stl_box = stl.get('bbox')
        if stl_box and merged:
            factor = 1.0
            span = max(merged[3] - merged[0], merged[4] - merged[1], merged[5] - merged[2])
            if span <= 0:
                span = 1.0
            diff = max(abs(stl_box[index] - merged[index]) for index in range(6))
            if diff / span > 0.05 and diff > slack:
                collector.add('datum', 'P1', 'stl vs model', '包围盒一致（容差 %.2f）' % slack,
                              '最大差 %.3f' % diff, diff,
                              evidence={'bbox': stl_box},
                              hint='导出的 STL 与当前零件表不一致：重新导出，或确认审计用的是同一版模型')
    if stl and not stl.get('triangles'):
        collector.add('datum', 'P0', 'stl triangles', '> 0', '0',
                      hint='STL 里没有三角面：导出失败或部件为空')


def audit_float(collector, parts):
    rules = collector.rules
    gap_tol = tolerance(rules, 'gap_tol', 0.10)
    buried_tol = tolerance(rules, 'buried_tol', 0.15)
    planes = [float(value) for value in rules.get('support_planes') or []]
    cantilever = rules.get('cantilever') or []
    suppressed = []
    for part in parts:
        if keyword_hit(part['name'], cantilever):
            suppressed.append(part['name'])
            continue
        box = part['bbox']
        z0 = box[2]
        own = volume(box)
        if own > 0:
            wrapped = 0.0
            for other in parts:
                if other is part:
                    continue
                wrapped = max(wrapped, overlap_volume(box, other['bbox']) / own)
            if wrapped > 0.2:
                # 与别的零件有实质交叠：这是"穿插/埋入"的问题，不是悬空；
                # 否则同一件会被同时报成"悬空 0.75"和"穿插 10 m³"（v0.4 首版踩过）。
                continue
        candidates = []
        for plane in planes:
            candidates.append((abs(z0 - plane), z0 - plane, 'plane %.3f' % plane, None))
        for other in parts:
            if other is part or other['name'] == part['name'] and other['bbox'] == box:
                continue
            if not footprint_overlap(box, other['bbox']):
                continue
            candidates.append((abs(z0 - other['bbox'][5]), z0 - other['bbox'][5],
                               'top of ' + other['name'], volume(other['bbox'])))
        if not candidates:
            continue
        # 只从"底面之下（含容差）"的支撑里挑最近的：否则 z=5 的零件会被判成
        # "低于上层 9.30 面 4.3 m"（v0.4 首版方向判定 bug，audit-dev 复现）。
        # 容差取 max(gap_tol, buried_tol)：底面略微"嵌进"支撑体（如球罩坐进基座 0.1 m）
        # 是贴合，不是悬空；再加 1e-9 抵消浮点边界（24.5-24.6 = -0.1000000000000014）。
        limit = -max(gap_tol, buried_tol) - 1e-9
        below = [item for item in candidates if item[1] >= limit]
        if below:
            _distance, gap, support, support_volume = min(below, key=lambda item: item[1])
        else:
            # 全部支撑都在上方：取最近的那个面作为参考，gap 为负 = 低于全部支撑
            _distance, gap, support, support_volume = min(candidates, key=lambda item: item[0])
        if gap > gap_tol:
            collector.add('float', 'P0', part['name'], '底面贴 %s' % support, 'z0 = %.3f' % z0, gap,
                          evidence={'part': part['name'], 'bbox': box, 'support': support},
                          hint='把底面落到 %s，或把该零件加进 rules.cantilever（确实悬臂的话）' % support)
        elif gap < -buried_tol - 1e-9:
            # 只有"比它大"的实体才有资格压它上头：甲板室底面低于甲板小件顶面，
            # 是小件立在上面，不是甲板室插穿（v0.4 实机把这个报成了 P0）。
            if support_volume is not None and support_volume < volume(box):
                continue
            collector.add('float', 'P0', part['name'], '底面不低于 %s' % support, 'z0 = %.3f' % z0, gap,
                          evidence={'part': part['name'], 'bbox': box, 'support': support},
                          hint='底面比支撑面低 %.2f m：抬到 %s，或确认这是有意穿插（如支柱/龙骨）' % (
                              abs(gap), support))
    return suppressed


def inside_inset(small, big, inset):
    """small 是否整体落在 big 内部并留出 inset 余量。

    贴面件（舷窗贴外板、栏杆贴甲板边）的 bbox 与大体积件的 bbox 必然相交，
    但它们有一面与宿主边界共面 —— 用"缩进包含"就能把这种情况排除掉，
    否则每个贴面件都会被误报成"埋入"（v0.4 首版就踩了这个）。
    """
    return (small[0] >= big[0] + inset and small[3] <= big[3] - inset and
            small[1] >= big[1] + inset and small[4] <= big[4] - inset and
            small[2] >= big[2] + inset and small[5] <= big[5] - inset)


def audit_buried(collector, parts):
    rules = collector.rules
    cover = tolerance(rules, 'buried_cover', 0.8)
    ratio = tolerance(rules, 'buried_ratio', 10.0)
    inset = tolerance(rules, 'buried_tol', 0.15)
    allow = rules.get('buried_allow') or []
    # 只有"盒状大件"能当宿主：船体/线型件的 bbox 是一个覆盖半条船的大空盒，
    # 甲板上的设备全落在它里面，按 bbox 判会一片"埋入"（v0.4 实机 460 件直接报了 34 条假 P0）。
    # 真机做法同 v9_verify 表 4："盒状大件完全包住小件"才算埋入。
    hosts = rules.get('buried_host_allow') or []
    for small in parts:
        if keyword_hit(small['name'], allow):
            continue
        small_volume = volume(small['bbox'])
        if small_volume <= 0:
            continue
        for big in parts:
            if big is small:
                continue
            if hosts and not keyword_hit(big['name'], hosts):
                continue
            if volume(big['bbox']) < ratio * small_volume:
                continue
            if not inside_inset(small['bbox'], big['bbox'], inset):
                continue
            inside = overlap_volume(small['bbox'], big['bbox']) / small_volume
            if inside >= cover:
                collector.add('buried', 'P0', small['name'],
                              '不被 %s 吞没' % big['name'], '体积 %.0f%% 在内' % (inside * 100.0), inside,
                              evidence={'part': small['name'], 'bbox': small['bbox'], 'host': big['name']},
                              hint='零件几乎整个埋在 %s 里：挪到外表面（贴面件用外壳半宽函数），'
                                   '或确认它只是内部结构并加进 rules.buried_allow' % big['name'])
                break


def audit_clash(collector, parts):
    rules = collector.rules
    clash_vol = tolerance(rules, 'clash_vol', 0.5)
    clash_frac = tolerance(rules, 'clash_frac', 0.35)
    cover = tolerance(rules, 'buried_cover', 0.8)
    reported = 0
    surface = rules.get('surface_parts') or []
    for index, left in enumerate(parts):
        for right in parts[index + 1:]:
            # 贴面件（玻璃带/舷窗/栏杆/面板）本来就是"嵌进"宿主的，bbox 交叠是建模方式，
            # 不是穿插；形状级穿插要 mesh 才能判，这里直接跳过以免整表噪声。
            if token_hit(left['name'], surface) or token_hit(right['name'], surface):
                continue
            inter = overlap_volume(left['bbox'], right['bbox'])
            if inter <= clash_vol:
                continue
            if pair_allowed(left['name'], right['name'], rules):
                continue
            small = min(volume(left['bbox']), volume(right['bbox']))
            if small <= 0:
                continue
            # 相对判据：只有"小件的相当一部分体积插进对方"才算穿插。
            # bbox 交叠在流水线模型里到处都是（横贯全宽的栏杆/舷墙、穿过甲板室的烟囱），
            # 只按绝对体积报，460 件模型会出 41 条 P1 假阳性（v0.4 实机）。
            if inter / small < clash_frac:
                continue
            # 几乎完全包含（>= clash_contain）：那是"埋入"的判据范围，交给 buried，
            # 避免同一对零件既报穿插又报埋入。
            if inter / small > tolerance(rules, 'clash_contain', 0.95):
                continue
            reported += 1
            if reported > 40:
                collector.add('clash', 'P1', 'clash batch', '交集体积 <= %.2f' % clash_vol,
                              '超过 40 对', 0, hint='交集对太多：先按区收敛（名字前缀）再逐对处理')
                return
            collector.add('clash', 'P1', left['name'] + ' ∩ ' + right['name'],
                          '交集体积 <= %.2f m³' % clash_vol, '%.2f m³' % inter, inter,
                          evidence={'part': left['name'], 'bbox': left['bbox'], 'other': right['name']},
                          hint='两件 bbox 交叠 %.2f m³：确认是否允许（如支柱穿船壳 → 加进 rules.clash_allow），'
                               '否则挪开或缩短其中一件' % inter)


def mirror_pair(name_a, name_b, keywords):
    """左右对称件（port/starboard）bbox 往往几乎重合，但它们是两个件，不是重复件。"""
    left = (name_a or '').lower()
    right = (name_b or '').lower()
    hits_a = [keyword for keyword in keywords if keyword.strip() and keyword in left]
    hits_b = [keyword for keyword in keywords if keyword.strip() and keyword in right]
    if not hits_a or not hits_b:
        return False
    # 去掉镜像词后主体名相同 → 认定为镜像对
    stripped_a = left
    stripped_b = right
    for keyword in keywords:
        text = keyword.strip()
        if not text:
            continue
        stripped_a = stripped_a.replace(text, '')
        stripped_b = stripped_b.replace(text, '')
    return stripped_a.strip() == stripped_b.strip()


def audit_dup(collector, parts):
    rules = collector.rules
    iou_limit = tolerance(rules, 'dup_iou', 0.9)
    seen = {}
    for part in parts:
        seen.setdefault(part['name'].strip().lower(), []).append(part)
    for key, items in seen.items():
        name = items[0]['name']
        if len(items) > 1:
            collector.add('dup', 'P1', name, '零件名唯一', '%d 个同名' % len(items), len(items),
                          evidence={'part': name, 'bbox': items[0]['bbox']},
                          hint='同名会让 STEP 实体名、配色键与差分匹配全部串味：给每个件加后缀（_1/_2 或位置）')
    mirror = rules.get('mirror_keywords') or ['port', 'starboard', ' stb', ' stbd', ' ps', ' sb']
    for index, left in enumerate(parts):
        for right in parts[index + 1:]:
            if left['name'] == right['name']:
                continue
            if mirror_pair(left['name'], right['name'], mirror):
                continue
            inter = overlap_volume(left['bbox'], right['bbox'])
            if inter <= 0:
                continue
            union = volume(left['bbox']) + volume(right['bbox']) - inter
            if union <= 0:
                continue
            iou = inter / union
            if iou > iou_limit:
                # bbox 相同 ≠ 重复件：X 形交叉撑、对角杆共用一个包络，IoU 也是 1.00。
                # 真正能判定重复的是"名字重复"（已经单独报 P1）；这里只做 P2 提示，等人工/网格确认。
                collector.add('dup', 'P2', left['name'] + ' ≈ ' + right['name'],
                              'IoU <= %.2f' % iou_limit, 'IoU %.2f' % iou, iou,
                              evidence={'bbox': left['bbox'], 'other': right['name']},
                              hint='两件 bbox 几乎重合：先确认是不是交叉撑/对角杆/镜像件（bbox 相同不等于重复）；'
                                   '确认重复再删。要判真实重复请用 mesh 比对体积')


SKETCH_STATUS_TEXT = {0: 'unknown', 1: 'fully_defined', 2: 'under_defined', 3: 'over_defined',
                     4: 'no_solution', 5: 'invalid_solution'}


def sketch_status(sketch):
    """解析草图约束状态。真实字段来自 sw_measure：

    `constrained_status`(int 0..5) + `constrained_status_text` + `fully_defined`。
    v0.4 首版只认 `over_defined`/`status`，而插件根本不产出这两个键 ——
    过定义草图会被当成"欠定义 P1"，严重度判错（audit 轮次实测发现）。
    """
    text = str(sketch.get('constrained_status_text') or '').strip().lower()
    if not text:
        value = sketch.get('constrained_status')
        if isinstance(value, (int, float)) and int(value) in SKETCH_STATUS_TEXT:
            text = SKETCH_STATUS_TEXT[int(value)]
    if not text:
        text = str(sketch.get('status') or '').strip().lower()
    if not text and bool(sketch.get('over_defined')):
        text = 'over_defined'
    fully = sketch.get('fully_defined')
    if not text and fully is True:
        text = 'fully_defined'
    if not text and fully is False:
        text = 'under_defined'
    return text


def audit_sketch(collector, facts):
    sketches = facts.get('sketches')
    if not isinstance(sketches, list):
        return
    under = []
    over = []
    broken = []
    for sketch in sketches:
        if not isinstance(sketch, dict):
            continue
        name = str(sketch.get('name') or 'sketch')
        status = sketch_status(sketch)
        if status == 'over_defined':
            over.append((name, sketch))
        elif status in ('no_solution', 'invalid_solution'):
            broken.append((name, sketch))
        elif status == 'under_defined':
            under.append((name, sketch))
    for name, sketch in broken[:10]:
        collector.add('sketch', 'P0', name, '约束有解',
                      str(sketch.get('constrained_status_text') or sketch.get('constrained_status')), 0,
                      evidence={'sketch': name},
                      hint='草图无解/解无效：删掉冲突约束（常见于镜像+对称+重合三重约束），否则重建会失败')
    for name, sketch in over[:20]:
        collector.add('sketch', 'P0', name, '无过定义约束', '过定义（status=%s）' % (
            sketch.get('constrained_status_text') or sketch.get('constrained_status')), 0,
                      evidence={'sketch': name},
                      hint='删掉冗余的尺寸/几何关系（过定义会让参数修改后重建失败）')
    if len(over) > 20:
        collector.add('sketch', 'P0', 'over-defined batch', '0', '%d 个草图' % len(over), len(over),
                      hint='过定义太多：先按特征顺序回溯，通常是一个尺寸与几何关系重复约束')
    for name, sketch in under[:20]:
        missing = sketch.get('missing') or sketch.get('free_degrees')
        collector.add('sketch', 'P1', name, '完全定义', '欠定义%s' % (
            ('（缺 %s）' % missing) if missing else ''), None,
                      evidence={'sketch': name, 'segments': sketch.get('segments')},
                      hint='补尺寸或几何关系把草图定义完；欠定义草图改了参数不更新，是"越改越不对"的根源')
    if len(under) > 20:
        collector.add('sketch', 'P1', 'under-defined batch', '0', '%d 个草图' % len(under), len(under),
                      hint='欠定义太多：优先定义参与关键尺寸链的草图')


def audit_shape(collector, diff):
    rows = diff.get('rows') or []
    profile_rows = diff.get('profile_rows') or []
    rules = collector.rules
    profile_tol = tolerance(rules, 'profile_tol', 1.0)
    for row in rows:
        verdict = str(row.get('verdict') or '')
        if verdict not in ('FAIL', 'MISSING'):
            continue
        box = row.get('bbox')
        evidence = {'part': row.get('part'), 'bbox': box, 'measure': row.get('name')}
        collector.add('shape', 'P1', '%s · %s' % (row.get('part') or '?', row.get('field') or '?'),
                      '参考带 %.2f~%.2f' % (row['ref'][0], row['ref'][1]),
                      '实测 %s' % row.get('model'), row.get('delta'), evidence=evidence,
                      hint=row.get('hint') or '按差分表改对应分量')
        if verdict == 'MISSING':
            collector.findings[-1]['severity'] = 'P0'
            collector.findings[-1]['hint'] = row.get('hint') or '模型里没有这个零件：确认命名或补建'
        if row.get('cell') and not collector.findings[-1].get('cell'):
            collector.findings[-1]['cell'] = row.get('cell')
    for row in profile_rows:
        verdict = str(row.get('verdict') or '')
        values = [value for value in (row.get('delta_vmax'), row.get('delta_vmin')) if value is not None]
        if verdict not in ('FAIL', 'MISSING') or not values:
            continue
        worst = max(values, key=abs)
        if abs(worst) <= profile_tol and verdict != 'MISSING':
            continue
        marker = row.get('cell')
        collector.add('shape', 'P1', 'profile ' + str(marker),
                      '|Δ| <= %.2f' % profile_tol, 'Δ %+.2f' % worst, worst,
                      evidence={'cell': marker, 'u_range': row.get('u_range')},
                      hint=row.get('hint') or '按格号定位到区，改该区模块的线型/构件尺寸')
        if marker and not collector.findings[-1].get('cell'):
            collector.findings[-1]['cell'] = marker


def audit_mass(collector, facts):
    rules = collector.rules
    fill_min = tolerance(rules, 'fill_min', 0.02)
    box = facts.get('bbox_mm')
    vol = facts.get('volume_mm3')
    if not box or vol is None:
        return
    try:
        extent = [float(value) for value in box]
        solid_volume = float(vol)
    except Exception:
        return
    envelope = extent[0] * extent[1] * extent[2]
    if envelope <= 0:
        return
    fill_max = tolerance(rules, 'fill_max', 0.999)
    ratio = solid_volume / envelope
    if ratio < fill_min:
        collector.add('mass', 'P0', 'fill ratio', '>= %.3f' % fill_min, '%.4f' % ratio, ratio,
                      hint='体积/包络过低：多数是壳体未闭合（破面）或布尔失败留下空壳；先做实体检查')
    elif ratio > fill_max:
        # 阈值定在 0.999：带孔的板本来就有 99%+ 的包络填充率，
        # 阈值过低会把正常零件全报成"近乎实心"（v0.4 首版取 0.98，实测误报）。
        collector.add('mass', 'P2', 'fill ratio', '<= %.3f' % fill_max, '%.4f' % ratio, ratio,
                      hint='体积几乎等于包络：确认切除/孔是否真的生效，或该件本来就是实心块（确认后可忽略）')


def audit_naming(collector, parts):
    pattern = re.compile(r'^[a-z0-9][a-z0-9 _.-]*$')
    count = 0
    for part in parts:
        name = part['name']
        if pattern.match(name):
            continue
        count += 1
        if count > 20:
            continue
        reason = []
        if re.search(r'[A-Z]', name):
            reason.append('有大写')
        if re.search(r'[^\x00-\x7f]', name):
            reason.append('有非 ASCII')
        if name != name.strip():
            reason.append('首尾空格')
        if '__' in name or name.endswith('_'):
            reason.append('下划线命名')
        collector.add('naming', 'P2', name, '英文小写短语', '、'.join(reason) or '格式不符',
                      evidence={'part': name, 'bbox': part['bbox']},
                      hint='零件名同时是 STEP 实体名与渲染配色键：统一小写、用空格分隔、避免中文（会破坏配色匹配）')
    if count > 20:
        collector.add('naming', 'P2', 'naming batch', '全部合规', '%d 个不合规' % count, count,
                      hint='命名问题成批：先定一个命名规范再批量重命名（渲染配色按前缀匹配）')


# --------------------------------------------------------------------------
# 报告与入口
# --------------------------------------------------------------------------
def render_report(collector, result, rules):
    lines = ['# 对抗审查报告', '',
             '生成：' + common.now_iso(), '',
             '总判定：**%s**（P0 %d / P1 %d / P2 %d）' % (
                 result['verdict'], result['counts']['P0'], result['counts']['P1'], result['counts']['P2']),
             '',
             '> P0 未清零前不要进入"调形状/调涂装"阶段：结构错了，形状每轮都在改错的东西。', '']
    for severity in ('P0', 'P1', 'P2'):
        items = [item for item in result['findings'] if item['severity'] == severity]
        if not items:
            continue
        lines.extend(['## ' + severity + '（%d）' % len(items), '',
                      '| id | 角度 | 格号 | 对象 | 期望 | 实测 | Δ | 改法 |',
                      '|---|---|---|---|---|---|---|---|'])
        for item in items:
            lines.append('| %s | %s | %s | %s | %s | %s | %s | %s |' % (
                item['id'], item['angle'], item.get('cell') or '-', item['subject'],
                item['expect'], item['actual'],
                '-' if item.get('delta') is None else '%+.3f' % item['delta'],
                item['hint']))
        lines.append('')
    lines.extend(['## 角度统计', '', '| 角度 | P0 | P1 | P2 | 开关 |', '|---|---|---|---|---|'])
    for name, code in ANGLES:
        stat = result['by_angle'].get(name) or {}
        lines.append('| %s %s | %d | %d | %d | %s |' % (
            code, name, stat.get('P0', 0), stat.get('P1', 0), stat.get('P2', 0),
            'on' if angle_on(rules, name) else 'off'))
    closed = [name for name, _code in ANGLES if not angle_on(rules, name)]
    if closed:
        lines.extend(['', '被 rules 关闭的角度：' + '、'.join(closed)])
    return '\n'.join(lines) + '\n'


def main(spec):
    workspace = common.workspace_of(spec)
    rules = load_rules(spec.get('rules'))
    # units 决定 parts / support_planes / model_bbox / grid 的换算比例。
    # v0.4 首版只换算了 parts，导致 units=mm 时支撑标高与包络判据静默失效
    # （把 9.30 mm 当成 9.30 m，悬空全部误判为"插穿 9.29 m"）。
    units = str(spec.get('units') or 'm').lower()
    factor = 0.001 if units in ('mm', 'millimeters') else 1.0
    if isinstance(spec.get('support_planes'), list) and spec['support_planes']:
        rules['support_planes'] = [float(value) * factor for value in spec['support_planes']]
    if isinstance(spec.get('model_bbox'), (list, tuple)) and len(spec['model_bbox']) == 6:
        spec = dict(spec)
        spec['model_bbox'] = [float(value) * factor for value in spec['model_bbox']]
    grid = spec.get('grid') if isinstance(spec.get('grid'), dict) else {}
    if grid and factor != 1.0:
        grid = dict(grid)
        for key in ('u_step', 'v_step', 'w_step', 'u_origin', 'v_origin', 'w_origin'):
            if grid.get(key) is not None:
                grid[key] = float(grid[key]) * factor
    parts = normalize_parts(spec, workspace)
    facts = load_facts(spec)
    diff = load_diff(spec, workspace)
    stl_path = spec.get('stl')
    if stl_path:
        stl_path = stl_path if os.path.isabs(stl_path) else os.path.abspath(os.path.join(workspace, stl_path))
    stl = stl_summary(stl_path)
    if not parts and not facts and not diff:
        raise common.CadError('BAD_ARGS', 'cadw_audit 至少需要 parts / facts / diff 之一')

    collector = Collector(rules, grid)
    suppressed = []
    if angle_on(rules, 'datum'):
        audit_datum(collector, parts, facts, spec, stl)
    if angle_on(rules, 'float') and parts:
        suppressed = audit_float(collector, parts)
    if angle_on(rules, 'buried') and parts:
        audit_buried(collector, parts)
    if angle_on(rules, 'clash') and parts:
        audit_clash(collector, parts)
    if angle_on(rules, 'dup') and parts:
        audit_dup(collector, parts)
    if angle_on(rules, 'sketch'):
        audit_sketch(collector, facts)
    if angle_on(rules, 'shape'):
        audit_shape(collector, diff)
    if angle_on(rules, 'mass'):
        audit_mass(collector, facts)
    if angle_on(rules, 'naming') and parts:
        audit_naming(collector, parts)

    findings = collector.findings
    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item['severity'], 9),
                                    ANGLE_CODES.get(item['angle'], 'A0'), item['subject']))
    counts = {'P0': 0, 'P1': 0, 'P2': 0, 'total': len(findings)}
    # 零计数也占位：报告表的"角度统计"要能看出这个角度跑过且没问题
    by_angle = {name: {'P0': 0, 'P1': 0, 'P2': 0} for name, _code in ANGLES}
    for item in findings:
        counts[item['severity']] = counts.get(item['severity'], 0) + 1
        stat = by_angle.setdefault(item['angle'], {'P0': 0, 'P1': 0, 'P2': 0})
        stat[item['severity']] = stat.get(item['severity'], 0) + 1
    verdict = 'FAIL' if counts['P0'] else ('WARN' if counts['P1'] else 'PASS')
    result = {
        'ok': True,
        'verdict': verdict,
        'counts': counts,
        'findings': findings,
        'by_angle': by_angle,
        'rules_used': {'tolerances': rules.get('tolerances'), 'support_planes': rules.get('support_planes'),
                       'angles': rules.get('angles')},
        'suppressed_cantilever': sorted(set(suppressed))[:20],
        'inputs': {'parts': len(parts), 'facts': bool(facts), 'diff_rows': len(diff.get('rows') or []),
                   'profile_rows': len(diff.get('profile_rows') or []), 'stl': bool(stl)},
    }
    report_path = None
    if spec.get('write_report', True):
        out_dir = spec.get('out_dir')
        if out_dir:
            out_dir = out_dir if os.path.isabs(out_dir) else os.path.abspath(os.path.join(workspace, out_dir))
        else:
            out_dir = os.path.join(workspace, 'cad-output', 'audit')
        common.ensure_dir(out_dir)
        stamp = common.now_iso().replace(':', '').replace('-', '').replace('+', '_')[:17]
        report_path = os.path.join(out_dir, 'audit-%s.md' % stamp)
        try:
            with open(report_path, 'w', encoding='utf-8') as handle:
                handle.write(render_report(collector, result, rules))
        except Exception:
            report_path = None
    result['report_path'] = report_path
    top = [item for item in findings if item['severity'] == 'P0'][:6]
    summary_lines = [
        '审查：%s（P0 %d / P1 %d / P2 %d）' % (verdict, counts['P0'], counts['P1'], counts['P2']),
        '输入：零件 %d、测量事实 %s、差分 %d 行、STL %s' % (
            len(parts), '有' if facts else '无', len(diff.get('rows') or []), '有' if stl else '无'),
    ]
    for item in top:
        summary_lines.append('  P0 ' + item['id'] + ' ' + item['angle'] + ' · ' + item['subject'] + '：' +
                             item['expect'] + ' → ' + item['actual'])
    if report_path:
        summary_lines.append('报告：' + report_path)
    if not findings:
        summary_lines.append('未发现可判定问题（注意：这只代表判据内无异常，不代表外形正确）')
    result['summary'] = '\n'.join(summary_lines)
    return result


if __name__ == '__main__':
    common.run(main)