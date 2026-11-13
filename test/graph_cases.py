# -*- coding: utf-8 -*-
"""graph.py 的离线用例（不需要 SolidWorks）。"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import graph  # noqa: E402


def rect(width=10, height=10, **extra):
    entity = {'type': 'rect', 'width': width, 'height': height}
    entity.update(extra)
    return entity


CASES = [
    ('plate', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'plane': 'front', 'entities': [rect(120, 80)]},
        {'op': 'extrude', 'depth': 10},
    ]}, True, None),
    ('hole', {'units': 'mm', 'nodes': [
        {'op': 'hole', 'at': [0, 0, 10], 'diameter': 8},
    ]}, True, None),
    ('fillet_all', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': 2},
        {'op': 'fillet', 'radius': 1, 'edges': 'all'},
    ]}, True, None),
    ('revolve_centerline', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [{'type': 'rect', 'center': [5, 5], 'width': 10, 'height': 10}],
         'centerline': [[0, 0], [0, 20]]},
        {'op': 'revolve', 'angle': 180},
    ]}, True, None),
    ('revolve_crosses_axis', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect(10, 10)],
         'centerline': [[0, 0], [0, 20]]},
        {'op': 'revolve'},
    ]}, False, '跨越'),
    ('cut_both_directions', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'cut', 'depth': 2, 'both_directions': True},
    ]}, False, 'both_directions'),
    ('extrude_flip', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': 2, 'flip': True},
    ]}, True, None),
    ('polyline_auto_close', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [
            {'type': 'polyline', 'points': [[0, 0], [10, 0], [10, 10], [0, 10]]},
        ]},
        {'op': 'extrude', 'depth': 5},
    ]}, True, None),
    ('sketch_ref_by_id', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'base', 'entities': [rect()]},
        {'op': 'extrude', 'sketch': 'base', 'depth': 5},
    ]}, True, None),
    ('bad_units', {'units': 'in', 'nodes': [{'op': 'sketch', 'entities': [rect()]}]}, False, 'units'),
    ('empty_nodes', {'units': 'mm', 'nodes': []}, False, '非空'),
    ('unknown_op', {'units': 'mm', 'nodes': [{'op': 'shell', 'thickness': 2}]}, False, '不支持'),
    ('unknown_field', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()], 'depth': 10},
    ]}, False, '未知字段'),
    ('negative_depth', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': -3},
    ]}, False, 'depth'),
    ('nan_depth', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': float('nan')},
    ]}, False, '有限'),
    ('open_contour', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [
            {'type': 'polyline', 'points': [[0, 0], [5, 0], [5, 5]], 'closed': False},
        ]},
    ]}, False, '闭合'),
    ('extrude_before_sketch', {'units': 'mm', 'nodes': [{'op': 'extrude', 'depth': 5}]}, False, 'sketch'),
    ('bad_sketch_ref', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'a', 'entities': [rect()]},
        {'op': 'extrude', 'sketch': 'nope', 'depth': 5},
    ]}, False, '引用'),
    ('polyline_too_short', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [{'type': 'polyline', 'points': [[0, 0], [1, 1]]}]},
    ]}, False, '至少 3'),
    ('fillet_zero', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': 2},
        {'op': 'fillet', 'radius': 0, 'edges': 'all'},
    ]}, False, 'radius'),
    ('fillet_bad_edges', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': 2},
        {'op': 'fillet', 'radius': 1, 'edges': []},
    ]}, False, 'edges'),
    ('hole_missing', {'units': 'mm', 'nodes': [{'op': 'hole', 'at': [0, 0]}]}, False, 'at'),
    ('revolve_without_centerline', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'revolve'},
    ]}, False, 'centerline'),
    ('cut_depth_required', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'cut'},
    ]}, False, 'depth'),
    ('cut_through', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'cut', 'through_all': True},
    ]}, True, None),
    ('polygon_ok', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [{'type': 'polygon', 'radius': 10, 'sides': 6}]},
        {'op': 'extrude', 'depth': 3},
    ]}, True, None),
    ('polygon_bad_sides', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [{'type': 'polygon', 'radius': 10, 'sides': 2}]},
    ]}, False, 'sides'),
    ('too_many_nodes', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]}
    ] + [{'op': 'hole', 'at': [0, 0, 0], 'diameter': 1} for _ in range(200)]}, False, '200'),
    ('duplicate_id', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'a', 'entities': [rect()]},
        {'op': 'sketch', 'id': 'a', 'entities': [rect()]},
    ]}, False, '重复'),
    ('face_plane', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'plane': {'face': [0, 0, 10]}, 'entities': [{'type': 'circle', 'radius': 5}]},
        {'op': 'cut', 'through_all': True},
    ]}, True, None),
    ('chamfer_ok', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [rect()]},
        {'op': 'extrude', 'depth': 2},
        {'op': 'chamfer', 'distance': 1, 'edges': [[10, 10, 2]]},
    ]}, True, None),
    ('arc_ok', {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'entities': [
            {'type': 'arc', 'center': [0, 0], 'radius': 5, 'start_deg': 0, 'end_deg': 180},
        ]},
    ]}, False, '闭合'),
]


def main():
    failures = []
    for name, payload, expect_ok, keyword in CASES:
        try:
            result, stats = graph.validate(payload)
            ok = True
            message = ''
        except graph.GraphError as error:
            ok = False
            message = error.message
        if ok != expect_ok:
            failures.append('%s: 期望 ok=%s，实际 ok=%s（%s）' % (name, expect_ok, ok, message))
            continue
        if not expect_ok and keyword and keyword not in message:
            failures.append('%s: 错误信息 %r 不含 %r' % (name, message, keyword))
        if expect_ok:
            digest = graph.graph_hash(result)
            if len(digest) != 64:
                failures.append('%s: graph_hash 长度异常' % name)
    print('graph_cases: %d 例，失败 %d' % (len(CASES), len(failures)))
    for item in failures:
        print('  FAIL ' + item)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
