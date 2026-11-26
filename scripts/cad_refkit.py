# -*- coding: utf-8 -*-
"""cad_refkit.py —— 参考图对位建模套件：标定 / 掩膜量测 / 米制网格 / 差分。

为什么单独一个脚本：这四件事共用同一套坐标换算（像素↔米）与掩膜语义，
拆开就会出现"量测用一套标定、叠图用另一套"的漂移。

动作（spec.action）：
  list       列出工作区里的参考图工程
  get        读取工程 JSON（含帧、掩膜、量测、目标带）
  calibrate  建立/更新一帧参考图的像素↔米标定，登记掩膜与网格
  measure    在参考图上按掩膜量测（profile / bbox / extent / coverage），写进工程
  render     米制网格叠图：模型多视角 + 参考帧 + 责任分区，输出 png 与格号索引
  diff       参考真值 vs 模型实测 → 逐格/逐件差分表（md + json）

约束：只读参考图与模型文件，只写 <workspace>/.cad-workbench/refkit 与 out_dir。
失败一律抛 common.CadError（码：REF_NOT_FOUND / REF_INVALID / BAD_ARGS / PYTHON_DEPS / REF_FAILED）。
"""
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402

DEFAULT_AXES = {'u': 'x', 'v': 'z', 'w': 'y'}
COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
AXIS_VECTORS = {
    'x': (1.0, 0.0, 0.0),
    '-x': (-1.0, 0.0, 0.0),
    'y': (0.0, 1.0, 0.0),
    '-y': (0.0, -1.0, 0.0),
    'z': (0.0, 0.0, 1.0),
    '-z': (0.0, 0.0, -1.0),
}
# 每个视图：(横轴 u, 纵轴 v, 近处深度轴)；深度轴升序 = 由远到近绘制
VIEWS = {
    'side': ('x', 'z', 'y'),
    'top': ('x', 'y', 'z'),
    'front': ('y', 'z', '-x'),
    'rear': ('-y', 'z', 'x'),
    'section': ('y', 'z', '-x'),
}
PART_COLORS = [
    (0.77, 0.17, 0.11), (0.16, 0.20, 0.24), (0.96, 0.72, 0.16), (0.07, 0.22, 0.48),
    (0.24, 0.30, 0.22), (0.75, 0.78, 0.81), (0.98, 0.36, 0.05), (0.16, 0.55, 0.45),
]


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def _np():
    try:
        import numpy
        return numpy
    except Exception:
        raise common.CadError('PYTHON_DEPS', '需要 numpy（pip install numpy）')


def _image():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except Exception:
        raise common.CadError('PYTHON_DEPS', '需要 Pillow（pip install pillow）')


def safe_id(value):
    raw = str(value or '').strip()
    cleaned = re.sub(r'[^0-9A-Za-z_.-]+', '-', raw).strip('.-')
    return cleaned[:64] or 'refkit'


def num(value, field, default=None):
    if value is None:
        if default is None:
            raise common.CadError('BAD_ARGS', '缺少数值字段 ' + field)
        return float(default)
    try:
        return float(value)
    except Exception:
        raise common.CadError('BAD_ARGS', '字段 ' + field + ' 不是数字：' + str(value))


def round3(value):
    try:
        return round(float(value), 3)
    except Exception:
        return value


def refkit_root(workspace):
    return os.path.join(workspace, '.cad-workbench', 'refkit')


def project_path(workspace, pid):
    return os.path.join(refkit_root(workspace), safe_id(pid) + '.json')


def new_project(pid):
    return {
        'version': 1,
        'id': safe_id(pid),
        'units': 'm',
        'axes': dict(DEFAULT_AXES),
        'grid': {'u_step': 5.0, 'v_step': 5.0, 'w_step': 2.0, 'u_origin': 0.0, 'v_origin': 0.0, 'w_origin': 0.0},
        'frames': {},
        'masks': {},
        'measures': {},
        'targets': [],
        'created': common.now_iso(),
        'updated': common.now_iso(),
    }


def load_project(spec, require=True):
    workspace = common.workspace_of(spec)
    pid = spec.get('project')
    path = project_path(workspace, pid)
    if not os.path.isfile(path):
        if not require:
            return workspace, new_project(pid), path
        raise common.CadError('REF_NOT_FOUND',
                              '参考图工程不存在：' + safe_id(pid) + '（先跑 cadw_ref action=calibrate）')
    with open(path, 'r', encoding='utf-8') as handle:
        project = json.load(handle)
    project.setdefault('frames', {})
    project.setdefault('masks', {})
    project.setdefault('measures', {})
    project.setdefault('targets', [])
    project.setdefault('grid', new_project(pid)['grid'])
    project.setdefault('axes', dict(DEFAULT_AXES))
    return workspace, project, path


def save_project(path, project):
    project['updated'] = common.now_iso()
    common.ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(project, handle, ensure_ascii=False, indent=1)
    return path


def resolve_input(workspace, path):
    raw = str(path or '').strip()
    if not raw:
        return None
    return raw if os.path.isabs(raw) else os.path.abspath(os.path.join(workspace, raw))


def out_dir_of(spec, workspace, project):
    requested = spec.get('out_dir')
    if requested:
        return common.ensure_dir(resolve_input(workspace, requested))
    return common.ensure_dir(os.path.join(workspace, 'cad-output', 'refkit', safe_id(project.get('id'))))


# --------------------------------------------------------------------------
# 标定
# --------------------------------------------------------------------------
def axis_params(axis, name):
    if not isinstance(axis, dict):
        raise common.CadError('REF_INVALID', '标定 ' + name + ' 轴需要 {px0,m0,px1,m1}')
    try:
        px0 = float(axis['px0'])
        m0 = float(axis['m0'])
        px1 = float(axis['px1'])
        m1 = float(axis['m1'])
    except Exception:
        raise common.CadError('REF_INVALID', '标定 ' + name + ' 轴需要 px0/m0/px1/m1 四个数')
    if abs(px1 - px0) < 1e-9:
        raise common.CadError('REF_INVALID', '标定 ' + name + ' 轴的两点像素重合')
    if abs(m1 - m0) < 1e-9:
        raise common.CadError('REF_INVALID', '标定 ' + name + ' 轴的两点米值重合')
    k = (m1 - m0) / (px1 - px0)
    return {
        'px0': px0, 'm0': m0, 'px1': px1, 'm1': m1,
        'k': k, 'm_per_px': k, 'px_per_m': (1.0 / k) if k else None,
    }


def axis_m(params, px):
    return params['m0'] + (float(px) - params['px0']) * params['k']


def axis_px(params, meters):
    return params['px0'] + (float(meters) - params['m0']) / params['k']


def action_calibrate(spec):
    workspace, project, path = load_project(spec, require=False)
    frame_spec = spec.get('frame') or {}
    frame_id = safe_id(frame_spec.get('id') or 'frame')
    image_path = resolve_input(workspace, frame_spec.get('path'))
    if not image_path:
        raise common.CadError('BAD_ARGS', 'calibrate 需要 frame.path（参考图路径）')
    if not os.path.isfile(image_path):
        raise common.CadError('REF_NOT_FOUND', '参考图不存在：' + image_path)
    Image, _draw, _font = _image()
    with Image.open(image_path) as image:
        image_size = [int(image.width), int(image.height)]
    up = axis_params(frame_spec.get('u'), 'u')
    vp = axis_params(frame_spec.get('v'), 'v')

    checks = []
    # v 轴符号要单独判：镜像/艉视帧也会出现"u 反向 + v 正向"的组合，
    # 原来要求 up>0 才提示，等于放过了这类错标定（复核 P2-7a）
    if vp['k'] > 0:
        checks.append('v 轴斜率为正：约定是"行向下、v 向上"，通常应为负值（镜像/倒置帧请确认）')
    ratio = abs(vp['m_per_px'] / up['m_per_px']) if up['m_per_px'] else None
    if ratio is not None and (ratio > 1.10 or ratio < 0.90):
        checks.append('两轴 m/px 相差 %.1f%%（%.5f vs %.5f）：正投影参考图通常接近 1，'\
                      '请确认两个方向的标定基准点是否量自同一平面' % (
                          abs(ratio - 1.0) * 100.0,
                          up['m_per_px'], vp['m_per_px']))
    in_bounds = []
    for key, params in (('u', up), ('v', vp)):
        limit = (image_size[0] if key == 'u' else image_size[1]) - 1
        for px in (params['px0'], params['px1']):
            # 有效像素是 0..width-1：px=width 已经不落在图上（复核 P2-7b）
            if px < 0 or px > limit:
                in_bounds.append('%s 轴标定点 px=%.1f 超出图像范围（有效 0..%d）' % (key, px, limit))
    checks.extend(in_bounds)

    frame = {
        'id': frame_id,
        'path': image_path,
        'view': str(frame_spec.get('view') or 'side'),
        'image_size': image_size,
        'u': {key: up[key] for key in ('px0', 'm0', 'px1', 'm1')},
        'v': {key: vp[key] for key in ('px0', 'm0', 'px1', 'm1')},
        'scale': {
            'm_per_px_u': round3(up['m_per_px']),
            'm_per_px_v': round3(vp['m_per_px']),
            'px_per_m_u': round3(up['px_per_m']),
            'px_per_m_v': round3(vp['px_per_m']),
        },
        'note': str(frame_spec.get('note') or ''),
        'created': common.now_iso(),
    }
    if frame_spec.get('slice'):
        frame['slice'] = frame_spec['slice']
    project['frames'][frame_id] = frame

    if isinstance(spec.get('masks'), dict):
        for name, rule in spec['masks'].items():
            if isinstance(rule, dict):
                project['masks'][safe_id(name)] = rule
    if isinstance(spec.get('grid'), dict):
        for key, value in spec['grid'].items():
            project['grid'][key] = float(value) if isinstance(value, (int, float)) else value
    if isinstance(spec.get('axes'), dict):
        for key in ('u', 'v', 'w'):
            if spec['axes'].get(key):
                project['axes'][key] = str(spec['axes'][key])
    if spec.get('model_bbox'):
        project['model_bbox'] = [num(item, 'model_bbox') for item in spec['model_bbox']]

    save_project(path, project)
    return {
        'ok': True,
        'project': project['id'],
        'frame': frame,
        'frames': sorted(project['frames'].keys()),
        'checks': checks,
        'summary': '\n'.join([
            '标定完成：' + project['id'] + '/' + frame_id + '（' + frame['view'] + '）',
            'u 轴：px %.1f→%.1f = %.2f→%.2f m（%.5f m/px）' % (
                up['px0'], up['px1'], up['m0'], up['m1'], up['m_per_px']),
            'v 轴：px %.1f→%.1f = %.2f→%.2f m（%.5f m/px）' % (
                vp['px0'], vp['px1'], vp['m0'], vp['m1'], vp['m_per_px']),
        ] + (['注意：' + item for item in checks] if checks else [])),
    }


# --------------------------------------------------------------------------
# 掩膜与量测
# --------------------------------------------------------------------------
def resolve_mask_rule(project, spec, item):
    rule = item.get('mask')
    if isinstance(rule, dict):
        return rule
    name = safe_id(rule) if rule else None
    masks = dict(project.get('masks') or {})
    if isinstance(spec.get('masks'), dict):
        for key, value in spec['masks'].items():
            if isinstance(value, dict):
                masks[safe_id(key)] = value
    if not name:
        raise common.CadError('BAD_ARGS', '量测 ' + str(item.get('id')) + ' 缺少 mask（名字或规则对象）')
    if name not in masks:
        raise common.CadError('BAD_ARGS', '工程里没有掩膜 "' + name + '"，可用：' + ', '.join(sorted(masks)) or '（空）')
    return masks[name]


def build_mask(arr, rule, U, V):
    np = _np()
    r = arr[:, :, 0].astype('int32')
    g = arr[:, :, 1].astype('int32')
    b = arr[:, :, 2].astype('int32')
    kind = str(rule.get('type') or 'color').lower()
    if kind == 'dark':
        mask = (r <= int(rule.get('r_max', 70))) & (g <= int(rule.get('g_max', 70))) & (b <= int(rule.get('b_max', 80)))
    elif kind == 'ink':
        tol = float(rule.get('tol') or 18)
        bg = [float(np.median(r)), float(np.median(g)), float(np.median(b))]
        mask = (np.abs(r - bg[0]) + np.abs(g - bg[1]) + np.abs(b - bg[2])) > tol * 3.0
    else:
        mask = np.ones(r.shape, dtype=bool)
        span = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        channels = {
            'r': r, 'g': g, 'b': b,
            'r_minus_b': r - b, 'r_minus_g': r - g, 'range': span,
        }
        for key, value in rule.items():
            if value is None:
                continue
            base = None
            direction = None
            for prefix in ('r_minus_b', 'r_minus_g'):
                if key.startswith(prefix + '_'):
                    base = channels[prefix]
                    direction = key[len(prefix) + 1:]
                    break
            if base is None and key == 'range_max':
                base, direction = span, 'max'
            if base is None and key[:1] in ('r', 'g', 'b') and '_' in key:
                base = channels[key[0]]
                direction = key.split('_', 1)[1]
            if base is None or direction not in ('min', 'max'):
                continue
            threshold = float(value)
            mask &= (base >= threshold) if direction == 'min' else (base <= threshold)
    for rect in rule.get('exclude_px') or []:
        try:
            x0, y0, x1, y1 = [int(item) for item in rect]
        except Exception:
            continue
        mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
    for key, coords in (('u', U), ('v', V)):
        bounds = rule.get(key)
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        low, high = bounds
        if low is not None:
            mask &= coords >= float(low)
        if high is not None:
            mask &= coords <= float(high)
    return mask


def bin_count(start, end, step):
    """覆盖 [start, end] 需要多少箱。

    原来用 int(round(span/step))：[0,10] step=4 只出 2 箱（覆盖 0..8），
    尾部静默少测且 u_range 还写 0..10；step=1 时 Python 的银行家舍入
    会把 round(10.5) 变成 10，再丢半格（refkit 复核 P1-1）。
    """
    span = float(end) - float(start)
    if step <= 0 or span <= 0:
        return 0
    return max(1, int(math.ceil(span / step - 1e-9)))


def do_profile(mask, U, V, rule):
    np = _np()
    bounds = rule.get('u')
    u_start = float(bounds[0]) if isinstance(bounds, (list, tuple)) and bounds[0] is not None else float(np.min(U))
    u_end = float(bounds[1]) if isinstance(bounds, (list, tuple)) and bounds[1] is not None else float(np.max(U))
    step = float(rule.get('step') or 0.5)
    if step <= 0:
        raise common.CadError('BAD_ARGS', 'profile.step 必须为正')
    v_filter = rule.get('v_filter')
    selected = mask
    if isinstance(v_filter, (list, tuple)) and len(v_filter) == 2:
        if v_filter[0] is not None:
            selected = selected & (V >= float(v_filter[0]))
        if v_filter[1] is not None:
            selected = selected & (V <= float(v_filter[1]))
    columns = np.nonzero((U[0] >= u_start) & (U[0] <= u_end))[0]
    points = []
    if columns.size:
        count = bin_count(u_start, u_end, step)
        for index in range(count):
            low = u_start + index * step
            high = low + step
            picks = columns[(U[0][columns] >= low) & (U[0][columns] < high)]
            if picks.size == 0:
                continue
            block = selected[:, picks]
            rows = np.nonzero(block.any(axis=1))[0]
            if rows.size == 0:
                continue
            v_low = float(np.min(V[rows, 0]))
            v_high = float(np.max(V[rows, 0]))
            points.append([round((low + high) / 2.0, 3), round(v_low, 3), round(v_high, 3)])
    return {
        'points': points,
        'n': len(points),
        'step': step,
        'u_range': [round(u_start, 3), round(u_end, 3)],
        'v_filter': v_filter,
    }


def do_bbox(mask, U, V, rule):
    np = _np()
    roi = rule.get('roi') if isinstance(rule.get('roi'), dict) else {}
    selected = mask
    for key, coords in (('u', U), ('v', V)):
        bounds = roi.get(key)
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        if bounds[0] is not None:
            selected = selected & (coords >= float(bounds[0]))
        if bounds[1] is not None:
            selected = selected & (coords <= float(bounds[1]))
    rows, cols = np.nonzero(selected)
    if rows.size == 0:
        return {'pixels': 0}
    values_u = U[0, cols]
    values_v = V[rows, 0]
    u0 = float(np.min(values_u))
    u1 = float(np.max(values_u))
    v0 = float(np.min(values_v))
    v1 = float(np.max(values_v))
    return {
        'pixels': int(rows.size),
        'u0': round3(u0), 'u1': round3(u1), 'v0': round3(v0), 'v1': round3(v1),
        'du': round3(u1 - u0), 'dv': round3(v1 - v0),
        'cu': round3(float(np.mean(values_u))), 'cv': round3(float(np.mean(values_v))),
    }


def do_extent(mask, U, V, rule, frame):
    np = _np()
    band = 2 if rule.get('band') is None else max(0, int(rule.get('band')))
    up = axis_params(frame.get('u'), 'u')
    vp = axis_params(frame.get('v'), 'v')
    scan = mask
    v_bounds = rule.get('v_filter')
    if isinstance(v_bounds, (list, tuple)) and len(v_bounds) == 2:
        if v_bounds[0] is not None:
            scan = scan & (V >= float(v_bounds[0]))
        if v_bounds[1] is not None:
            scan = scan & (V <= float(v_bounds[1]))
    if rule.get('at_u') is not None:
        column = int(round(axis_px(up, num(rule.get('at_u'), 'at_u'))))
        block = scan[:, max(0, column - band):column + band + 1]
        rows = np.nonzero(block.any(axis=1))[0]
        if rows.size == 0:
            return {'pixels': 0, 'at_u': round3(num(rule.get('at_u'), 'at_u')),
                    'u0': None, 'u1': None, 'v0': None, 'v1': None}
        v_low = float(np.min(V[rows, 0]))
        v_high = float(np.max(V[rows, 0]))
        at_u = round3(num(rule.get('at_u'), 'at_u'))
        return {
            'pixels': int(np.count_nonzero(block)),
            'at_u': at_u,
            'u0': at_u, 'u1': at_u,
            'v0': round3(v_low), 'v1': round3(v_high), 'dv': round3(v_high - v_low),
        }
    if rule.get('at_v') is not None:
        row = int(round(axis_px(vp, num(rule.get('at_v'), 'at_v'))))
        block = scan[max(0, row - band):row + band + 1, :]
        cols = np.nonzero(block.any(axis=0))[0]
        if cols.size == 0:
            return {'pixels': 0, 'at_v': round3(num(rule.get('at_v'), 'at_v')),
                    'u0': None, 'u1': None, 'v0': None, 'v1': None}
        u_low = float(np.min(U[0, cols]))
        u_high = float(np.max(U[0, cols]))
        at_v = round3(num(rule.get('at_v'), 'at_v'))
        return {
            'pixels': int(np.count_nonzero(block)),
            'at_v': at_v,
            'u0': round3(u_low), 'u1': round3(u_high), 'du': round3(u_high - u_low),
            'v0': at_v, 'v1': at_v,
        }
    raise common.CadError('BAD_ARGS', 'extent 需要 at_u（沿 u 的列）或 at_v（沿 v 的行）')


def do_coverage(mask, U, V, rule):
    np = _np()
    bounds = rule.get('u')
    u_start = float(bounds[0]) if isinstance(bounds, (list, tuple)) and bounds[0] is not None else float(np.min(U))
    u_end = float(bounds[1]) if isinstance(bounds, (list, tuple)) and bounds[1] is not None else float(np.max(U))
    step = float(rule.get('step') or 1.0)
    v_filter = rule.get('v_filter')
    selected = mask
    if isinstance(v_filter, (list, tuple)) and len(v_filter) == 2:
        if v_filter[0] is not None:
            selected = selected & (V >= float(v_filter[0]))
        if v_filter[1] is not None:
            selected = selected & (V <= float(v_filter[1]))
    columns = np.nonzero((U[0] >= u_start) & (U[0] <= u_end))[0]
    points = []
    gaps = []
    if columns.size:
        hit = selected[:, columns].any(axis=0)
        count = bin_count(u_start, u_end, step)
        for index in range(count):
            low = u_start + index * step
            high = low + step
            picks = (U[0][columns] >= low) & (U[0][columns] < high)
            if picks.sum() == 0:
                # 空箱（画面里没有这段）也要占位并计入断口：
                # 否则"缺一整段"这件事在结果里凭空消失（refkit 复核 P2-9）
                points.append([round((low + high) / 2.0, 3), None])
                if gaps and abs(gaps[-1][1] - low) < 1e-6:
                    gaps[-1][1] = round(high, 3)
                else:
                    gaps.append([round(low, 3), round(high, 3)])
                continue
            ratio = float(hit[picks].sum()) / float(picks.sum())
            points.append([round((low + high) / 2.0, 3), round(ratio, 3)])
            if ratio < 0.5:
                if gaps and abs(gaps[-1][1] - low) < 1e-6:
                    gaps[-1][1] = round(high, 3)
                else:
                    gaps.append([round(low, 3), round(high, 3)])
    ratios = [item[1] for item in points if item[1] is not None]
    return {
        'points': points,
        'min_ratio': round3(min(ratios)) if ratios else 0.0,
        'gaps': gaps,
        'u_range': [round(u_start, 3), round(u_end, 3)],
    }


MEASURE_FIELDS = {
    'bbox': ('u0', 'u1', 'v0', 'v1', 'du', 'dv', 'cu', 'cv'),
    'extent': ('u0', 'u1', 'v0', 'v1', 'du', 'dv'),
    'profile': ('n',),
    'coverage': ('min_ratio',),
}


def action_measure(spec):
    workspace, project, path = load_project(spec)
    frame_id = safe_id(spec.get('frame_id') or spec.get('frame') or '')
    if not frame_id:
        raise common.CadError('BAD_ARGS', 'measure 需要 frame_id')
    frame = project['frames'].get(frame_id)
    if not frame:
        raise common.CadError('REF_NOT_FOUND', '工程里没有帧 "' + frame_id + '"，已有：' + ', '.join(sorted(project['frames'])))
    Image, _draw, _font = _image()
    np = _np()
    items = spec.get('measures') or []
    if not isinstance(items, list) or not items:
        raise common.CadError('BAD_ARGS', 'measure 需要 measures 数组')
    with Image.open(frame['path']) as image:
        arr = np.asarray(image.convert('RGB')).astype('int32')
    U, V = frame_coords(frame, arr.shape[1], arr.shape[0])
    results = []
    warnings = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get('id'):
            # 契约 §3.2：每个量测条目必须有 id（自动生成会让目标带/差分对不上）
            raise common.CadError('BAD_ARGS', '每个量测条目都需要 id（第 %d 条缺）' % (len(results) + 1))
        measure_id = safe_id(item.get('id'))
        kind = str(item.get('kind') or 'bbox').lower()
        rule = resolve_mask_rule(project, spec, item)
        mask = build_mask(arr, rule, U, V)
        try:
            if kind == 'profile':
                result = do_profile(mask, U, V, item)
            elif kind == 'bbox':
                result = do_bbox(mask, U, V, item)
            elif kind == 'extent':
                result = do_extent(mask, U, V, item, frame)
            elif kind == 'coverage':
                result = do_coverage(mask, U, V, item)
            else:
                raise common.CadError('BAD_ARGS', '未知量测 kind：' + kind)
        except common.CadError:
            raise
        except Exception as error:
            raise common.CadError('REF_FAILED', '量测 ' + measure_id + ' 失败：' + str(error))
        record = {
            'id': measure_id,
            'frame': frame_id,
            'view': frame.get('view'),
            'kind': kind,
            'rule': {key: item.get(key) for key in ('mask', 'u', 'v', 'step', 'v_filter', 'roi', 'at_u', 'at_v', 'band') if item.get(key) is not None},
            'model_filter': str(item.get('model_filter') or ''),
            'compare': normalize_compare(item.get('compare')),
            'result': result,
            'at': common.now_iso(),
        }
        project['measures'][measure_id] = record
        if result.get('pixels') == 0 or (kind == 'profile' and not result.get('points')):
            warnings.append('量测 ' + measure_id + ' 没命中像素：检查掩膜阈值或 ROI')
        results.append(record)

        target_spec = item.get('target')
        if target_spec is None and spec.get('save_targets') is True:
            # 顶层开关：按"量测值 ± tol"自动固化，名字取 <id>_<字段>
            field = default_target_field(kind)
            value = result.get(field)
            if value is None:
                warnings.append('save_targets 跳过 ' + measure_id + '：结果里没有字段 ' + field)
            else:
                tol = num(spec.get('tol'), 'tol', 0.3)
                target_spec = {'name': measure_id + '_' + field, 'field': field,
                               'tol': tol, 'note': '来自 save_targets（' + frame_id + '/' + measure_id + '）'}
        if isinstance(target_spec, dict):
            target_name = safe_id(target_spec.get('name') or measure_id)
            field = str(target_spec.get('field') or default_target_field(kind))
            value = result.get(field)
            if value is None:
                warnings.append('目标带 ' + target_name + ' 的字段 ' + field + ' 在量测结果里不存在')
            else:
                tol = num(target_spec.get('tol'), 'tol', 0.3)
                entry = {
                    'name': target_name,
                    'part': str(target_spec.get('part') or ''),
                    'field': str(target_spec.get('model_field') or field),
                    'ref': [round3(float(value) - tol), round3(float(value) + tol)],
                    'unit': 'm',
                    'note': str(target_spec.get('note') or ('来自 ' + frame_id + '/' + measure_id)),
                    'source': {'frame': frame_id, 'measure': measure_id, 'field': field},
                }
                project['targets'] = [row for row in project['targets'] if row.get('name') != target_name]
                project['targets'].append(entry)
    save_project(path, project)
    summary_lines = ['量测完成：' + str(len(results)) + ' 项（工程 ' + project['id'] + '/' + frame_id + '）']
    for record in results:
        summary_lines.append('  ' + record['id'] + ' · ' + record['kind'] + ' · ' + describe_result(record['result']))
    if project['targets']:
        summary_lines.append('目标带：' + str(len(project['targets'])) + ' 条')
    summary_lines.extend(['警告：' + item for item in warnings])
    return {
        'ok': True,
        'project': project['id'],
        'frame': frame_id,
        'measures': results,
        'targets': project['targets'],
        'warnings': warnings,
        'summary': '\n'.join(summary_lines),
    }


def normalize_compare(value):
    """轮廓比较哪一边：vmax（上缘）/ vmin（下缘）。单色掩膜常常只可靠地抓住一边。"""
    if value is None:
        return ['vmax', 'vmin']
    if isinstance(value, str):
        value = [value]
    picked = []
    for item in value:
        text = str(item).strip().lower()
        if text in ('vmax', 'vmin') and text not in picked:
            picked.append(text)
    return picked or ['vmax', 'vmin']


def default_target_field(kind):
    if kind == 'bbox':
        return 'du'
    if kind == 'extent':
        return 'dv'
    return 'n'


def describe_result(result):
    if not isinstance(result, dict):
        return str(result)
    if result.get('points') is not None:
        points = result['points']
        if points and len(points[0]) == 3:
            highs = [item[2] for item in points]
            lows = [item[1] for item in points]
            return '点数 %d，v 范围 %.2f~%.2f' % (len(points), min(lows), max(highs))
        return '点数 %d，min_ratio %s' % (len(points), result.get('min_ratio'))
    if result.get('pixels') == 0:
        return '未命中像素'
    return 'u %.2f~%.2f，v %.2f~%.2f，Δ %.2f×%.2f' % (
        result.get('u0', 0), result.get('u1', 0), result.get('v0', 0), result.get('v1', 0),
        result.get('du', 0), result.get('dv', 0))


def frame_coords(frame, width, height):
    np = _np()
    up = axis_params(frame.get('u'), 'u')
    vp = axis_params(frame.get('v'), 'v')
    columns = np.arange(width, dtype='float64')
    rows = np.arange(height, dtype='float64')
    u_values = up['m0'] + (columns - up['px0']) * up['k']
    v_values = vp['m0'] + (rows - vp['px0']) * vp['k']
    U = np.repeat(u_values[None, :], height, axis=0)
    V = np.repeat(v_values[:, None], width, axis=1)
    return U, V

# --------------------------------------------------------------------------
# 网格叠图
# --------------------------------------------------------------------------
def part_color(index, name):
    lower = (name or '').lower()
    keyword_colors = [
        ('hull', (0.77, 0.17, 0.11)), ('bulwark', (0.77, 0.17, 0.11)),
        ('container', (0.07, 0.22, 0.48)), ('crane', (0.96, 0.72, 0.16)),
        ('a frame', (0.96, 0.72, 0.16)), ('prop', (0.96, 0.72, 0.16)),
        ('rescue', (0.98, 0.36, 0.05)), ('deck', (0.24, 0.30, 0.22)),
        ('window', (0.10, 0.13, 0.16)), ('porthole', (0.10, 0.13, 0.16)),
        ('pod', (0.10, 0.13, 0.16)), ('funnel', (0.86, 0.87, 0.88)),
        ('bridge', (0.90, 0.91, 0.92)), ('radome', (0.90, 0.91, 0.92)),
    ]
    for keyword, color in keyword_colors:
        if keyword in lower:
            return color
    return PART_COLORS[index % len(PART_COLORS)]


def view_basis(view):
    if view not in VIEWS:
        raise common.CadError('BAD_ARGS', '未知视图 "' + str(view) + '"，可用：' + ', '.join(VIEWS))
    u_axis, v_axis, d_axis = VIEWS[view]
    return AXIS_VECTORS[u_axis], AXIS_VECTORS[v_axis], AXIS_VECTORS[d_axis]


def load_stl(path, units='mm'):
    np = _np()
    if not os.path.isfile(path):
        raise common.CadError('REF_NOT_FOUND', 'STL 不存在：' + path)
    with open(path, 'rb') as handle:
        raw = handle.read()
    scale = 0.001 if str(units).lower() == 'mm' else 1.0
    if len(raw) >= 84:
        header = np.frombuffer(raw[0:84], dtype=np.uint8)
        count = int(np.frombuffer(header[80:84].tobytes(), dtype='<u4')[0])
        # 用 >= ：不少工具会在二进制 STL 尾部多写一个换行/NUL，严格相等会掉进 ASCII 分支报解析失败
        if len(raw) >= 84 + count * 50 and count > 0:
            record = np.dtype([('normal', '<f4', 3), ('vertex', '<f4', (3, 3)), ('attr', '<u2')])
            # raw 是 bytes：直接给 frombuffer（这里曾误写 .tobytes()，STL 分支因此从未跑通，
            # 直到 20 万面性能探针才暴露 —— 网格用例一直只走 mesh 分支）
            body = np.frombuffer(raw[84:84 + count * 50], dtype=record)
            return body['vertex'].astype('float64') * scale
    vertices = []
    text = raw.decode('utf-8', 'ignore')
    for match in re.finditer(r'vertex[ \t]+([-0-9.eE+]+)[ \t]+([-0-9.eE+]+)[ \t]+([-0-9.eE+]+)', text):
        vertices.append([float(match.group(1)), float(match.group(2)), float(match.group(3))])
    if not vertices or len(vertices) % 3:
        raise common.CadError('REF_FAILED', 'STL 解析失败（既不是二进制也不像 ASCII）：' + path)
    return np.array(vertices, dtype='float64').reshape(-1, 3, 3) * scale


def filter_groups_slice(groups, slice_spec):
    """按切片范围裁剪三角网格：只保留与该范围有交集的三角形。

    参考帧只拍到局部（艉部/艏部）时，整船模型叠上去会糊成一片；
    契约 §2 承诺 front/rear/section 可带 slice，这里实现它。
    """
    np = _np()
    axis = str(slice_spec.get('axis') or 'x').lower()
    index = {'x': 0, 'y': 1, 'z': 2}.get(axis)
    if index is None:
        raise common.CadError('BAD_ARGS', 'slice.axis 只能是 x/y/z')
    low = float(slice_spec.get('from'))
    high = float(slice_spec.get('to'))
    if high < low:
        low, high = high, low
    kept = []
    for name, tris in groups:
        if tris.size == 0:
            continue
        values = tris[:, :, index]
        keep = (values.max(axis=1) >= low) & (values.min(axis=1) <= high)
        if keep.any():
            kept.append((name, tris[keep]))
    return kept


def load_model_groups(spec, workspace, units='mm'):
    """模型来源优先级：mesh（带名字的三角网格）> stl（单色）。

    mesh 形如 {"names":[...], "tris":[[[[x,y,z],...3个点], ...], ...]}（每个 tris[i] 是一个部件的三角面）。
    也接受"扁平三角形列表"（每个元素就是一个三角形），自动整理成一组。
    """
    groups = []
    mesh = spec.get('mesh')
    if isinstance(mesh, str):
        path = resolve_input(workspace, mesh)
        with open(path, 'r', encoding='utf-8') as handle:
            mesh = json.load(handle)
    if isinstance(mesh, dict) and isinstance(mesh.get('tris'), list):
        np = _np()
        names = mesh.get('names') or []
        for index, tris in enumerate(mesh['tris']):
            if not tris:
                continue
            array = np.array(tris, dtype='float64')
            if array.ndim == 2 and array.shape == (3, 3):
                array = array.reshape(-1, 3, 3)
            if array.ndim != 3:
                continue
            name = names[index] if index < len(names) else 'part-%d' % index
            groups.append((str(name), array))
    stl = spec.get('stl')
    if stl:
        groups.append(('model', load_stl(resolve_input(workspace, stl), spec.get('stl_units') or units)))
    return [item for item in groups if item[1].size]


def normalize_parts(spec, workspace):
    parts = spec.get('parts')
    if isinstance(parts, str):
        path = resolve_input(workspace, parts)
        with open(path, 'r', encoding='utf-8') as handle:
            parts = json.load(handle)
    if parts is None and spec.get('report'):
        path = resolve_input(workspace, spec['report'])
        with open(path, 'r', encoding='utf-8') as handle:
            parts = json.load(handle)
    if isinstance(parts, dict):
        if isinstance(parts.get('bbox'), dict):
            parts = [{'name': name, 'bbox': box} for name, box in parts['bbox'].items()]
        elif isinstance(parts.get('parts'), list):
            parts = parts['parts']
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


def axis_span(bbox, axis):
    """取 bbox 在某轴上的 (low, high)；轴名允许带负号（rear 视图的 u = -y）。"""
    text = str(axis).strip().lower()
    sign = -1.0 if text.startswith('-') else 1.0
    index = {'x': 0, 'y': 1, 'z': 2}.get(text.lstrip('-'))
    if index is None:
        raise common.CadError('BAD_ARGS', '轴名必须是 x/y/z（可带负号）：' + str(axis))
    low, high = bbox[index], bbox[index + 3]
    if sign < 0:
        return -high, -low
    return low, high


def render_groups(groups, view, scale, margin, background=248, max_tris=400000):
    np = _np()
    Image, _draw_mod, _font = _image()
    ImageDraw = _draw_mod
    bu, bv, bd = [np.array(vector, dtype='float64') for vector in view_basis(view)]
    total = sum(int(len(tris)) for _name, tris in groups)
    if total == 0:
        raise common.CadError('REF_FAILED', '没有三角面可渲染：给 stl 或 mesh')
    if total > max_tris:
        stride = int(math.ceil(total / float(max_tris)))
        groups = [(name, tris[::stride]) for name, tris in groups]
    corners = []
    for _name, tris in groups:
        flat = tris.reshape(-1, 3)
        corners.append(np.stack([flat @ bu, flat @ bv], axis=1))
    points = np.concatenate(corners, axis=0)
    u_min = float(points[:, 0].min())
    u_max = float(points[:, 0].max())
    v_min = float(points[:, 1].min())
    v_max = float(points[:, 1].max())
    width = int(math.ceil((u_max - u_min) * scale)) + 2 * margin
    height = int(math.ceil((v_max - v_min) * scale)) + 2 * margin
    width = max(240, min(6000, width))
    height = max(180, min(6000, height))
    canvas = Image.new('RGB', (width, height), (background, background, background))
    draw = ImageDraw.Draw(canvas)
    light = np.array([-0.35, -0.52, 0.78])
    light = light / np.linalg.norm(light)

    def to_px(u_values, v_values):
        return (margin + (u_values - u_min) * scale, margin + (v_max - v_values) * scale)

    for index, (name, tris) in enumerate(groups):
        base = np.array(part_color(index, name), dtype='float64')
        flat = tris.reshape(-1, 3)
        pu = (flat @ bu).reshape(-1, 3)
        pv = (flat @ bv).reshape(-1, 3)
        pd = (flat @ bd).reshape(-1, 3)
        edge1 = tris[:, 1, :] - tris[:, 0, :]
        edge2 = tris[:, 2, :] - tris[:, 0, :]
        normals = np.cross(edge1, edge2)
        lengths = np.linalg.norm(normals, axis=1)
        lengths[lengths == 0] = 1.0
        normals = normals / lengths[:, None]
        shade = 0.60 + 0.40 * np.abs(normals @ light)
        order = np.argsort(pd.mean(axis=1))
        for row in order:
            xs, ys = to_px(pu[row], pv[row])
            color = tuple(int(max(0, min(255, base[channel] * shade[row] * 255))) for channel in range(3))
            draw.polygon([(float(xs[0]), float(ys[0])), (float(xs[1]), float(ys[1])), (float(xs[2]), float(ys[2]))], fill=color)
    return canvas, {
        'u_min': round3(u_min), 'u_max': round3(u_max),
        'v_min': round3(v_min), 'v_max': round3(v_max),
        'scale_px_per_m': round3(scale), 'margin_px': margin,
        'width': width, 'height': height,
        'triangles': int(total),
    }


def cell_code(u_value, v_value, grid):
    u_step = float(grid.get('u_step') or 5.0)
    v_step = float(grid.get('v_step') or 5.0)
    u_origin = float(grid.get('u_origin') or 0.0)
    v_origin = float(grid.get('v_origin') or 0.0)
    column = int(math.floor((float(u_value) - u_origin) / u_step))
    row = int(math.floor((float(v_value) - v_origin) / v_step))
    if column < 0 or row + 1 < 0:
        # 越界格号必须自解释：'C-13' 既能读成"列 C-1 行 3"也能读成"列 C 行 13"（复核 P2-12）
        return 'N%dR%d' % (abs(column), abs(row + 1))
    if 0 <= column < len(COL_LETTERS):
        letter = COL_LETTERS[column]
    else:
        letter = 'C%d' % column
    return '%s%d' % (letter, row + 1)


def cells_of_bbox(bbox, grid, limit=24):
    """零件覆盖到的全部格号（按列/行区间遍历，不再只取 4 角 —— 中段会被跨过）。"""
    u_lo, u_hi = axis_span(bbox, grid.get('u_axis') or 'x')
    v_lo, v_hi = axis_span(bbox, grid.get('v_axis') or 'z')
    u_step = float(grid.get('u_step') or 5.0)
    v_step = float(grid.get('v_step') or 5.0)
    u_origin = float(grid.get('u_origin') or 0.0)
    v_origin = float(grid.get('v_origin') or 0.0)
    cell = cell_code((u_lo + u_hi) / 2.0, (v_lo + v_hi) / 2.0, grid)
    first_col = int(math.floor((u_lo - u_origin) / u_step))
    last_col = int(math.floor((u_hi - u_origin) / u_step))
    first_row = int(math.floor((v_lo - v_origin) / v_step))
    last_row = int(math.floor((v_hi - v_origin) / v_step))
    touched = []
    for column in range(first_col, last_col + 1):
        for row in range(first_row, last_row + 1):
            code = cell_code(u_origin + (column + 0.5) * u_step, v_origin + (row + 0.5) * v_step, grid)
            if code not in touched:
                touched.append(code)
            if len(touched) >= limit:
                return cell, touched
    return cell, touched or [cell]


def annotate_model(canvas, transform, grid, parts, label_parts, title):
    _Image, ImageDraw, _font = _image()
    draw = ImageDraw.Draw(canvas, 'RGBA')
    font = load_font(13)
    small = load_font(11)
    u_step = float(grid.get('u_step') or 5.0)
    v_step = float(grid.get('v_step') or 5.0)
    u_origin = float(grid.get('u_origin') or 0.0)
    v_origin = float(grid.get('v_origin') or 0.0)
    scale = transform['scale_px_per_m']
    margin = transform['margin_px']
    u_min = transform['u_min']
    v_max = transform['v_max']

    def px(u_value):
        return margin + (u_value - u_min) * scale

    def py(v_value):
        return margin + (v_max - v_value) * scale

    start_u = int(math.floor((u_min - u_origin) / u_step)) - 1
    end_u = int(math.ceil((transform['u_max'] - u_origin) / u_step)) + 1
    for index in range(start_u, end_u + 1):
        value = u_origin + index * u_step
        x = px(value)
        if x < 0 or x > canvas.width:
            continue
        draw.line([(x, 0), (x, canvas.height)], fill=(0, 120, 200, 110), width=1)
        label = (COL_LETTERS[index] if 0 <= index < len(COL_LETTERS) else 'C%d' % index) + ' %g' % value
        # 列标签放在顶部留白带的下沿，别和标题抢同一行（复核 P2-18）
        draw.text((x + 2, 4), label, fill=(0, 90, 170, 255), font=small)
    start_v = int(math.floor((transform['v_min'] - v_origin) / v_step)) - 1
    end_v = int(math.ceil((v_max - v_origin) / v_step)) + 1
    for index in range(start_v, end_v + 1):
        value = v_origin + index * v_step
        y = py(value)
        if y < 0 or y > canvas.height:
            continue
        draw.line([(0, y), (canvas.width, y)], fill=(0, 120, 200, 110) if index % 2 else (0, 120, 200, 180), width=1)
        draw.text((2, y - 14), '%d %g' % (index + 1, value), fill=(0, 90, 170, 255), font=small)
    for index, part in enumerate(parts):
        box = part.get('bbox')
        if not box:
            continue
        u_lo, u_hi = axis_span(box, grid.get('u_axis') or 'x')
        v_lo, v_hi = axis_span(box, grid.get('v_axis') or 'z')
        color = part_color(index, part.get('name'))
        stroke = tuple(int(value * 255) for value in color) + (150,)
        draw.rectangle([px(u_lo), py(v_hi), px(u_hi), py(v_lo)], outline=stroke, width=1)
        if label_parts:
            text = str(part.get('name'))[:24]
            draw.text((px(u_lo) + 2, py(v_hi) - 12), text, fill=(20, 20, 20, 220), font=small)
    # 标题画在底部留白带：顶部留给列标签，左边留给行标签，互不遮挡（复核 P2-18）
    text = str(title)
    draw.text((margin, canvas.height - 18), text, fill=(30, 30, 30, 255), font=font)
    return canvas


def render_part_boxes(parts, view, scale, margin=46, background=248):
    """没有三角网格时只画部件包围盒：至少能读格号、看谁在哪一格。"""
    np = _np()
    Image, _draw_mod, _font = _image()
    ImageDraw = _draw_mod
    bu, bv, _bd = [np.array(vector, dtype='float64') for vector in view_basis(view)]
    u_values = []
    v_values = []
    for part in parts:
        box = part['bbox']
        for x in (box[0], box[3]):
            for y in (box[1], box[4]):
                for z in (box[2], box[5]):
                    point = np.array([x, y, z], dtype='float64')
                    u_values.append(float(point @ bu))
                    v_values.append(float(point @ bv))
    if not u_values:
        raise common.CadError('REF_FAILED', '没有可画的包围盒')
    u_min = min(u_values)
    u_max = max(u_values)
    v_min = min(v_values)
    v_max = max(v_values)
    width = max(240, min(6000, int(math.ceil((u_max - u_min) * scale)) + 2 * margin))
    height = max(180, min(6000, int(math.ceil((v_max - v_min) * scale)) + 2 * margin))
    canvas = Image.new('RGB', (width, height), (background, background, background))
    ImageDraw.Draw(canvas)
    return canvas, {
        'u_min': round3(u_min), 'u_max': round3(u_max), 'v_min': round3(v_min), 'v_max': round3(v_max),
        'scale_px_per_m': round3(scale), 'margin_px': margin, 'width': width, 'height': height,
        'triangles': 0, 'mode': 'boxes',
    }


def view_grid(grid, view):
    """每个视图的横/纵轴不同（side=x/z、top=x/y、front/rear=y/z）。

    原来所有视图共用工程里的 u_axis/v_axis（默认 x/z），俯视图/正视图的
    零件框与格号会按 x/z 画到 y/z 的画布上，直接错位（复核 P1-2）。
    """
    derived = dict(grid)
    if view in VIEWS:
        u_axis, v_axis, _depth = VIEWS[view]
        derived['u_axis'] = u_axis
        derived['v_axis'] = v_axis
    return derived


def load_font(size):
    _Image, _draw, ImageFont = _image()
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/arial.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def image_stats(path):
    np = _np()
    Image, _draw, _font = _image()
    with Image.open(path) as image:
        arr = np.asarray(image.convert('RGB')).astype('float64')
    stddev = float(arr.std())
    unique = int(len(np.unique(arr.reshape(-1, 3), axis=0)))
    white = float((arr > 245).all(axis=2).mean())
    return {
        'path': path,
        'width': int(arr.shape[1]),
        'height': int(arr.shape[0]),
        'stddev': round3(stddev),
        'unique_colors': unique,
        'white_ratio': round3(white),
        'blank': bool(stddev < 2.0 or unique < 8),
    }


def overlay_frame(frame, out_path, grid, zones=None, scale_factor=1.0, label_frames=True):
    np = _np()
    Image, ImageDraw, _font = _image()
    with Image.open(frame['path']) as source:
        image = source.convert('RGB')
    if scale_factor and abs(scale_factor - 1.0) > 1e-6:
        image = image.resize((int(image.width * scale_factor), int(image.height * scale_factor)), Image.LANCZOS)
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, 'RGBA')
    font = load_font(14)
    small = load_font(12)
    up = axis_params(frame.get('u'), 'u')
    vp = axis_params(frame.get('v'), 'v')
    u_step = float(grid.get('u_step') or 5.0)
    v_step = float(grid.get('v_step') or 5.0)
    u_min = axis_m(up, 0)
    u_max = axis_m(up, image.width)
    v_min = axis_m(vp, image.height)
    v_max = axis_m(vp, 0)
    u_origin = float(grid.get('u_origin') or 0.0)
    v_origin = float(grid.get('v_origin') or 0.0)
    index = int(math.floor((min(u_min, u_max) - u_origin) / u_step)) - 1
    while u_origin + index * u_step <= max(u_min, u_max) + u_step:
        value = u_origin + index * u_step
        x = axis_px(up, value) * scale_factor
        if 0 <= x <= image.width:
            draw.line([(x, 0), (x, image.height)], fill=(0, 120, 200, 150), width=1)
            label = (COL_LETTERS[index] if 0 <= index < len(COL_LETTERS) else 'C%d' % index) + ' %g' % value
            draw.text((x + 2, 2), label, fill=(0, 70, 150, 255), font=small)
        index += 1
    index = int(math.floor((min(v_min, v_max) - v_origin) / v_step)) - 1
    while v_origin + index * v_step <= max(v_min, v_max) + v_step:
        value = v_origin + index * v_step
        y = axis_px(vp, value) * scale_factor
        if 0 <= y <= image.height:
            draw.line([(0, y), (image.width, y)], fill=(0, 120, 200, 150) if index % 2 else (0, 120, 200, 210), width=1)
            draw.text((3, y - 14), '%d %g' % (index + 1, value), fill=(0, 70, 150, 255), font=small)
        index += 1
    for zone in zones or []:
        code = str(zone.get('code') or '')
        label = str(zone.get('label') or '')
        x0 = axis_px(up, float(zone.get('u0'))) * scale_factor
        x1 = axis_px(up, float(zone.get('u1'))) * scale_factor
        y0 = axis_px(vp, float(zone.get('v1'))) * scale_factor
        y1 = axis_px(vp, float(zone.get('v0'))) * scale_factor
        fill = zone.get('fill') or [255, 60, 60, 40]
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                       fill=tuple(fill), outline=(200, 0, 0, 220), width=2)
        if code or label:
            text = (code + ' ' + label).strip()
            draw.text((min(x0, x1) + 3, min(y0, y1) + 2), text, fill=(140, 0, 0, 255), font=font)
    if label_frames:
        draw.text((6, image.height - 18), str(frame.get('id') or '') + ' · ' + str(frame.get('view') or ''),
                  fill=(0, 0, 0, 220), font=font)
    merged = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
    merged.save(out_path)
    return out_path


def montage(paths, out_path, labels=None, columns=2, cell=(760, 460)):
    Image, ImageDraw, _font = _image()
    if not paths:
        return None
    rows = int(math.ceil(len(paths) / float(columns)))
    canvas = Image.new('RGB', (cell[0] * columns, (cell[1] + 24) * rows), 'white')
    draw = ImageDraw.Draw(canvas)
    font = load_font(15)
    for index, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert('RGB')
        image.thumbnail((cell[0] - 12, cell[1] - 12))
        x = cell[0] * (index % columns) + 6
        y = (cell[1] + 24) * (index // columns) + 22
        canvas.paste(image, (x, y))
        text = labels[index] if labels and index < len(labels) else str(path).split(os.sep)[-1]
        draw.text((x, y - 18), text, fill=(0, 0, 0), font=font)
    canvas.save(out_path)
    return out_path


def action_render(spec):
    workspace, project, path = load_project(spec, require=False)
    out_dir = out_dir_of(spec, workspace, project)
    basename = common.safe_basename(spec.get('basename'), safe_id(project.get('id')))
    grid = dict(project.get('grid') or {})
    if isinstance(spec.get('grid'), dict):
        grid.update(spec['grid'])
    grid.setdefault('u_axis', str((project.get('axes') or {}).get('u') or 'x'))
    grid.setdefault('v_axis', str((project.get('axes') or {}).get('v') or 'z'))
    parts = normalize_parts(spec, workspace)
    groups = load_model_groups(spec, workspace)
    if not parts and not groups:
        raise common.CadError('BAD_ARGS', 'render 需要模型：parts（逐件 bbox）或 stl/mesh（三角网格）')
    views = spec.get('views') or ['side', 'top']
    if isinstance(views, str):
        views = [views]
    slice_spec = spec.get('slice') if isinstance(spec.get('slice'), dict) else None
    if not slice_spec:
        # calibrate 时写在帧上的 slice 也要生效，否则"按帧切片"是死字段（复核 P2-8）
        for frame in (project.get('frames') or {}).values():
            if isinstance(frame.get('slice'), dict):
                slice_spec = frame['slice']
                break
    if slice_spec and groups:
        groups = filter_groups_slice(groups, slice_spec)
    if slice_spec and parts:
        axis = str(slice_spec.get('axis') or 'x').lower()
        low = float(slice_spec.get('from'))
        high = float(slice_spec.get('to'))
        if high < low:
            low, high = high, low
        index = {'x': 0, 'y': 1, 'z': 2}.get(axis)
        if index is None:
            raise common.CadError('BAD_ARGS', 'slice.axis 只能是 x/y/z')
        kept = []
        for part in parts:
            part_low, part_high = part['bbox'][index], part['bbox'][index + 3]
            if part_high >= low and part_low <= high:
                kept.append(part)
        parts = kept
    unknown = [view for view in views if view not in VIEWS]
    if unknown:
        # 先校验再写盘：否则 side 已经落盘、第二个视图才报错，留下半套产物（复核 P2-17）
        raise common.CadError('BAD_ARGS', '未知视图：' + ', '.join(unknown) + '（可用：' + ', '.join(VIEWS) + '）')
    if 'section' in views and not slice_spec:
        raise common.CadError('BAD_ARGS', 'section 视图需要 slice:{axis,from,to}（例如吊舱剖面）')
    scale = float(spec.get('scale') or 0)
    label_parts = bool(spec.get('label_parts'))
    zones = spec.get('zones') or []
    images = []
    model_files = []
    if groups or parts:
        for view in views:
            if groups:
                canvas, transform = render_groups(groups, view, scale or 22.0, int(spec.get('margin') or 46))
            else:
                # 只有逐件 bbox 时退化成"零件框 + 网格"：读格号仍然有效，但不代表真实形状
                canvas, transform = render_part_boxes(parts, view, scale or 16.0, int(spec.get('margin') or 46))
            derived = view_grid(grid, view)
            annotate_model(canvas, transform, derived, parts, label_parts,
                           '%s · %s   u=%s v=%s   %g m grid' % (
                               basename, view, derived['u_axis'], derived['v_axis'],
                               float(derived.get('u_step') or 5)))
            target = os.path.join(out_dir, '%s_model_%s.png' % (basename, view))
            canvas.save(target)
            model_files.append(target)
            images.append(image_stats(target))
    ref_files = []
    for frame_id, frame in sorted((project.get('frames') or {}).items()):
        target = os.path.join(out_dir, '%s_ref_%s.png' % (basename, frame_id))
        overlay_frame(frame, target, grid, zones=zones, scale_factor=float(spec.get('ref_scale') or 1.6))
        ref_files.append(target)
        images.append(image_stats(target))
    zone_file = None
    if zones and project.get('frames'):
        first_frame_id = sorted(project['frames'])[0]
        frame = project['frames'][first_frame_id]
        zone_file = os.path.join(out_dir, '%s_zones.png' % basename)
        overlay_frame(frame, zone_file, grid, zones=zones, scale_factor=float(spec.get('ref_scale') or 1.6))
        images.append(image_stats(zone_file))
    sheet = None
    if len(model_files) + len(ref_files) > 1:
        sheet = os.path.join(out_dir, '%s_views.png' % basename)
        labels = [str(item).split(os.sep)[-1].replace(basename + '_', '') for item in model_files + ref_files]
        montage(model_files + ref_files, sheet, labels=labels)
        images.append(image_stats(sheet))
    cells = {}
    cells_by_view = {}
    if parts:
        for part in parts:
            cell, touched = cells_of_bbox(part['bbox'], grid)
            cells[part['name']] = {'cell': cell, 'cells': touched, 'bbox': [round3(value) for value in part['bbox']]}
        for view in views:
            derived = view_grid(grid, view)
            bucket = {}
            for part in parts:
                cell, touched = cells_of_bbox(part['bbox'], derived)
                bucket[part['name']] = {'cell': cell, 'cells': touched}
            cells_by_view[view] = {'u_axis': derived['u_axis'], 'v_axis': derived['v_axis'], 'parts': bucket}
    cells_path = None
    if cells:
        cells_path = os.path.join(out_dir, '%s_cells.json' % basename)
        with open(cells_path, 'w', encoding='utf-8') as handle:
            json.dump({'grid': grid, 'parts': cells}, handle, ensure_ascii=False, indent=1)
    blanks = [item for item in images if item.get('blank')]
    summary = [
        '网格叠图：' + str(len(model_files)) + ' 张模型视图、' + str(len(ref_files)) + ' 张参考帧',
        '格号：列 ' + str(grid['u_axis']) + ' 每 %g m 一个字母，行 ' % float(grid.get('u_step') or 5) +
        str(grid['v_axis']) + ' 每 %g m 一个数字' % float(grid.get('v_step') or 5),
        sheet or '',
        cells_path or '',
        ('警告：%d 张图疑似空白' % len(blanks)) if blanks else '',
    ]
    return {
        'ok': True,
        'project': project.get('id'),
        'out_dir': out_dir,
        'images': images,
        'model_views': model_files,
        'ref_views': ref_files,
        'zones_image': zone_file,
        'contact_sheet': sheet,
        'cells_path': cells_path,
        'cells': cells,
        'cells_by_view': cells_by_view,
        'summary': '\n'.join([line for line in summary if line]),
    }

# --------------------------------------------------------------------------
# 差分
# --------------------------------------------------------------------------
def axis_index(axis):
    index = {'x': 0, 'y': 1, 'z': 2}.get(str(axis).lower())
    if index is None:
        raise common.CadError('BAD_ARGS', '轴名必须是 x/y/z：' + str(axis))
    return index


def field_value(bbox, field):
    x0, y0, z0, x1, y1, z1 = [float(value) for value in bbox]
    values = {
        'x0': x0, 'y0': y0, 'z0': z0, 'x1': x1, 'y1': y1, 'z1': z1,
        'dx': x1 - x0, 'dy': y1 - y0, 'dz': z1 - z0,
        'cx': (x0 + x1) / 2.0, 'cy': (y0 + y1) / 2.0, 'cz': (z0 + z1) / 2.0,
        # 字面语义：zmax = 最大 z（= z1），ymax = 最大 y；绝对值另给 zabs/ymax_abs。
        # 原来 zmax 返回 max(|z0|,|z1|)，水下件会给出正数（复核 P2-14）。
        'zmax': z1, 'zmin': z0, 'ymax': y1, 'ymin': y0,
        'zabs': max(abs(z0), abs(z1)), 'yabs': max(abs(y0), abs(y1)),
    }
    if field not in values:
        raise common.CadError('BAD_ARGS', '未知字段 "' + str(field) + '"，可用：' + ', '.join(sorted(values)))
    return values[field]


def union_bbox(boxes):
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes), min(box[2] for box in boxes),
        max(box[3] for box in boxes), max(box[4] for box in boxes), max(box[5] for box in boxes),
    ]


def name_matches(name, pattern):
    text = str(pattern or '').strip()
    if not text:
        return True
    if text.lower().startswith('re:'):
        return re.search(text[3:], str(name), re.IGNORECASE) is not None
    return text.lower() in str(name).lower()


def match_parts(parts, pattern):
    text = str(pattern or '').strip()
    if not text:
        return list(parts)
    return [part for part in parts if name_matches(part['name'], text)]


def build_target_row(target, parts, grid):
    name = str(target.get('name') or '')
    field = str(target.get('field') or 'dx')
    band = target.get('ref')
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        raise common.CadError('BAD_ARGS', '目标 "' + name + '" 的 ref 需要 [下限, 上限]')
    low = float(band[0])
    high = float(band[1])
    if high < low:
        low, high = high, low
    pattern = str(target.get('part') or '')
    selected = match_parts(parts, pattern)
    # 名字精确等于 pattern 的部件优先，避免 "bridge" 把 bridge roof / bridge glazing 一起并进来
    # （v0.4 实机踩过：驾驶室长度被并成 15.0 m 而参考是 12.4）。
    exact = [part for part in parts if part['name'].strip().lower() == pattern.strip().lower()]
    if exact:
        selected = exact
    elif str(target.get('part_mode') or '').lower() == 'first' and selected:
        selected = [min(selected, key=lambda part: part['name'])]
    box = union_bbox([part['bbox'] for part in selected]) if selected else None
    if box is None:
        return {
            'cell': None, 'name': name, 'part': target.get('part') or '', 'field': field,
            'ref': [round3(low), round3(high)], 'ref_note': target.get('note') or '',
            'model': None, 'delta': None, 'margin': None, 'verdict': 'MISSING',
            'hint': '模型里找不到匹配零件 "' + str(target.get('part')) + '"，先确认零件名',
        }
    value = field_value(box, field)
    width = high - low
    if low <= value <= high:
        delta = 0.0
        margin = min(value - low, high - value)
        verdict = 'PASS'
    else:
        delta = value - high if value > high else value - low
        margin = 0.0
        warn_factor = target.get('warn_factor')
        warn_factor = 0.5 if warn_factor is None else float(warn_factor)
        # 契约：|Δ| ≤ warn_factor × tol，tol = 半带宽。原来乘整带宽，阈值放宽一倍（复核 P1-5）
        verdict = 'WARN' if abs(delta) <= warn_factor * max(width / 2.0, 1e-6) else 'FAIL'
    cell = cell_code((box[0] + box[3]) / 2.0, (box[2] + box[5]) / 2.0, grid)
    hint = ''
    if verdict != 'PASS':
        direction = '偏大' if delta > 0 else '偏小'
        hint = '实测 %s 为 %.3f，%s %.3f m（带 %g~%g）；改 %s 对应常量，或核对参考值' % (
            field, value, direction, abs(delta), low, high, target.get('part') or '该零件')
    return {
        'cell': cell, 'name': name, 'part': target.get('part') or '', 'field': field,
        'ref': [round3(low), round3(high)], 'ref_note': target.get('note') or '',
        'model': round3(value), 'delta': round3(delta), 'margin': round3(margin),
        'verdict': verdict, 'hint': hint, 'bbox': [round3(item) for item in box],
        'matched': [part['name'] for part in selected][:8],
    }


def sample_profile_points(groups, u_index, v_index, step, max_tris=20000):
    """把三角网格采样成 (u, v) 点云用于轮廓包络。

    只取顶点会在"大平面、顶点都在两端"的模型上出现空洞（v0.4 合成用例踩过：
    两个盒子的中间段没有任何顶点，轮廓差分整段漏报）。所以三角形少时沿三条边
    均匀插值；网格密（> max_tris）时顶点已经足够密，只取顶点以控内存。
    """
    np = _np()
    total = sum(int(len(tris)) for _name, tris in groups)
    dense = total > max_tris
    us = []
    vs = []
    for _name, tris in groups:
        if tris.size == 0:
            continue
        u = tris[:, :, u_index]
        v = tris[:, :, v_index]
        us.append(u.reshape(-1))
        vs.append(v.reshape(-1))
        if dense:
            continue
        for first, second in ((0, 1), (1, 2), (2, 0)):
            du = u[:, second] - u[:, first]
            dv = v[:, second] - v[:, first]
            need = np.ceil(np.abs(du) / max(step * 0.5, 1e-6)).astype(int)
            if need.size == 0:
                continue
            # 上限 512：step 很小（0.5 m）时长边 80 m 需要 >160 个采样点，
            # 卡在 64 会让"模型与参考完全一致"也报 MISSING（refkit 复核 P2-13）
            samples = int(max(1, min(int(need.max()), 512)))
            fraction = np.linspace(0.0, 1.0, samples + 1)[None, :]
            us.append((u[:, first][:, None] + du[:, None] * fraction).reshape(-1))
            vs.append((v[:, first][:, None] + dv[:, None] * fraction).reshape(-1))
    if not us:
        return None, None
    return np.concatenate(us), np.concatenate(vs)


def model_profile(parts, groups, u_axis, v_axis, u_start, u_end, step):
    np = _np()
    u_index = axis_index(u_axis)
    v_index = axis_index(v_axis)
    bins = {}
    counts = bin_count(u_start, u_end, step)
    if groups:
        u_values, v_values = sample_profile_points(groups, u_index, v_index, step)
        if u_values is None:
            return bins, 'vertices'
        for index in range(counts):
            low = u_start + index * step
            high = low + step
            picks = (u_values >= low) & (u_values < high)
            if not picks.any():
                continue
            bins[round((low + high) / 2.0, 3)] = (float(v_values[picks].min()), float(v_values[picks].max()))
        return bins, 'sampled'
    for part in parts:
        box = part['bbox']
        part_u0, part_u1 = axis_span(box, u_axis)
        part_v0, part_v1 = axis_span(box, v_axis)
        for index in range(counts):
            low = u_start + index * step
            high = low + step
            if part_u1 < low or part_u0 > high:
                continue
            key = round((low + high) / 2.0, 3)
            current = bins.get(key)
            if current is None:
                bins[key] = (part_v0, part_v1)
            else:
                bins[key] = (min(current[0], part_v0), max(current[1], part_v1))
    return bins, 'bbox'


def profile_rows_for(project, parts, groups, grid, tolerance, only=None):
    u_axis = str((project.get('axes') or {}).get('u') or 'x')
    v_axis = str((project.get('axes') or {}).get('v') or 'z')
    rows = []
    wanted = [str(item) for item in only] if isinstance(only, (list, tuple)) and only else None
    for measure_id, record in sorted((project.get('measures') or {}).items()):
        if record.get('kind') != 'profile':
            continue
        if wanted is not None and measure_id not in wanted:
            continue
        result = record.get('result') or {}
        points = result.get('points') or []
        if not points:
            continue
        step = float(result.get('step') or 0.5)
        u_range = result.get('u_range') or [points[0][0], points[-1][0]]
        u_start = float(u_range[0])
        u_end = float(u_range[1])
        # 参考轮廓往往是"船体红壳"这类单色掩膜，模型侧必须能限定到对应部件，
        # 否则上层建筑会被算进船体轮廓，得到假的 Δ（v0.4 实测踩过）。
        edges = normalize_compare(record.get('compare'))
        pattern = str(record.get('model_filter') or '')
        selected_parts = match_parts(parts, pattern) if pattern else list(parts)
        selected_groups = groups
        filtered_groups = []
        if pattern and groups:
            filtered_groups = [item for item in groups if name_matches(item[0], pattern)]
            if not filtered_groups:
                # 过滤不到任何部件时必须报 MISSING，绝不能"退回整网"——
                # 那会把上层建筑算进船体轮廓，得到一个假的 Δ（复核 P1-4）
                for point in points:
                    rows.append({
                        'cell': cell_code(point[0], point[2], grid),
                        'measure': measure_id,
                        'frame': record.get('frame'),
                        'u_range': [round3(point[0] - step / 2.0), round3(point[0] + step / 2.0)],
                        'ref': {'vmax': round3(point[2]), 'vmin': round3(point[1])},
                        'model': {'vmax': None, 'vmin': None},
                        'delta_vmax': None, 'delta_vmin': None, 'compare': edges,
                        'verdict': 'MISSING', 'method': 'none',
                        'hint': '模型里没有匹配 model_filter "' + pattern + '" 的部件：确认命名，或改过滤条件',
                    })
                continue
            selected_groups = filtered_groups
        if pattern and parts and not selected_parts:
            for point in points:
                rows.append({
                    'cell': cell_code(point[0], point[2], grid),
                    'measure': measure_id, 'frame': record.get('frame'),
                    'u_range': [round3(point[0] - step / 2.0), round3(point[0] + step / 2.0)],
                    'ref': {'vmax': round3(point[2]), 'vmin': round3(point[1])},
                    'model': {'vmax': None, 'vmin': None},
                    'delta_vmax': None, 'delta_vmin': None, 'compare': edges,
                    'verdict': 'MISSING', 'method': 'none',
                    'hint': '模型里没有匹配 model_filter "' + pattern + '" 的部件',
                })
            continue
        bins, method = model_profile(selected_parts, selected_groups, u_axis, v_axis, u_start, u_end, step)
        if not bins:
            for point in points:
                rows.append({
                    'cell': cell_code(point[0], point[2], grid),
                    'measure': measure_id, 'frame': record.get('frame'),
                    'u_range': [round3(point[0] - step / 2.0), round3(point[0] + step / 2.0)],
                    'ref': {'vmax': round3(point[2]), 'vmin': round3(point[1])},
                    'model': {'vmax': None, 'vmin': None},
                    'delta_vmax': None, 'delta_vmin': None, 'compare': edges,
                    'verdict': 'MISSING', 'method': method,
                    'hint': '模型侧没有可用于轮廓的几何（检查 mesh/stl 是否为空）',
                })
            continue
        groups_by_cell = {}
        for point in points:
            u_value, v_low, v_high = point[0], point[1], point[2]
            nearest = min(bins.keys(), key=lambda key: abs(key - u_value)) if bins else None
            if nearest is None or abs(nearest - u_value) > max(step * 1.5, 0.5):
                rows.append({
                    'cell': cell_code(u_value, v_high, grid),
                    'measure': measure_id,
                    'frame': record.get('frame'),
                    'u_range': [round3(u_value - step / 2.0), round3(u_value + step / 2.0)],
                    'ref': {'vmax': round3(v_high), 'vmin': round3(v_low)},
                    'model': {'vmax': None, 'vmin': None},
                    'delta_vmax': None,
                    'delta_vmin': None,
                    'verdict': 'MISSING',
                    'method': method,
                    'hint': '模型在该 u 段没有任何几何（参考有 v %.2f~%.2f），多半是部件缺失或完全错位' % (
                        v_low, v_high),
                })
                continue
            model_low, model_high = bins[nearest]
            cell = cell_code(u_value, v_high, grid)
            slot = groups_by_cell.get(cell)
            if slot is None:
                groups_by_cell[cell] = {
                    'ref_vmin': v_low, 'ref_vmax': v_high,
                    'model_vmin': model_low, 'model_vmax': model_high,
                    'u_min': u_value, 'u_max': u_value,
                }
            else:
                slot['ref_vmin'] = min(slot['ref_vmin'], v_low)
                slot['ref_vmax'] = max(slot['ref_vmax'], v_high)
                slot['model_vmin'] = min(slot['model_vmin'], model_low)
                slot['model_vmax'] = max(slot['model_vmax'], model_high)
                slot['u_min'] = min(slot['u_min'], u_value)
                slot['u_max'] = max(slot['u_max'], u_value)
        for cell, entry in sorted(groups_by_cell.items()):
            delta_high = entry['model_vmax'] - entry['ref_vmax']
            delta_low = entry['model_vmin'] - entry['ref_vmin']
            checked = []
            if 'vmax' in edges:
                checked.append(abs(delta_high))
            if 'vmin' in edges:
                checked.append(abs(delta_low))
            worst = max(checked) if checked else 0.0
            verdict = 'PASS'
            if worst > tolerance:
                verdict = 'FAIL'
            elif worst > tolerance * 0.5:
                verdict = 'WARN'
            rows.append({
                'cell': cell,
                'measure': measure_id,
                'frame': record.get('frame'),
                'u_range': [round3(entry['u_min']), round3(entry['u_max'])],
                'ref': {'vmax': round3(entry['ref_vmax']), 'vmin': round3(entry['ref_vmin'])},
                'model': {'vmax': round3(entry['model_vmax']), 'vmin': round3(entry['model_vmin'])},
                'delta_vmax': round3(delta_high) if 'vmax' in edges else None,
                'delta_vmin': round3(delta_low) if 'vmin' in edges else None,
                'compare': edges,
                'verdict': verdict,
                'method': method,
                'hint': '' if verdict == 'PASS' else '本格上部轮廓差 %.2f m / 下部差 %.2f m（来源 %s）' % (
                    delta_high, delta_low, measure_id),
            })
    return rows


def fmt_value(value):
    return '-' if value is None else '%.2f' % float(value)


def fmt_delta(value):
    return '-' if value is None else '%+.2f' % float(value)


def action_diff(spec):
    workspace, project, path = load_project(spec)
    out_dir = out_dir_of(spec, workspace, project)
    grid = dict(project.get('grid') or {})
    if isinstance(spec.get('grid'), dict):
        grid.update(spec['grid'])
    parts = normalize_parts(spec, workspace)
    groups = []
    if spec.get('stl') or spec.get('mesh'):
        groups = load_model_groups(spec, workspace)
    if not parts and not groups:
        # 契约 §4 允许"模型侧只有 STL/网格"的纯轮廓差分（复核 P1-6）
        raise common.CadError('BAD_ARGS', 'diff 需要模型实测：parts / report（逐件 bbox）或 stl/mesh（三角网格）')
    targets = spec.get('targets')
    if targets is None:
        targets = project.get('targets') or []
    if isinstance(targets, str):
        targets = json.loads(targets)
    rows = [build_target_row(target, parts, grid) for target in targets]
    tolerance = float(spec.get('tolerance') or 1.0)
    profile_rows = []
    if spec.get('profile') is not False:
        profile_rows = profile_rows_for(project, parts, groups, grid, tolerance, spec.get('profile_measures'))
    counts = {'PASS': 0, 'WARN': 0, 'FAIL': 0, 'MISSING': 0}
    for row in rows:
        key = row.get('verdict') or 'MISSING'
        counts[key] = counts.get(key, 0) + 1
    profile_counts = {'PASS': 0, 'WARN': 0, 'FAIL': 0, 'MISSING': 0}
    for row in profile_rows:
        profile_counts[row['verdict']] = profile_counts.get(row['verdict'], 0) + 1
    # 秒级时间戳会撞车：同一秒内两次 diff 会互相覆盖（复核 P2-17），补毫秒
    stamp = time.strftime('%Y%m%dT%H%M%S') + '-%03d' % (int(time.time() * 1000) % 1000)
    report_path = os.path.join(out_dir, 'diff-%s.md' % stamp)
    lines = ['# 差分表 · ' + str(project.get('id')), '',
             '生成：' + common.now_iso(), '',
             '格号：列 ' + str(grid.get('u_axis') or 'x') + ' 每 %g m / 行 ' % float(grid.get('u_step') or 5) +
             str(grid.get('v_axis') or 'z') + ' 每 %g m' % float(grid.get('v_step') or 5), '',
             '判定：PASS %d｜WARN %d｜FAIL %d｜MISSING %d' % (
                 counts.get('PASS', 0), counts.get('WARN', 0), counts.get('FAIL', 0), counts.get('MISSING', 0)),
             '', '## 1 目标带（构件尺寸）', '',
             '| 格号 | 指标 | 零件 | 分量 | 参考带 | 实测 | Δ | 判定 |',
             '|---|---|---|---|---|---|---|---|']
    for row in rows:
        lines.append('| %s | %s | %s | %s | %.2f~%.2f | %s | %s | %s |' % (
            row.get('cell') or '-', row.get('name') or '', row.get('part') or '-', row.get('field') or '',
            row['ref'][0], row['ref'][1],
            '-' if row.get('model') is None else '%.3f' % row['model'],
            '-' if row.get('delta') is None else '%+.3f' % row['delta'],
            row.get('verdict')))
    lines.extend(['', '## 2 轮廓（逐格上下缘）', '',
                  '| 格号 | u 区间 | 参考 vmax | 实测 vmax | Δ | 参考 vmin | 实测 vmin | Δ | 判定 |',
                  '|---|---|---|---|---|---|---|---|---|'])
    for row in profile_rows:
        lines.append('| %s | %.1f~%.1f | %.2f | %s | %s | %.2f | %s | %s | %s |' % (
            row['cell'], row['u_range'][0], row['u_range'][1],
            row['ref']['vmax'], fmt_value(row['model']['vmax']), fmt_delta(row['delta_vmax']),
            row['ref']['vmin'], fmt_value(row['model']['vmin']), fmt_delta(row['delta_vmin']),
            row['verdict']))
    fail_rows = [row for row in rows if row['verdict'] in ('FAIL', 'MISSING')]
    fail_profiles = [row for row in profile_rows if row['verdict'] in ('FAIL', 'MISSING')]
    lines.extend(['', '## 3 待处理', ''])
    if not fail_rows and not fail_profiles:
        lines.append('- 无。目标带与轮廓均在判据内。')
    for row in fail_rows:
        lines.append('- 格 %s：%s / %s（%s）Δ %+.3f → %s' % (
            row.get('cell') or '?', row.get('part') or '?', row.get('field') or '?',
            row.get('name') or '', float(row.get('delta') or 0.0), row.get('hint') or '核对参考值'))
    for row in fail_profiles:
        lines.append('- 格 %s 轮廓：Δvmax %s / Δvmin %s → %s' % (
            row['cell'], fmt_delta(row.get('delta_vmax')), fmt_delta(row.get('delta_vmin')), row['hint']))
    common.ensure_dir(out_dir)
    with open(report_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    verdict = 'FAIL' if (fail_rows or fail_profiles) else (
        'WARN' if (counts.get('WARN') or profile_counts.get('WARN')) else 'PASS')
    summary = '\n'.join([
        '差分：目标带 %d 条（PASS %d / WARN %d / FAIL %d / MISSING %d），轮廓 %d 格（FAIL %d）' % (
            len(rows), counts.get('PASS', 0), counts.get('WARN', 0), counts.get('FAIL', 0),
            counts.get('MISSING', 0), len(profile_rows), len(fail_profiles)),
        '判定：' + verdict,
        '报告：' + report_path,
    ])
    return {
        'ok': True,
        'project': project.get('id'),
        'verdict': verdict,
        'rows': rows,
        'profile_rows': profile_rows,
        'counts': counts,
        'profile_counts': profile_counts,
        'report_path': report_path,
        'summary': summary,
    }


# --------------------------------------------------------------------------
# 工程列表与读取
# --------------------------------------------------------------------------
def action_list(spec):
    workspace = common.workspace_of(spec)
    root = refkit_root(workspace)
    projects = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith('.json'):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except Exception:
                continue
            projects.append({
                'project': data.get('id') or name[:-5],
                'path': path,
                'frames': sorted((data.get('frames') or {}).keys()),
                'masks': sorted((data.get('masks') or {}).keys()),
                'measures': len(data.get('measures') or {}),
                'targets': len(data.get('targets') or []),
                'updated': data.get('updated'),
            })
    return {
        'ok': True,
        'projects': projects,
        'summary': '参考图工程 %d 个%s' % (
            len(projects), ('：' + '、'.join(item['project'] for item in projects)) if projects else
            '（先跑 cadw_ref action=calibrate 建工程）'),
    }


def action_get(spec):
    workspace, project, path = load_project(spec)
    return {
        'ok': True,
        'project': project,
        'path': path,
        'summary': '工程 %s：帧 %d、掩膜 %d、量测 %d、目标带 %d' % (
            project.get('id'), len(project.get('frames') or {}), len(project.get('masks') or {}),
            len(project.get('measures') or {}), len(project.get('targets') or [])),
    }


ACTIONS = {
    'list': action_list,
    'get': action_get,
    'calibrate': action_calibrate,
    'measure': action_measure,
    'render': action_render,
    'diff': action_diff,
}


def main(spec):
    action = str(spec.get('action') or '').strip().lower()
    handler = ACTIONS.get(action)
    if handler is None:
        raise common.CadError('BAD_ARGS', '未知 action "' + action + '"，可用：' + ', '.join(sorted(ACTIONS)))
    return handler(spec)


if __name__ == '__main__':
    common.run(main)


