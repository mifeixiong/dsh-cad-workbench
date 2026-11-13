# -*- coding: utf-8 -*-
"""sw_common / sw_build 的离线回归（不需要 SolidWorks）。

覆盖：
  * Hole 世界坐标 → 草图坐标的变换方向（P0：Top 面镜像 bug）；
  * 导出/另存的「残留旧文件冒充新产物」防护；
  * OS 级跨进程锁互斥（不再有 stale 接管竞态）；
  * NaN 安全 JSON、basename 消毒、graph.validate 存在性。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')
sys.path.insert(0, SCRIPTS)

import graph  # noqa: E402
import sw_build  # noqa: E402
import sw_common  # noqa: E402


FAILURES = []


def check(name, condition, detail=''):
    print(('  PASS ' if condition else '  FAIL ') + name + ' ' + str(detail)[:200])
    if not condition:
        FAILURES.append(name)


def test_transform():
    class Transform:
        ArrayData = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0,
                     0.0, 0.0, -0.01, 1.0, 0.0, 0.0, 0.0]

    class Sketch:
        ModelToSketchTransform = Transform()

    sx, sy = sw_build._world_to_sketch(Sketch(), 20, 10, -15)
    check('transform.top_face', abs(sx - 20) < 1e-6 and abs(sy - 15) < 1e-6, '(%s, %s)' % (sx, sy))
    sx, sy = sw_build._world_to_sketch(Sketch(), 0, 0, 0)
    check('transform.origin', abs(sx - 0) < 1e-6 and abs(sy - 0) < 1e-6, '(%s, %s)' % (sx, sy))


class NoopModel:
    class Extension:
        def SaveAs3(self, *args):
            return None

    def SaveAs3(self, *args):
        return None


class GoodModel:
    class Extension:
        def SaveAs3(self, path, *args):
            with open(path, 'wb') as handle:
                handle.write(b'NEW')

    def SaveAs3(self, path, *args):
        with open(path, 'wb') as handle:
            handle.write(b'NEW')


def test_export_guard():
    directory = tempfile.mkdtemp(prefix='cadw-common-')
    target = os.path.join(directory, 'part.step')
    with open(target, 'wb') as handle:
        handle.write(b'OLD')
    before = os.stat(target)
    try:
        sw_common.export_document(NoopModel(), target, overwrite=True)
        check('export.noop_raises', False, '没有抛错')
    except sw_common.CadError as error:
        check('export.noop_raises', error.code in ('UNSUPPORTED', 'EXPORT_FAILED'), error.code)
    check('export.old_restored', open(target, 'rb').read() == b'OLD')
    after = os.stat(target)
    check('export.old_mtime_restored', before.st_mtime_ns == after.st_mtime_ns,
          '%s -> %s' % (before.st_mtime_ns, after.st_mtime_ns))
    result = sw_common.export_document(GoodModel(), target, overwrite=True)
    check('export.success_new_content', open(target, 'rb').read() == b'NEW', result)
    leftovers = [name for name in os.listdir(directory) if '.bak-' in name]
    check('export.no_backup_left', not leftovers, leftovers)


def test_json_safe():
    safe = sw_common._json_safe({'a': float('nan'), 'b': float('inf'), 'c': [1.0, float('-inf')]})
    check('json.nan_to_null', safe['a'] is None and safe['b'] is None and safe['c'][1] is None, safe)
    check('json.dumps_ok', json.dumps(safe, allow_nan=False) is not None)
    check('basename.sanitized', sw_common.safe_basename('../x/y:z', 'fb') in ('..-x-y-z', 'x-y-z', '..-x-y-z'), sw_common.safe_basename('../x/y:z', 'fb'))


def test_lock():
    state = tempfile.mkdtemp(prefix='cadw-lock-')
    log = os.path.join(state, 'events.log')
    worker = os.path.join(state, 'worker.py')
    with open(worker, 'w', encoding='utf-8') as handle:
        handle.write(
            'import sys, time\n'
            'sys.path.insert(0, %r)\n'
            'import sw_common\n'
            'with sw_common.sw_lock("worker"):\n'
            '    with open(%r, "a") as f:\n'
            '        f.write("ACQ %%f\\n" %% time.time()); f.flush()\n'
            '    time.sleep(0.35)\n'
            '    with open(%r, "a") as f:\n'
            '        f.write("REL %%f\\n" %% time.time()); f.flush()\n'
            % (SCRIPTS, log, log)
        )
        handle.flush()
    env = dict(os.environ, CADW_STATE_DIR=state, CADW_LOCK_TIMEOUT_MS='5000',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for _ in range(3):
        for _ in range(2):
            subprocess.Popen([sys.executable, worker], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        if len(open(log).read().splitlines()) >= 4:
            break
    lines = [line.split() for line in open(log).read().splitlines() if line.strip()]
    events = [(kind, float(ts)) for kind, ts in lines]
    check('lock.both_acquired', len([e for e in events if e[0] == 'ACQ']) >= 2, events)
    intervals = []
    start = None
    for kind, ts in events:
        if kind == 'ACQ':
            start = ts
        elif kind == 'REL' and start is not None:
            intervals.append((start, ts))
            start = None
    intervals.sort()
    overlap = any(intervals[i][1] > intervals[i + 1][0] + 0.05 for i in range(len(intervals) - 1))
    check('lock.no_overlap', not overlap, intervals)


def test_graph_entry():
    check('graph.validate_exists', callable(getattr(graph, 'validate', None)))
    check('graph.supported_ops', 'extrude' in graph.SUPPORTED_OPS)


def main():
    test_transform()
    test_export_guard()
    test_json_safe()
    test_lock()
    test_graph_entry()
    print('\ncommon_cases: 失败 %d' % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
