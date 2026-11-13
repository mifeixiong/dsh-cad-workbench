# -*- coding: utf-8 -*-
"""live_windows.py —— 已打开窗口管理的实机验证（需要 SolidWorks）。

覆盖：
  * list 能读到用户已打开的窗口（GetActiveObject 失败时 Dispatch 回退）；
  * 新建/建模后的脏文档：close 默认 CONFIRM_REQUIRED、dry_run 只预览；
  * confirm=true 丢弃关闭（QuitDoc），关闭后列表里消失；
  * 已保存文档关闭（CloseDoc）、重新打开、再关闭；
  * docs 批量关闭。

不会关闭测试开始前已存在的文档；结束时把新建的测试文档清理掉。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PASS, FAIL = [], []
WORK = None


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
                                        'message': (process.stdout + process.stderr)[-600:]}}
    result['_exit'] = process.returncode
    return result


def session(**spec):
    payload = {'workspace': WORK}
    payload.update(spec)
    return run('sw_session.py', payload)


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(('  PASS ' if condition else '  FAIL ') + name + ' ' + str(detail)[:300])


def list_docs():
    result = session(action='list')
    return result, {item['doc_id']: item for item in (result.get('documents') or [])}


def make_dirty_part(label):
    created = session(action='new_part', visual=False)
    if not created.get('ok'):
        return None, created
    token = created['doc']['doc_id']
    built = run('sw_build.py', {'workspace': WORK, 'doc': token, 'visual': False,
                                'graph': {'units': 'mm', 'nodes': [
                                    {'op': 'sketch', 'plane': 'front',
                                     'entities': [{'type': 'rect', 'width': 20, 'height': 10}]},
                                    {'op': 'extrude', 'depth': 5},
                                ]}})
    if not built.get('ok'):
        return None, built
    return token, built


def main():
    global WORK
    WORK = tempfile.mkdtemp(prefix='cadw-live-windows-')
    doctor = run('cad_doctor.py', {'workspace': WORK, 'probe_com': True}, 180)
    if not doctor.get('ok') or not doctor.get('capabilities', {}).get('model'):
        print('SolidWorks 不可用，跳过窗口实机验证。')
        return 2

    initial, before = list_docs()
    check('list.connected', initial.get('ok') is True and initial.get('connected') is True, initial.get('error'))
    initial_ids = set(before)
    initial_active = initial.get('active_doc_id')
    print('  初始窗口 %d 个；测试不会关闭它们' % len(initial_ids))
    created_tokens = []

    try:
        # 1) 脏文档：默认拒绝、dry_run 不改状态、confirm 丢弃
        token, built = make_dirty_part('dirty')
        if not token:
            check('dirty.create', False, built)
            return 1
        created_tokens.append(token)
        listed, docs = list_docs()
        entry = docs.get(token)
        check('dirty.listed', entry is not None and entry.get('saved') is False and
              entry.get('window') is True, entry)
        refused = session(action='close', doc=token)
        check('dirty.refused', refused.get('ok') is False and
              refused['error']['code'] == 'CONFIRM_REQUIRED', refused.get('error'))
        still, docs = list_docs()
        check('dirty.still_open', token in docs, list(docs))
        preview = session(action='close', doc=token, dry_run=True)
        check('dirty.preview', preview.get('ok') is True and preview.get('dry_run') is True and
              preview.get('requires_confirm') is True, preview)
        plan = (preview.get('plan') or [{}])[0]
        check('dirty.preview_plan', plan.get('action') == 'blocked', plan)
        closed = session(action='close', doc=token, confirm=True)
        check('dirty.closed', closed.get('ok') is True and
              len(closed.get('closed_documents') or []) == 1, closed)
        check('dirty.discarded', len(closed.get('discarded') or []) == 1, closed.get('discarded'))
        still, docs = list_docs()
        check('dirty.gone', token not in docs, list(docs))
        created_tokens.remove(token)

        # 2) 已保存文档：CloseDoc、重新打开、再关闭
        token, built = make_dirty_part('saved')
        if not token:
            check('saved.create', False, built)
            return 1
        created_tokens.append(token)
        target = os.path.join(WORK, 'live_windows_%d.sldprt' % os.getpid())
        saved = session(action='save_as', doc=token, path=target, confirm=True, visual=False)
        check('saved.save_as', saved.get('ok') is True, saved.get('error'))
        if saved.get('ok'):
            token = saved['doc']['doc_id']
            created_tokens.remove(created_tokens[-1])
            created_tokens.append(token)
        closed = session(action='close', doc=token)
        check('saved.closed', closed.get('ok') is True and
              closed.get('closed_documents'), closed)
        still, docs = list_docs()
        check('saved.gone', token not in docs, list(docs))
        created_tokens.remove(token)
        opened = session(action='open', path=target, visual=False)
        check('saved.reopened', opened.get('ok') is True, opened.get('error'))
        if opened.get('ok'):
            opened_token = opened['doc']['doc_id']
            created_tokens.append(opened_token)
            closed = session(action='close', doc=opened_token)
            check('saved.reclosed', closed.get('ok') is True, closed)
            created_tokens.remove(opened_token)
        try:
            os.remove(target)
        except OSError:
            pass

        # 3) docs 批量关闭两个脏文档
        token_a, built_a = make_dirty_part('batch-a')
        token_b, built_b = make_dirty_part('batch-b')
        if token_a and token_b:
            created_tokens.extend([token_a, token_b])
            closed = session(action='close', docs=[token_a, token_b], confirm=True)
            check('batch.closed', closed.get('ok') is True and
                  len(closed.get('closed_documents') or []) == 2, closed)
            still, docs = list_docs()
            check('batch.gone', token_a not in docs and token_b not in docs, list(docs))
            created_tokens.remove(token_a)
            created_tokens.remove(token_b)
        else:
            check('batch.create', False, built_a if not token_a else built_b)

        # 4) 初始窗口仍在
        still, docs = list_docs()
        check('initial.intact', initial_ids.issubset(set(docs)), sorted(initial_ids - set(docs)))
        if initial_active:
            activated = session(action='activate', doc=initial_active, visual=False)
            check('initial.reactivate', activated.get('ok') is True, activated.get('error'))
    finally:
        # 清理本测试新建但未关掉的文档；绝不碰初始文档
        for token in list(created_tokens):
            session(action='close', doc=token, confirm=True)
        still, docs = list_docs()
        leftovers = [token for token in created_tokens if token in docs]
        check('cleanup.no_temp_left', not leftovers, leftovers)

    print('\nPASS %d FAIL %d' % (len(PASS), len(FAIL)))
    for item in FAIL:
        print('  FAIL ' + item)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
