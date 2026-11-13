# -*- coding: utf-8 -*-
"""live_regressions.py —— 针对实机审计发现的 P0/P1 回归（需要 SolidWorks）。

用法：python test/live_regressions.py [--workspace DIR]
覆盖：Top 面孔位、倒角角度、闭合 polyline、失败回滚、open/save。
"""
import json
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


def main():
    global WORK
    workspace = None
    for index, item in enumerate(sys.argv):
        if item == '--workspace' and index + 1 < len(sys.argv):
            workspace = sys.argv[index + 1]
    WORK = os.path.abspath(workspace or os.path.join(tempfile.gettempdir(), 'cadw-live-regressions'))
    os.makedirs(WORK, exist_ok=True)
    pid = os.getpid()

    r = run('cad_doctor.py', {'workspace': WORK, 'probe_com': True}, 180)
    if not r.get('ok') or not r.get('capabilities', {}).get('model'):
        print('SolidWorks 不可用，跳过实机回归。')
        return 2

    token = run('sw_session.py', {'workspace': WORK, 'action': 'new_part', 'visual': False})['doc']['doc_id']
    run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'plane': 'top', 'entities': [{'type': 'rect', 'width': 120, 'height': 80}]},
        {'op': 'extrude', 'depth': 10},
    ]}})
    hole = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'hole', 'at': [20, 10, -15], 'diameter': 10},
    ]}})
    check('hole.top_face', hole.get('ok') is True, hole.get('error'))
    measured = run('sw_measure.py', {'workspace': WORK, 'doc': token, 'scope': 'mass', 'with_massprops': True})
    centroid = measured.get('centroid_mm') or [0, 0, 0]
    check('hole.position_x', abs(centroid[0] + 0.16496) < 0.08, 'x=%.4f' % centroid[0])
    check('hole.position_z', abs(centroid[2] - 0.12372) < 0.08, 'z=%.4f' % centroid[2])

    before = measured.get('volume_mm3') or 0
    chamfer = run('sw_build.py', {'workspace': WORK, 'doc': token, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'chamfer', 'distance': 5, 'angle': 45, 'edges': [{'at': [60, 5, 40]}]},
    ]}})
    check('chamfer.build', chamfer.get('ok') is True, chamfer.get('error'))
    after = run('sw_measure.py', {'workspace': WORK, 'doc': token, 'scope': 'mass', 'with_massprops': True})
    removed = before - (after.get('volume_mm3') or 0)
    check('chamfer.angle_degrees', abs(removed - 125.0) < 2.0, 'removed=%.3f' % removed)

    saved = run('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': token,
                                  'path': os.path.join(WORK, 'reg_%d.sldprt' % pid),
                                  'confirm': True, 'visual': False})
    check('save_as', saved.get('ok') is True, saved.get('error'))
    token = saved.get('doc', {}).get('doc_id')
    closed = run('sw_session.py', {'workspace': WORK, 'action': 'close', 'doc': token, 'confirm': True})
    opened = run('sw_session.py', {'workspace': WORK, 'action': 'open',
                                   'path': os.path.join(WORK, 'reg_%d.sldprt' % pid), 'visual': False})
    check('open.saved_file', opened.get('ok') is True, opened.get('error'))
    if opened.get('ok'):
        run('sw_session.py', {'workspace': WORK, 'action': 'close',
                              'doc': opened['doc']['doc_id'], 'confirm': True})

    token2 = run('sw_session.py', {'workspace': WORK, 'action': 'new_part', 'visual': False})['doc']['doc_id']
    poly = run('sw_build.py', {'workspace': WORK, 'doc': token2, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'sketch', 'plane': 'front', 'entities': [
            {'type': 'polyline', 'closed': True, 'points': [[0, 0], [10, 0], [10, 10], [0, 10]]},
        ]},
        {'op': 'extrude', 'depth': 2},
    ]}})
    check('polyline.closed', poly.get('ok') is True, poly.get('error'))
    check('polyline.volume', abs((run('sw_measure.py', {
        'workspace': WORK, 'doc': token2, 'scope': 'mass', 'with_massprops': True,
    }).get('volume_mm3') or 0) - 200.0) < 0.1)
    before_count = run('sw_measure.py', {'workspace': WORK, 'doc': token2, 'scope': 'summary'}).get('feature_count')
    failed = run('sw_build.py', {'workspace': WORK, 'doc': token2, 'graph': {'units': 'mm', 'nodes': [
        {'op': 'hole', 'at': [999, 999, 999], 'diameter': 5},
    ]}})
    details = (failed.get('error') or {}).get('details') or {}
    after_count = run('sw_measure.py', {'workspace': WORK, 'doc': token2, 'scope': 'summary'}).get('feature_count')
    check('rollback.clean', details.get('rollback', {}).get('done') is True and before_count == after_count,
          '%s -> %s' % (before_count, after_count))
    run('sw_session.py', {'workspace': WORK, 'action': 'close', 'doc': token2, 'confirm': True})

    print('\nPASS %d FAIL %d' % (len(PASS), len(FAIL)))
    for item in FAIL:
        print('  FAIL ' + item)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
