# -*- coding: utf-8 -*-
"""live_advanced.py —— 扫掠 / 线性阵列 / 圆周阵列 / 装配体（需要 SolidWorks）。

用法：python test/live_advanced.py [--workspace DIR]
"""
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PASS, FAIL = [], []


def run(script, spec, timeout=300):
    path = os.path.join(WORK, '_spec.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(spec, handle, ensure_ascii=False)
    process = subprocess.run([PY, os.path.join(ROOT, 'scripts', script), '--spec', path],
                             capture_output=True, text=True, timeout=timeout)
    lines = [line for line in process.stdout.strip().splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1])
    except Exception:
        result = {'ok': False, 'error': {'code': 'NO_JSON',
                                        'message': (process.stdout + process.stderr)[-400:]}}
    result['_exit'] = process.returncode
    return result


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(('  PASS ' if condition else '  FAIL ') + name + ' ' + str(detail)[:200])


def volume(token):
    return (run('sw_measure.py', {'workspace': WORK, 'doc': token, 'scope': 'mass',
                                  'with_massprops': True}).get('volume_mm3') or 0)


def new_part():
    return run('sw_session.py', {'workspace': WORK, 'action': 'new_part',
                                 'visual': False})['doc']['doc_id']


def close(token):
    if token:
        run('sw_session.py', {'workspace': WORK, 'action': 'close', 'doc': token, 'confirm': True})


def main():
    global WORK
    workspace = None
    for index, item in enumerate(sys.argv):
        if item == '--workspace' and index + 1 < len(sys.argv):
            workspace = sys.argv[index + 1]
    WORK = os.path.abspath(workspace or os.path.join(tempfile.gettempdir(), 'cadw-live-advanced'))
    os.makedirs(WORK, exist_ok=True)

    doctor = run('cad_doctor.py', {'workspace': WORK, 'probe_com': True}, 180)
    if not doctor.get('ok') or not doctor.get('capabilities', {}).get('model'):
        print('SolidWorks 不可用，跳过高级实机测试。')
        return 2

    # 1) sweep
    token = new_part()
    sweep = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'path', 'plane': 'front', 'entities': [
            {'type': 'polyline', 'closed': False, 'points': [[0, 0], [60, 0]]},
        ]},
        {'op': 'sketch', 'id': 'profile', 'plane': 'right', 'entities': [{'type': 'circle', 'radius': 5}]},
        {'op': 'sweep', 'profile': 'profile', 'path': 'path'},
    ]}})
    check('sweep.build', sweep.get('ok') is True, sweep.get('error'))
    check('sweep.volume', abs(volume(token) - math.pi * 25 * 60) < 1.0, round(volume(token), 2))
    close(token)

    # 2) linear pattern
    token = new_part()
    linear = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'base', 'plane': 'front', 'entities': [{'type': 'rect', 'width': 120, 'height': 80}]},
        {'op': 'extrude', 'sketch': 'base', 'depth': 10},
        {'op': 'hole', 'id': 'hole1', 'at': [30, 20, 10], 'diameter': 10},
        {'op': 'linear_pattern', 'feature': 'hole1',
         'direction': {'at': [0, 40, 10]}, 'count': 4, 'spacing': 20},
    ]}})
    check('linear_pattern.build', linear.get('ok') is True, linear.get('error'))
    check('linear_pattern.holes4', abs(volume(token) - (96000 - 4 * math.pi * 25 * 10)) < 1.0,
          round(volume(token), 2))
    close(token)

    # 3) circular pattern
    token = new_part()
    circular = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'base', 'plane': 'front', 'entities': [{'type': 'rect', 'width': 120, 'height': 80}]},
        {'op': 'extrude', 'sketch': 'base', 'depth': 10},
        {'op': 'hole', 'id': 'center', 'at': [0, 0, 10], 'diameter': 10},
        {'op': 'hole', 'id': 'seed', 'at': [30, 0, 10], 'diameter': 10},
        {'op': 'circular_pattern', 'feature': 'seed', 'axis': {'at': [5, 0, 10]},
         'count': 4, 'angle': 360},
    ]}})
    check('circular_pattern.build', circular.get('ok') is True, circular.get('error'))
    check('circular_pattern.holes5', abs(volume(token) - (96000 - 5 * math.pi * 25 * 10)) < 1.0,
          round(volume(token), 2))
    close(token)

    # 4) 构造线 + 交点孔
    token = new_part()
    plate = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'plane': 'front', 'entities': [
            {'type': 'rect', 'width': 80, 'height': 60},
            {'type': 'line', 'from': [-25, -30], 'to': [-25, 30], 'construction': True},
            {'type': 'line', 'from': [25, -30], 'to': [25, 30], 'construction': True},
            {'type': 'line', 'from': [-40, -15], 'to': [40, -15], 'construction': True},
            {'type': 'line', 'from': [-40, 15], 'to': [40, 15], 'construction': True},
        ], 'holes': [
            {'diameter': 6, 'intersection': [1, 3]},
            {'diameter': 6, 'intersection': [1, 4]},
            {'diameter': 6, 'intersection': [2, 3]},
            {'diameter': 6, 'intersection': [2, 4]},
        ]},
        {'op': 'extrude', 'depth': 5},
    ]}})
    check('construction_holes.build', plate.get('ok') is True, plate.get('error'))
    expected = 80 * 60 * 5 - 4 * math.pi * 9 * 5
    check('construction_holes.volume', abs(volume(token) - expected) < 0.01,
          round(volume(token), 3))
    close(token)

    # 5) 圆柱孔同心配合
    hole_a = os.path.join(WORK, 'hole_a.sldprt')
    hole_b = os.path.join(WORK, 'hole_b.sldprt')
    for name, size in (('hole_a', 80), ('hole_b', 60)):
        token = new_part()
        built = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
            {'op': 'sketch', 'plane': 'front', 'entities': [
                {'type': 'rect', 'width': size, 'height': size},
                {'type': 'circle', 'radius': 5},
            ]},
            {'op': 'extrude', 'depth': 5},
        ]}})
        if not built.get('ok'):
            check('hole_plate.' + name, False, built.get('error'))
            close(token)
            continue
        saved_part = run('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': token,
                                           'path': os.path.join(WORK, name + '.sldprt'),
                                           'confirm': True, 'visual': False})
        close(saved_part.get('doc', {}).get('doc_id') or token)
    asm_new = run('sw_assembly.py', {'workspace': WORK, 'action': 'new'})
    asm_token = asm_new.get('doc', {}).get('doc_id')
    inserted = run('sw_assembly.py', {'workspace': WORK, 'action': 'insert', 'doc': asm_token,
        'components': [{'path': hole_a, 'fixed': True}, {'path': hole_b, 'x': 40, 'z': 20}]})
    mate = run('sw_assembly.py', {'workspace': WORK, 'action': 'mate', 'doc': asm_token, 'mates': [
        {'type': 'concentric',
         'a': {'component': 'hole_a-1', 'hole': {'at': [0, 0, 2.5], 'radius': 5}},
         'b': {'component': 'hole_b-1', 'hole': {'at': [0, 0, 2.5], 'radius': 5}}},
    ]})
    check('concentric_mate', mate.get('ok') is True and mate.get('mate_count') == 1,
          json.dumps(mate.get('error') or mate.get('mates'), ensure_ascii=False)[:300])
    close(asm_token)

    # 6) assembly
    part_a = os.path.join(WORK, 'plate.sldprt')
    part_b = os.path.join(WORK, 'pin.sldprt')
    token = new_part()
    run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'base', 'plane': 'front', 'entities': [{'type': 'rect', 'width': 40, 'height': 40}]},
        {'op': 'extrude', 'sketch': 'base', 'depth': 5},
    ]}})
    saved = run('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': token,
                                  'path': part_a, 'confirm': True, 'visual': False})
    close(saved.get('doc', {}).get('doc_id'))
    token = new_part()
    run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'id': 'base', 'plane': 'front', 'entities': [{'type': 'circle', 'radius': 5}]},
        {'op': 'extrude', 'sketch': 'base', 'depth': 20},
    ]}})
    saved = run('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': token,
                                  'path': part_b, 'confirm': True, 'visual': False})
    close(saved.get('doc', {}).get('doc_id'))

    asm_new = run('sw_assembly.py', {'workspace': WORK, 'action': 'new'})
    check('assembly.new', asm_new.get('ok') is True, asm_new.get('error'))
    if asm_new.get('ok'):
        asm_token = asm_new['doc']['doc_id']
        inserted = run('sw_assembly.py', {'workspace': WORK, 'action': 'insert', 'doc': asm_token,
            'components': [{'path': part_a, 'fixed': True},
                           {'path': part_b, 'x': 10}]})
        check('assembly.insert', inserted.get('ok') is True and inserted.get('component_count') == 2,
              inserted.get('error'))
        mates = run('sw_assembly.py', {'workspace': WORK, 'action': 'mate', 'doc': asm_token,
            'mates': [{'type': 'coincident',
                       'a': {'component': 'plate-1', 'plane': 'top'},
                       'b': {'component': 'pin-1', 'plane': 'front'}}]})
        check('assembly.mate', mates.get('ok') is True and mates.get('mate_count', 0) >= 1,
              json.dumps(mates.get('mates'), ensure_ascii=False))
        listed = run('sw_assembly.py', {'workspace': WORK, 'action': 'list', 'doc': asm_token})
        check('assembly.list', listed.get('ok') is True
              and len(listed.get('components', [])) == 2 and len(listed.get('mates', [])) >= 1)
        asm_saved = run('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': asm_token,
            'path': os.path.join(WORK, 'assembly.sldasm'), 'confirm': True, 'visual': False})
        check('assembly.save', asm_saved.get('ok') is True, asm_saved.get('error'))
        close(asm_token)

    print('\nPASS %d FAIL %d' % (len(PASS), len(FAIL)))
    for item in FAIL:
        print('  FAIL ' + item)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())