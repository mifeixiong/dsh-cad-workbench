# -*- coding: utf-8 -*-
"""
graph.py —— Feature Graph 的校验与归一化（唯一实现）。

sw_build.py 的 dry_run 与正式建模都走这份校验；tests/graph_cases.py 也直接调它。
单位：长度 mm，角度度；对外只允许 "mm"。

错误统一为 GraphError(code='GRAPH_INVALID')，带 node / path / hint。
"""
import hashlib
import json
import math
import os
import sys

SUPPORTED_OPS = ('sketch', 'extrude', 'cut', 'revolve', 'hole', 'fillet', 'chamfer')
SUPPORTED_ENTITIES = ('rect', 'circle', 'polygon', 'polyline', 'centerline', 'arc')
EXPERIMENTAL_ENTITIES = ('arc',)

COMMON_KEYS = {'op', 'id', 'name', 'hint'}
OP_KEYS = {
    'sketch': {'plane', 'offset', 'entities', 'centerline', 'face'},
    'extrude': {'sketch', 'depth', 'direction', 'flip'},
    'cut': {'sketch', 'depth', 'through_all', 'flip', 'both_directions'},
    'revolve': {'sketch', 'axis', 'angle', 'type'},
    'hole': {'at', 'diameter', 'depth', 'through_all'},
    'fillet': {'radius', 'edges'},
    'chamfer': {'distance', 'edges', 'angle'},
}
ENTITY_KEYS = {
    'rect': {'type', 'center', 'width', 'height'},
    'circle': {'type', 'center', 'radius'},
    'polygon': {'type', 'center', 'radius', 'sides', 'rotation'},
    'polyline': {'type', 'points', 'closed'},
    'centerline': {'type', 'from', 'to'},
    'arc': {'type', 'center', 'radius', 'start_deg', 'end_deg'},
}
CLOSED_ENTITIES = ('rect', 'circle', 'polygon')


class GraphError(Exception):
    def __init__(self, message, node=None, path=None, hint=None):
        super().__init__(message)
        self.code = 'GRAPH_INVALID'
        self.message = message
        self.node = node
        self.path = path
        self.hint = hint

    def envelope(self):
        error = {'code': self.code, 'message': self.message}
        details = {}
        if self.node is not None:
            details['node'] = self.node
        if self.path:
            details['path'] = self.path
        if self.hint:
            details['hint'] = self.hint
        if details:
            error['details'] = details
        return {'ok': False, 'error': error}


def _fail(message, node=None, path=None, hint=None):
    raise GraphError(message, node=node, path=path, hint=hint)


def _number(value, label, node, path, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail('%s 必须是数字' % label, node, path)
    number = float(value)
    if not math.isfinite(number):
        _fail('%s 必须是有限数字' % label, node, path)
    if minimum is not None and number < minimum:
        _fail('%s 不能小于 %s' % (label, minimum), node, path)
    if maximum is not None and number > maximum:
        _fail('%s 不能大于 %s' % (label, maximum), node, path)
    return number


def _point(value, label, node, path, allow_zero=True):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        _fail('%s 必须是 [x, y]' % label, node, path)
    x = _number(value[0], label + '[0]', node, path)
    y = _number(value[1], label + '[1]', node, path)
    if not allow_zero and x == 0 and y == 0:
        _fail('%s 不能是原点' % label, node, path)
    return [round(x, 9), round(y, 9)]


def _point3(value, label, node, path):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _fail('%s 必须是 [x, y, z]（mm）' % label, node, path)
    return [round(_number(value[index], '%s[%d]' % (label, index), node, path), 9) for index in range(3)]


def _keys(obj, allowed, label, node, path):
    unknown = sorted(set(obj.keys()) - allowed)
    if unknown:
        _fail('%s 含未知字段：%s' % (label, ', '.join(unknown)), node, path,
              hint='未知字段一律拒绝，避免拼错字段名却静默用默认值。')


def _entity(entity, node, index):
    path = 'entities[%d]' % index
    if not isinstance(entity, dict):
        _fail('草图实体必须是对象', node, path)
    kind = entity.get('type')
    if kind not in SUPPORTED_ENTITIES:
        _fail('不支持的草图实体：%s' % kind, node, path,
              hint='v0.1 支持 %s；slot / rounded_rect 计划在 v0.2。' % ' / '.join(SUPPORTED_ENTITIES))
    _keys(entity, ENTITY_KEYS[kind], '实体', node, path)
    if kind == 'rect':
        center = _point(entity.get('center', [0, 0]), 'center', node, path)
        width = _number(entity.get('width'), 'width', node, path, minimum=1e-6)
        height = _number(entity.get('height'), 'height', node, path, minimum=1e-6)
        return {'type': 'rect', 'center': center, 'width': width, 'height': height}
    if kind == 'circle':
        center = _point(entity.get('center', [0, 0]), 'center', node, path)
        radius = _number(entity.get('radius'), 'radius', node, path, minimum=1e-6)
        return {'type': 'circle', 'center': center, 'radius': radius}
    if kind == 'polygon':
        center = _point(entity.get('center', [0, 0]), 'center', node, path)
        radius = _number(entity.get('radius'), 'radius', node, path, minimum=1e-6)
        sides = entity.get('sides')
        if not isinstance(sides, int) or isinstance(sides, bool) or sides < 3 or sides > 64:
            _fail('sides 必须是 3..64 的整数', node, path)
        rotation = _number(entity.get('rotation', 0), 'rotation', node, path, minimum=-360, maximum=360)
        return {'type': 'polygon', 'center': center, 'radius': radius, 'sides': sides, 'rotation': rotation}
    if kind == 'polyline':
        points = entity.get('points')
        if not isinstance(points, list) or len(points) < 3:
            _fail('polyline.points 至少 3 个点', node, path)
        parsed = [_point(item, 'points[%d]' % idx, node, path) for idx, item in enumerate(points)]
        closed = entity.get('closed', True)
        if not isinstance(closed, bool):
            _fail('closed 必须是布尔值', node, path)
        if closed and parsed[0] != parsed[-1]:
            if math.dist(parsed[0], parsed[-1]) > 1e-6:
                parsed.append(parsed[0])
        return {'type': 'polyline', 'points': parsed, 'closed': closed}
    if kind == 'centerline':
        start = _point(entity.get('from'), 'from', node, path)
        end = _point(entity.get('to'), 'to', node, path)
        if start == end:
            _fail('centerline 的 from 与 to 不能重合', node, path)
        return {'type': 'centerline', 'from': start, 'to': end}
    if kind == 'arc':
        center = _point(entity.get('center', [0, 0]), 'center', node, path)
        radius = _number(entity.get('radius'), 'radius', node, path, minimum=1e-6)
        start = _number(entity.get('start_deg', 0), 'start_deg', node, path)
        end = _number(entity.get('end_deg', 90), 'end_deg', node, path)
        return {'type': 'arc', 'center': center, 'radius': radius, 'start_deg': start, 'end_deg': end}
    _fail('未处理的实体类型：%s' % kind, node, path)


def _profile_points(entity):
    kind = entity.get('type')
    if kind == 'rect':
        cx, cy = entity['center']
        half_w, half_h = entity['width'] / 2.0, entity['height'] / 2.0
        return [(cx - half_w, cy - half_h), (cx + half_w, cy - half_h),
                (cx + half_w, cy + half_h), (cx - half_w, cy + half_h)]
    if kind == 'polyline':
        return [tuple(point) for point in entity['points']]
    if kind == 'polygon':
        import math
        cx, cy = entity['center']
        radius = entity['radius']
        rotation = entity.get('rotation', 0)
        return [(cx + radius * math.cos(math.radians(rotation + 360.0 * index / entity['sides'])),
                 cy + radius * math.sin(math.radians(rotation + 360.0 * index / entity['sides'])))
                for index in range(entity['sides'])]
    if kind == 'circle':
        cx, cy = entity['center']
        radius = entity['radius']
        return [(cx - radius, cy), (cx + radius, cy), (cx, cy - radius), (cx, cy + radius)]
    return []


def _check_revolve_profile(sketch_node, index, path):
    """旋转轮廓不能同时出现在中心线两侧（会自交；SolidWorks 不报错但几何错）。"""
    centerline = sketch_node.get('centerline')
    if not centerline:
        for entity in sketch_node.get('entities', []):
            if entity.get('type') == 'centerline':
                centerline = [entity['from'], entity['to']]
                break
    if not centerline:
        return
    (x1, y1), (x2, y2) = centerline
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        _fail('revolve 的中心线长度为零', index, path)
    sides = []
    for entity in sketch_node.get('entities', []):
        for px, py in _profile_points(entity):
            sides.append(dx * (py - y1) - dy * (px - x1))
    if not sides:
        return
    tolerance = 1e-6
    if max(sides) > tolerance and min(sides) < -tolerance:
        _fail('旋转轮廓跨越中心线（两侧都有材料），会自交或失败', index, path,
              hint='把轮廓全部放在中心线一侧（贴轴或离轴）。')


def validate(graph):
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except Exception:
            _fail('graph 不是合法 JSON')
    if not isinstance(graph, dict):
        _fail('graph 必须是对象：{units:"mm", nodes:[...]}')
    _keys(graph, {'units', 'nodes', 'name'}, 'graph', None, None)
    units = graph.get('units', 'mm')
    if units != 'mm':
        _fail('graph.units 只支持 "mm"', None, 'units')
    nodes = graph.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        _fail('graph.nodes 必须是非空数组', None, 'nodes')
    if len(nodes) > 200:
        _fail('graph.nodes 最多 200 个节点', None, 'nodes')

    normalized = []
    last_sketch_index = None
    ids = {}
    for index, node in enumerate(nodes):
        path = 'nodes[%d]' % index
        if not isinstance(node, dict):
            _fail('节点必须是对象', index, path)
        op = node.get('op')
        if op not in SUPPORTED_OPS:
            _fail('不支持的 op：%s' % op, index, path,
                  hint='v0.1 支持 %s；shell / pattern 计划在 v0.2。' % ' / '.join(SUPPORTED_OPS))
        _keys(node, COMMON_KEYS | OP_KEYS[op], '节点', index, path)
        node_id = node.get('id') or ('n%d' % (index + 1))
        if not isinstance(node_id, str) or not node_id.strip():
            _fail('id 必须是非空字符串', index, path)
        if node_id in ids:
            _fail('id 重复：%s' % node_id, index, path)
        ids[node_id] = index
        base = {'op': op, 'id': node_id}
        if node.get('name'):
            base['name'] = str(node['name'])

        if op == 'sketch':
            plane = node.get('plane', 'front')
            if isinstance(plane, dict):
                _keys(plane, {'face'}, 'plane', index, path)
                face = _point3(plane.get('face'), 'plane.face', index, path)
                base['plane'] = {'face': face}
            elif isinstance(plane, str) and plane.strip():
                key = plane.strip()
                alias = {'front': 'Front Plane', 'top': 'Top Plane', 'right': 'Right Plane'}
                base['plane'] = alias.get(key.lower(), key)
            else:
                _fail('plane 必须是字符串或 {"face":[x,y,z]}', index, path)
            if node.get('face') is not None:
                base['plane'] = {'face': _point3(node.get('face'), 'face', index, path)}
            if node.get('offset') is not None:
                _fail('offset 偏移基准面不在 v0.1 支持范围内', index, path,
                      hint='先用 cadw_session 建偏移面，或在 v0.2 用 offset。')
            entities = node.get('entities')
            if not isinstance(entities, list) or not entities:
                _fail('sketch.entities 必须是非空数组', index, path)
            parsed_entities = [_entity(entity, index, position) for position, entity in enumerate(entities)]
            closed = any(
                entity.get('type') in CLOSED_ENTITIES
                or (entity.get('type') == 'polyline' and entity.get('closed'))
                for entity in parsed_entities
            )
            if not closed:
                _fail('草图缺少闭合轮廓（extrude/cut/revolve 需要）', index, path)
            base['entities'] = parsed_entities
            centerline = node.get('centerline')
            if centerline is not None:
                if not isinstance(centerline, (list, tuple)) or len(centerline) != 2:
                    _fail('centerline 必须是 [[x1,y1],[x2,y2]]', index, path)
                base['centerline'] = [_point(centerline[0], 'centerline[0]', index, path),
                                      _point(centerline[1], 'centerline[1]', index, path)]
            last_sketch_index = index

        elif op in ('extrude', 'cut'):
            sketch_ref = node.get('sketch')
            sketch_index = None
            if sketch_ref is not None:
                if isinstance(sketch_ref, int) and not isinstance(sketch_ref, bool):
                    sketch_index = sketch_ref if 0 <= sketch_ref < index else None
                elif isinstance(sketch_ref, str):
                    sketch_index = ids.get(sketch_ref)
                if sketch_index is None or normalized[sketch_index].get('op') != 'sketch':
                    _fail('sketch 必须引用前面已定义的 sketch 节点（id 或序号）', index, path)
            else:
                sketch_index = last_sketch_index
            if sketch_index is None:
                _fail('%s 前必须有 sketch 节点' % op, index, path)
            base['sketch_index'] = sketch_index
            if op == 'extrude':
                depth = _number(node.get('depth'), 'depth', index, path, minimum=1e-6)
                direction = node.get('direction', 'blind')
                if direction not in ('blind', 'midplane'):
                    _fail('direction 只支持 blind / midplane', index, path)
                base['depth'] = depth
                base['direction'] = direction
                base['flip'] = bool(node.get('flip', False))
            else:
                through = node.get('through_all', False)
                if not isinstance(through, bool):
                    _fail('through_all 必须是布尔值', index, path)
                base['through_all'] = through
                if through:
                    base['depth'] = None
                else:
                    if node.get('depth') is None:
                        _fail('盲切必须显式给 depth（或用 through_all:true）', index, path)
                    base['depth'] = _number(node.get('depth'), 'depth', index, path, minimum=1e-6)
                base['flip'] = bool(node.get('flip', False))
                both = node.get('both_directions', False)
                if both:
                    _fail('v0.1 不支持 cut.both_directions（请用两次 cut 或调整 flip）', index, path)
                base['both_directions'] = False

        elif op == 'revolve':
            sketch_ref = node.get('sketch')
            sketch_index = None
            if sketch_ref is not None:
                if isinstance(sketch_ref, int) and not isinstance(sketch_ref, bool):
                    sketch_index = sketch_ref if 0 <= sketch_ref < index else None
                elif isinstance(sketch_ref, str):
                    sketch_index = ids.get(sketch_ref)
                if sketch_index is None or normalized[sketch_index].get('op') != 'sketch':
                    _fail('sketch 必须引用前面已定义的 sketch 节点', index, path)
            else:
                sketch_index = last_sketch_index
            if sketch_index is None:
                _fail('revolve 前必须有 sketch 节点', index, path)
            sketch_node = normalized[sketch_index]
            axis = node.get('axis', 'centerline')
            if axis != 'centerline':
                _fail('v0.1 只支持 axis="centerline"（在草图画中心线）', index, path)
            has_centerline = any(entity.get('type') == 'centerline' for entity in sketch_node.get('entities', []))
            if not has_centerline and 'centerline' not in sketch_node:
                _fail('revolve 的草图必须包含 centerline 实体作为旋转轴', index, path)
            _check_revolve_profile(sketch_node, index, path)
            angle = _number(node.get('angle', 360), 'angle', index, path, minimum=0.1, maximum=360)
            kind = node.get('type', 'boss')
            if kind not in ('boss', 'cut'):
                _fail('revolve.type 只支持 boss / cut', index, path)
            base.update({'sketch_index': sketch_index, 'axis': 'centerline', 'angle': angle, 'type': kind})

        elif op == 'hole':
            base['at'] = _point3(node.get('at'), 'at', index, path)
            base['diameter'] = _number(node.get('diameter'), 'diameter', index, path, minimum=1e-6)
            through = node.get('through_all', True)
            if not isinstance(through, bool):
                _fail('through_all 必须是布尔值', index, path)
            base['through_all'] = through
            base['depth'] = None if through else _number(node.get('depth'), 'depth', index, path, minimum=1e-6)
            last_sketch_index = None

        elif op in ('fillet', 'chamfer'):
            size_key = 'radius' if op == 'fillet' else 'distance'
            base[size_key] = _number(node.get(size_key), size_key, index, path, minimum=1e-6)
            edges = node.get('edges')
            if edges == 'all':
                base['edges'] = 'all'
            elif isinstance(edges, list) and edges:
                parsed = []
                for position, edge in enumerate(edges):
                    if isinstance(edge, dict):
                        _keys(edge, {'at', 'tol'}, '边', index, '%s[%d]' % (path, position))
                        parsed.append({'at': _point3(edge.get('at'), 'edge.at', index, path),
                                       'tol': _number(edge.get('tol', 0.2), 'edge.tol', index, path,
                                                      minimum=0.001, maximum=5.0)})
                    else:
                        parsed.append({'at': _point3(edge, 'edge', index, path), 'tol': 0.2})
                base['edges'] = parsed
            else:
                _fail('edges 必须是 "all" 或 [{at:[x,y,z],tol?}, ...]', index, path)
                base['edges'] = []
            if op == 'chamfer':
                base['angle'] = _number(node.get('angle', 45), 'angle', index, path, minimum=1, maximum=89)
            last_sketch_index = None

        normalized.append(base)

    stats = {
        'nodes': len(normalized),
        'sketches': sum(1 for node in normalized if node['op'] == 'sketch'),
        'features': sum(1 for node in normalized if node['op'] != 'sketch'),
        'ops': sorted({node['op'] for node in normalized}),
    }
    return {'units': 'mm', 'nodes': normalized}, stats


def graph_hash(graph):
    text = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main(spec):
    graph, stats = validate(spec.get('graph'))
    return {'ok': True, 'graph': graph, 'stats': stats, 'graph_hash': graph_hash(graph)}


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sw_common import run
    run(main)
