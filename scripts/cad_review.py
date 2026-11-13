# -*- coding: utf-8 -*-
"""cad_review.py —— DXF / STL / STEP 只读审查（规则数据驱动）。"""
import json
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(PLUGIN_ROOT, 'rules', 'dxf_rules.json')


def _check(check_id, status, detail, count=None, hint=None, samples=None):
    item = {'id': check_id, 'status': status, 'detail': detail}
    if count is not None:
        item['count'] = count
    if hint:
        item['hint'] = hint
    if samples:
        item['samples'] = samples[:5]
    return item


def _verdict(checks):
    statuses = [item['status'] for item in checks]
    if 'FAIL' in statuses:
        return 'FAIL'
    if 'WARN' in statuses:
        return 'WARN'
    return 'PASS'


def _load_rules(overrides):
    rules = {}
    try:
        with open(DEFAULT_RULES, 'r', encoding='utf-8') as handle:
            rules = json.load(handle)
    except Exception:
        rules = {}
    if overrides is not None and not isinstance(overrides, dict):
        raise common.CadError('BAD_ARGS', 'rules 必须是对象（可只给要覆盖的字段）')
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(rules.get(key), dict):
                merged = dict(rules[key])
                merged.update(value)
                rules[key] = merged
            else:
                rules[key] = value
    return rules


def review_dxf(path, rules):
    try:
        import ezdxf
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'path': path, 'kind': 'dxf', 'verdict': 'FAIL',
                'checks': [_check('ezdxf', 'FAIL', '缺少 ezdxf：%s' % error,
                                  hint='pip install ezdxf')]}
    checks = []
    try:
        doc = ezdxf.readfile(path)
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'path': path, 'kind': 'dxf', 'verdict': 'FAIL',
                'checks': [_check('read', 'FAIL', 'DXF 读取失败：%s' % error)]}
    msp = doc.modelspace()

    units_rule = rules.get('units', {})
    allowed_units = set(units_rule.get('allowed', [4, 6]))
    try:
        units = int(doc.header.get('$INSUNITS', 0) or 0)
    except Exception:
        units = 0
    if units == 0:
        checks.append(_check('units', 'WARN', 'DXF 未声明单位（$INSUNITS=0）。',
                             hint='导出时设置单位或写 $INSUNITS=4（mm）。'))
    elif allowed_units and units not in allowed_units:
        checks.append(_check('units', 'FAIL', '单位 $INSUNITS=%s 不在允许集合 %s。' % (units, sorted(allowed_units))))
    else:
        checks.append(_check('units', 'PASS', '单位 $INSUNITS=%s。' % units))

    layer_rule = rules.get('layers', {})
    names = [layer.dxf.name for layer in doc.layers]
    upper = {name.upper() for name in names}
    aliases = layer_rule.get('aliases', {})

    def _layer_present(canonical):
        if canonical.upper() in upper:
            return True
        for alternative in aliases.get(canonical, []):
            if alternative.upper() in upper or alternative in names:
                return True
        return False

    required = layer_rule.get('required', [])
    missing = [name for name in required if not _layer_present(name)]
    strict = bool(layer_rule.get('strict', False))
    if missing:
        checks.append(_check('layers_required', 'FAIL' if strict else 'WARN',
                             '缺少图层：%s' % ', '.join(missing), count=len(missing),
                             hint='机械图建议 OUTLINE/CENTER/HIDDEN/DIM/TEXT 分层。'))
    else:
        checks.append(_check('layers_required', 'PASS', '必需图层齐全。'))
    pattern = layer_rule.get('pattern')
    if pattern and str(pattern).strip():
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = None
        bad = [name for name in names if regex and not regex.match(name.upper())]
        if bad:
            checks.append(_check('layer_names', 'WARN', '图层名不符合 %s：%s' % (pattern, ', '.join(bad[:8])),
                                 count=len(bad), samples=bad))
        else:
            checks.append(_check('layer_names', 'PASS', '图层名符合规范。'))
    forbidden = set(name.upper() for name in layer_rule.get('forbidden', []))
    used_forbidden = sorted(name for name in names if name.upper() in forbidden)
    if used_forbidden:
        checks.append(_check('layers_forbidden', 'WARN', '使用了禁止图层：%s' % ', '.join(used_forbidden)))

    linetype_rule = rules.get('linetypes', {})
    bad_linetypes = []
    for layer in doc.layers:
        expected = linetype_rule.get(layer.dxf.name.upper())
        if not expected:
            continue
        actual = str(layer.dxf.get('linetype', '')).upper()
        if actual and actual not in [item.upper() for item in expected]:
            bad_linetypes.append('%s=%s（期望 %s）' % (layer.dxf.name, actual, '/'.join(expected)))
    if bad_linetypes:
        checks.append(_check('linetypes', 'WARN', '线型不符合：%s' % '；'.join(bad_linetypes[:8])))
    else:
        checks.append(_check('linetypes', 'PASS', '关键图层线型正常。'))

    entity_rule = rules.get('entities', {})
    zero_length = []
    segments = {}
    counts = {}
    for entity in msp:
        kind = entity.dxftype()
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'LINE':
            start = entity.dxf.start
            end = entity.dxf.end
            length = math.dist((start.x, start.y), (end.x, end.y))
            if length < 1e-6:
                zero_length.append('%s 零长线 @%s' % (entity.dxf.handle, [round(start.x, 3), round(start.y, 3)]))
            else:
                endpoints = sorted([(round(start.x, 4), round(start.y, 4)),
                                    (round(end.x, 4), round(end.y, 4))])
                key = (entity.dxf.layer, tuple(endpoints))
                segments[key] = segments.get(key, 0) + 1
    max_zero = int(entity_rule.get('max_zero_length', 0))
    if len(zero_length) > max_zero:
        checks.append(_check('zero_length', 'FAIL', '存在 %d 条零长直线。' % len(zero_length),
                             count=len(zero_length), samples=zero_length))
    else:
        checks.append(_check('zero_length', 'PASS', '没有零长直线。'))
    duplicates = sum(count * (count - 1) // 2 for count in segments.values())
    max_duplicates = int(entity_rule.get('max_duplicate_pairs', 0))
    if duplicates > max_duplicates:
        checks.append(_check('duplicates', 'WARN' if duplicates <= max_duplicates + 5 else 'FAIL',
                             '发现 %d 对重复线段（同图层同端点）。' % duplicates, count=duplicates))
    else:
        checks.append(_check('duplicates', 'PASS', '没有重复线段（0 对）。', count=0))

    dims_rule = rules.get('dimensions', {})
    dimension_count = counts.get('DIMENSION', 0) + counts.get('ARC_DIMENSION', 0)
    min_dims = int(dims_rule.get('min_count', 0))
    if min_dims and dimension_count < min_dims:
        checks.append(_check('dimensions', 'WARN', '尺寸只有 %d 个（期望至少 %d）。' % (dimension_count, min_dims),
                             count=dimension_count))
    else:
        checks.append(_check('dimensions', 'PASS', '尺寸数量 %d。' % dimension_count, count=dimension_count))

    text_rule = rules.get('text', {})
    min_height = float(text_rule.get('min_height_mm', 0))
    small = []
    empty = []
    for entity in msp.query('TEXT MTEXT'):
        try:
            value = entity.dxf.text if entity.dxftype() == 'TEXT' else entity.text
        except Exception:
            value = ''
        if not str(value).strip():
            empty.append(entity.dxf.handle)
        if entity.dxftype() == 'MTEXT':
            height = float(entity.dxf.get('char_height', 0) or 2.5)
        else:
            height = float(getattr(entity.dxf, 'height', 0) or 2.5)
        if min_height and height < min_height:
            small.append('%s=%.2fmm' % (str(value)[:12], height))
    if small:
        checks.append(_check('text_height', 'WARN', '字高小于 %s mm：%s' % (min_height, ', '.join(small[:8])),
                             count=len(small)))
    else:
        checks.append(_check('text_height', 'PASS', '文字字高正常。'))
    if empty:
        checks.append(_check('text_empty', 'WARN', '存在空文字实体 %d 个。' % len(empty), count=len(empty)))
    else:
        checks.append(_check('text_empty', 'PASS', '没有空文字。'))

    extents_rule = rules.get('extents', {})
    try:
        from ezdxf import bbox
        extents = bbox.extents(msp)
        size = [extents.size.x, extents.size.y]
    except Exception:
        size = None
    if size is None:
        checks.append(_check('extents', 'WARN', '读不到图形范围。'))
    else:
        min_size = float(extents_rule.get('min_size_mm', 0))
        max_size = float(extents_rule.get('max_size_mm', 1e9))
        if min(size) < min_size:
            checks.append(_check('extents', 'WARN', '图形范围过小：%s mm。' % [round(v, 3) for v in size]))
        elif max(size) > max_size:
            checks.append(_check('extents', 'WARN', '图形范围过大：%s mm（可能单位错了）。' % [round(v, 3) for v in size]))
        else:
            checks.append(_check('extents', 'PASS', '图形范围 %s mm。' % [round(v, 3) for v in size]))

    return {
        'ok': True,
        'path': path,
        'kind': 'dxf',
        'verdict': _verdict(checks),
        'checks': checks,
        'stats': {
            'layers': names,
            'entities': counts,
            'entity_total': sum(counts.values()),
            'dimension_count': dimension_count,
            'units': units,
            'extents_mm': [round(value, 3) for value in size] if size else None,
        },
    }


def _read_stl(path):
    with open(path, 'rb') as handle:
        data = handle.read()
    if len(data) < 84:
        raise ValueError('文件太小，不是 STL')
    triangles = []
    if data[:5].lower() == b'solid' and b'facet' in data[:2000].lower():
        text = data.decode('ascii', 'ignore')
        for match in re.finditer(r'vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)', text):
            triangles.append(tuple(float(match.group(index)) for index in (1, 2, 3)))
    else:
        count = struct.unpack('<I', data[80:84])[0]
        offset = 84
        for _ in range(min(count, 2_000_000)):
            if offset + 50 > len(data):
                break
            values = struct.unpack('<12f', data[offset:offset + 48])
            triangles.append((values[3], values[4], values[5]))
            triangles.append((values[6], values[7], values[8]))
            triangles.append((values[9], values[10], values[11]))
            offset += 50
    return triangles


def review_stl(path):
    checks = []
    try:
        points = _read_stl(path)
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'path': path, 'kind': 'stl', 'verdict': 'FAIL',
                'checks': [_check('read', 'FAIL', 'STL 读取失败：%s' % error)]}
    triangle_count = len(points) // 3
    if triangle_count == 0:
        return {'ok': False, 'path': path, 'kind': 'stl', 'verdict': 'FAIL',
                'checks': [_check('triangles', 'FAIL', 'STL 没有三角面。')]}
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    size = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    degenerate = 0
    edges = {}
    for index in range(triangle_count):
        a, b, c = points[index * 3:index * 3 + 3]
        if a == b or b == c or a == c:
            degenerate += 1
            continue
        for first, second in ((a, b), (b, c), (c, a)):
            key = tuple(sorted((tuple(round(v, 4) for v in first), tuple(round(v, 4) for v in second))))
            edges[key] = edges.get(key, 0) + 1
    open_edges = sum(1 for count in edges.values() if count == 1)
    non_manifold = sum(1 for count in edges.values() if count > 2)
    checks.append(_check('triangles', 'PASS', '%d 个三角面。' % triangle_count, count=triangle_count))
    checks.append(_check('degenerate', 'FAIL' if degenerate > 0 else 'PASS',
                         '退化三角面 %d 个。' % degenerate, count=degenerate))
    watertight = open_edges == 0 and non_manifold == 0
    checks.append(_check('watertight', 'PASS' if watertight else 'WARN',
                         '水密启发式：%s（开放边 %d，非流形边 %d）。' % ('通过' if watertight else '不通过',
                                                                       open_edges, non_manifold),
                         count=open_edges))
    checks.append(_check('bbox', 'PASS', '包围盒 %s（STL 无单位声明，按文件数值计）。' %
                         [round(value, 3) for value in size]))
    return {
        'ok': True,
        'path': path,
        'kind': 'stl',
        'verdict': _verdict(checks),
        'checks': checks,
        'stats': {'triangles': triangle_count, 'bbox': [round(value, 3) for value in size],
                  'open_edges': open_edges, 'non_manifold_edges': non_manifold},
    }


def review_step(path):
    checks = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            limit = 8 * 1024 * 1024
            text = handle.read(limit + 1)
            truncated = len(text) > limit
            text = text[:limit]
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'path': path, 'kind': 'step', 'verdict': 'FAIL',
                'checks': [_check('read', 'FAIL', 'STEP 读取失败：%s' % error)]}
    if 'ISO-10303-21' not in text[:2000]:
        checks.append(_check('header', 'FAIL', '不是 ISO-10303-21 STEP 文件。'))
    else:
        checks.append(_check('header', 'PASS', 'STEP 头部正常。'))
    schema = 'unknown'
    match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text)
    if match:
        schema = match.group(1)
    checks.append(_check('schema', 'PASS', 'schema：%s。' % schema))
    unit = 'unknown'
    if re.search(r'SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.', text):
        unit = 'mm'
    elif re.search(r'SI_UNIT\s*\(\s*\$?\s*,\s*\.METRE\.', text):
        unit = 'm'
    checks.append(_check('unit', 'PASS' if unit != 'unknown' else 'WARN',
                         '单位：%s。' % (unit if unit != 'unknown' else '未识别')))
    points = re.findall(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)", text)
    bbox = None
    if points:
        xs, ys, zs = [], [], []
        for item in points[:200000]:
            parts = [part.strip() for part in item.split(',')]
            if len(parts) < 3:
                continue
            try:
                xs.append(float(parts[0])); ys.append(float(parts[1])); zs.append(float(parts[2]))
            except ValueError:
                continue
        if xs:
            bbox = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    bbox_check = _check('bounding_box', 'PASS' if bbox else 'WARN',
                        ('坐标包围盒（启发式，含全部 CARTESIAN_POINT，可能偏大）：%s。' % [round(v, 4) for v in bbox])
                        if bbox else '没有 CARTESIAN_POINT，无法给包围盒。',
                        hint='启发式结果不能当精确尺寸；需要精确值请用 SolidWorks/FreeCAD 打开测量。')
    if bbox:
        bbox_check['heuristic'] = True
    checks.append(bbox_check)
    entity_count = len(re.findall(r'^#\d+\s*=', text, re.MULTILINE))
    if truncated:
        checks.append(_check('truncated', 'WARN',
                             'STEP 超过 8MB，只统计了前 8MB；实体数与包围盒可能偏小。',
                             hint='精确审查请用 CAD 软件打开，或拆分 STEP。'))
    return {
        'ok': True,
        'path': path,
        'kind': 'step',
        'verdict': _verdict(checks),
        'checks': checks,
        'stats': {'schema': schema, 'unit': unit, 'entities': entity_count,
                  'bbox': [round(value, 4) for value in bbox] if bbox else None,
                  'bbox_heuristic': True},
    }


def main(spec):
    paths = [os.path.abspath(item) for item in (spec.get('paths') or [])]
    if not paths:
        raise common.CadError('BAD_ARGS', '至少需要一个文件路径')
    rules = _load_rules(spec.get('rules'))
    files = []
    for path in paths:
        if not os.path.isfile(path):
            files.append({'ok': False, 'path': path, 'kind': 'unknown', 'verdict': 'FAIL',
                          'checks': [_check('exists', 'FAIL', '文件不存在。')]})
            continue
        extension = os.path.splitext(path)[1].lower()
        if extension == '.dxf':
            files.append(review_dxf(path, rules))
        elif extension == '.stl':
            files.append(review_stl(path))
        elif extension in ('.step', '.stp'):
            files.append(review_step(path))
        else:
            files.append({'ok': False, 'path': path, 'kind': extension or 'unknown', 'verdict': 'FAIL',
                          'checks': [_check('kind', 'FAIL', '不支持的审查格式：%s（支持 .dxf/.stl/.step/.stp）' % extension)]})
    verdicts = [item.get('verdict') for item in files]
    verdict = 'FAIL' if 'FAIL' in verdicts else ('WARN' if 'WARN' in verdicts else 'PASS')
    return {'ok': True, 'files': files, 'verdict': verdict, 'rules_version': rules.get('version')}


if __name__ == '__main__':
    common.run(main)
