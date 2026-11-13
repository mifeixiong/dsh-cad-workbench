# -*- coding: utf-8 -*-
"""live_smoke.py —— SolidWorks 实机端到端验收（无 SolidWorks 时自动跳过）。

用法：
    python test/live_smoke.py [--workspace E:\\code\\_cad_verify\\live]

覆盖：新建零件 → 板+孔+圆角 → 实测 → 4 视图 → STEP/STL/PDF → 工程图(A3,4视图,pdf/dxf/dwg)
→ DXF/STEP/STL 审查 → 精确回滚 → 过期令牌拒绝 → 跨进程锁。
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
if ROOT + os.sep + 'scripts' not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))

PASS = []
FAIL = []


def check(name, condition, detail=''):
    if condition:
        PASS.append(name)
        print('  PASS %s %s' % (name, detail))
    else:
        FAIL.append('%s %s' % (name, detail))
        print('  FAIL %s %s' % (name, detail))
    return bool(condition)


def run_script(script, spec, timeout=600):
    spec_path = os.path.join(WORK, '_spec_%s.json' % script.replace('.py', ''))
    with open(spec_path, 'w', encoding='utf-8') as handle:
        json.dump(spec, handle, ensure_ascii=False)
    process = subprocess.run([PY, os.path.join(ROOT, 'scripts', script), '--spec', spec_path],
                             capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(process.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {'ok': False, 'error': {'code': 'NO_JSON', 'message': process.stdout[-500:] + process.stderr[-500:]}}
    payload['_exit'] = process.returncode
    return payload


def sw_pid():
    try:
        output = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq SLDWORKS.exe', '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, timeout=20).stdout
        for line in output.splitlines():
            parts = [item.strip().strip('"') for item in line.split(',')]
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
    except Exception:
        pass
    return None


def cleanup_docs(tokens):
    for token in tokens:
        if not token:
            continue
        try:
            run_script('sw_session.py', {'workspace': WORK, 'action': 'close', 'doc': token, 'confirm': True})
        except Exception:
            pass


def main():
    global WORK
    workspace = None
    for index, item in enumerate(sys.argv):
        if item == '--workspace' and index + 1 < len(sys.argv):
            workspace = sys.argv[index + 1]
    WORK = os.path.abspath(workspace or os.path.join(os.path.dirname(HERE), '..', '_cad_verify', 'live'))
    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    images_dir = os.path.join(WORK, 'captures')
    exports_dir = os.path.join(WORK, 'exports')
    drawings_dir = os.path.join(WORK, 'drawings')

    print('== 1. doctor ==')
    doctor = run_script('cad_doctor.py', {'workspace': WORK, 'probe_com': True}, timeout=180)
    check('doctor.ok', doctor.get('ok') is True, json.dumps(doctor.get('error', {}), ensure_ascii=False)[:200])
    if not doctor.get('ok'):
        print('SolidWorks 不可用，跳过实机验收。')
        return 2
    check('doctor.model', bool(doctor.get('capabilities', {}).get('model')))
    check('doctor.drawing', bool(doctor.get('capabilities', {}).get('drawing')))

    tokens = []
    print('== 2. 新建零件 ==')
    created = run_script('sw_session.py', {'workspace': WORK, 'action': 'new_part', 'visual': False}, timeout=180)
    check('session.new_part', created.get('ok') is True, json.dumps(created.get('error', {}), ensure_ascii=False)[:200])
    if not created.get('ok'):
        return 1
    token = created['doc']['doc_id']
    tokens.append(token)

    print('== 3. 板 + 孔 + 圆角 ==')
    plate = run_script('sw_build.py', {
        'workspace': WORK, 'doc': token,
        'graph': {'units': 'mm', 'nodes': [
            {'op': 'sketch', 'plane': 'front', 'entities': [{'type': 'rect', 'width': 120, 'height': 80}]},
            {'op': 'extrude', 'depth': 10},
        ]},
        'expect': {'bbox_mm': [120, 80, 10]}, 'rollback_on_fail': True,
    })
    check('build.plate', plate.get('ok') is True, json.dumps(plate.get('error', {}), ensure_ascii=False)[:200])
    check('build.plate.bbox', plate.get('measurements', {}).get('bbox_mm') == [120.0, 80.0, 10.0],
          str(plate.get('measurements', {}).get('bbox_mm')))
    features_after_plate = plate.get('features_after')

    hole = run_script('sw_build.py', {
        'workspace': WORK, 'doc': token,
        'graph': {'units': 'mm', 'nodes': [
            {'op': 'hole', 'at': [30, 20, 10], 'diameter': 10, 'through_all': True},
        ]},
    })
    check('build.hole', hole.get('ok') is True, json.dumps(hole.get('error', {}), ensure_ascii=False)[:200])
    measured = run_script('sw_measure.py', {'workspace': WORK, 'doc': token, 'scope': 'mass',
                                            'with_massprops': True})
    volume = measured.get('volume_mm3')
    expect_volume = 120 * 80 * 10 - 3.141592653589793 * 25 * 10
    check('measure.volume', volume is not None and abs(volume - expect_volume) / expect_volume < 0.01,
          '实测 %.1f，期望 %.1f' % (volume or -1, expect_volume))

    fillet = run_script('sw_build.py', {
        'workspace': WORK, 'doc': token,
        'graph': {'units': 'mm', 'nodes': [
            {'op': 'fillet', 'radius': 5, 'edges': [
                {'at': [60, 40, 5]}, {'at': [-60, 40, 5]},
                {'at': [60, -40, 5]}, {'at': [-60, -40, 5]},
            ]},
        ]},
    })
    check('build.fillet', fillet.get('ok') is True, json.dumps(fillet.get('error', {}), ensure_ascii=False)[:200])

    print('== 4. 保存 ==')
    part_path = os.path.join(WORK, 'plate_live.sldprt')
    saved = run_script('sw_session.py', {'workspace': WORK, 'action': 'save_as', 'doc': token,
                                         'path': part_path, 'confirm': True, 'visual': False})
    check('session.save_as', saved.get('ok') is True and os.path.isfile(part_path),
          json.dumps(saved.get('error', {}), ensure_ascii=False)[:200])
    if saved.get('ok'):
        token = saved['doc']['doc_id']
        tokens.append(token)

    print('== 5. 截图 ==')
    captured = run_script('sw_capture.py', {
        'workspace': WORK, 'doc': token, 'views': ['iso', 'front', 'top', 'right'],
        'width': 1000, 'height': 700, 'contact_sheet': True,
        'out_dir': images_dir, 'basename': 'plate_live',
    }, timeout=300)
    check('capture.ok', captured.get('ok') is True, json.dumps(captured.get('error', {}), ensure_ascii=False)[:200])
    images = captured.get('images', [])
    check('capture.views', len(images) >= 4, '实际 %d' % len(images))
    check('capture.not_blank', bool(images) and not any(item.get('blank') for item in images))

    print('== 6. 导出 ==')
    exported = run_script('sw_export.py', {
        'workspace': WORK, 'doc': token, 'formats': ['step', 'stl', 'pdf'],
        'out_dir': exports_dir, 'basename': 'plate_live', 'overwrite': True,
    }, timeout=300)
    check('export.ok', exported.get('ok') is True, json.dumps(exported.get('error', {}), ensure_ascii=False)[:200])
    artifacts = exported.get('artifacts', [])
    check('export.formats', len(artifacts) == 3, '实际 %d' % len(artifacts))
    for item in artifacts:
        check('export.%s' % item['format'], os.path.isfile(item['path']) and (item.get('bytes') or 0) > 100)

    print('== 7. 工程图 ==')
    drawing = run_script('sw_drawing.py', {
        'workspace': WORK, 'doc': token, 'sheet': 'A3',
        'views': ['front', 'top', 'right', 'iso'], 'first_angle': True,
        'insert_model_dims': True, 'export': ['pdf', 'dxf', 'dwg'],
        'out_dir': drawings_dir, 'basename': 'plate_live', 'overwrite': True,
    }, timeout=600)
    check('drawing.ok', drawing.get('ok') is True, json.dumps(drawing.get('error', {}), ensure_ascii=False)[:300])
    check('drawing.views', drawing.get('view_count', 0) >= 4, '实际 %s' % drawing.get('view_count'))
    check('drawing.pdf', any(item['format'] == 'pdf' and os.path.isfile(item['path'])
                             for item in drawing.get('artifacts', [])))
    if drawing.get('ok'):
        tokens.append(drawing.get('drawing', {}).get('doc_id'))

    print('== 8. 审查 ==')
    review_paths = [os.path.join(drawings_dir, 'plate_live.dxf'),
                    os.path.join(exports_dir, 'plate_live.step'),
                    os.path.join(exports_dir, 'plate_live.stl')]
    reviewed = run_script('cad_review.py', {'workspace': WORK, 'paths': review_paths}, timeout=180)
    check('review.ok', reviewed.get('ok') is True, json.dumps(reviewed.get('error', {}), ensure_ascii=False)[:200])
    verdicts = {item['kind']: item['verdict'] for item in reviewed.get('files', [])}
    check('review.stl', verdicts.get('stl') == 'PASS', str(verdicts))
    check('review.step', verdicts.get('step') == 'PASS', str(verdicts))
    check('review.dxf', verdicts.get('dxf') in ('PASS', 'WARN'), str(verdicts))

    print('== 9. 回滚 ==')
    rollback = run_script('sw_build.py', {
        'workspace': WORK, 'doc': token,
        'graph': {'units': 'mm', 'nodes': [
            {'op': 'sketch', 'plane': 'front', 'entities': [{'type': 'rect', 'center': [200, 0], 'width': 20, 'height': 20}]},
            {'op': 'extrude', 'depth': 5},
            {'op': 'fillet', 'radius': 2, 'edges': [{'at': [999, 999, 999]}]},
        ]},
        'rollback_on_fail': True,
    }, timeout=300)
    details = rollback.get('error', {}).get('details', {})
    check('rollback.rejected', rollback.get('ok') is False and rollback.get('error', {}).get('code') == 'BUILD_FAILED')
    check('rollback.done', details.get('rollback', {}).get('done') is True, json.dumps(details.get('rollback', {}), ensure_ascii=False)[:200])
    check('rollback.count', details.get('features_before') == details.get('features_after'),
          '%s -> %s' % (details.get('features_before'), details.get('features_after')))

    print('== 10. 过期令牌 ==')
    stale = run_script('sw_build.py', {
        'workspace': WORK, 'doc': '0000000000000000',
        'graph': {'units': 'mm', 'nodes': [{'op': 'sketch', 'plane': 'front',
                                            'entities': [{'type': 'circle', 'radius': 5}]}]},
    })
    check('token.rejected', stale.get('ok') is False and stale.get('error', {}).get('code') == 'DOC_NOT_FOUND',
          str(stale.get('error', {}).get('code')))

    print('== 11. 锁 ==')
    pid = sw_pid()
    lock_path = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), 'dsh-cad-workbench', 'sw.lock')
    if pid:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, 'w', encoding='utf-8') as handle:
            json.dump({'pid': pid, 'tool': 'live-smoke-lock', 'token': 't',
                       'timestamp': time.time()}, handle)
        os.environ['CADW_LOCK_TIMEOUT_MS'] = '2000'
        started = time.time()
        busy = run_script('sw_measure.py', {'workspace': WORK, 'doc': token, 'scope': 'summary'}, timeout=120)
        elapsed = time.time() - started
        try:
            os.remove(lock_path)
        except Exception:
            pass
        os.environ.pop('CADW_LOCK_TIMEOUT_MS', None)
        check('lock.busy', busy.get('ok') is False and busy.get('error', {}).get('code') == 'DOC_BUSY',
              str(busy.get('error', {}).get('code')))
        check('lock.waited', elapsed >= 1.5, '等待 %.1fs' % elapsed)
    else:
        print('  跳过锁测试：找不到 SLDWORKS.exe 进程')

    print('== 12. 清理 ==')
    cleanup_docs(tokens)
    print('\n结果：PASS %d，FAIL %d' % (len(PASS), len(FAIL)))
    for item in FAIL:
        print('  FAIL ' + item)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
