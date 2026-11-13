# -*- coding: utf-8 -*-
"""一条命令跑完离线测试（不需要 SolidWorks）。"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHILD_ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')


def run(label, command, cwd=ROOT):
    print('\n=== %s ===' % label)
    result = subprocess.run(command, cwd=cwd, env=CHILD_ENV)
    print('--- %s exit=%s' % (label, result.returncode))
    return result.returncode


def main():
    python = sys.executable
    codes = []
    codes.append(run('common_cases', [python, os.path.join(HERE, 'common_cases.py')]))
    codes.append(run('session_cases', [python, os.path.join(HERE, 'session_cases.py')]))
    codes.append(run('graph_cases', [python, os.path.join(HERE, 'graph_cases.py')]))
    codes.append(run('review_cases', [python, os.path.join(HERE, 'review_cases.py')]))
    if '--python-only' in sys.argv:
        failed = sum(1 for code in codes if code != 0)
        print('\n总计 %d 项，失败 %d（--python-only）' % (len(codes), failed))
        return 1 if failed else 0
    node = shutil.which('node')
    if node:
        codes.append(run('selftest', [node, '--test', os.path.join(HERE, 'selftest.mjs')]))
    else:
        candidates = [
            os.path.join(os.path.expanduser('~'), '.dsh', 'dsh-runtimes', 'dsh-primary-runtime',
                         'dependencies', 'node', 'bin', 'node.exe'),
        ]
        found = next((path for path in candidates if os.path.isfile(path)), None)
        if found:
            codes.append(run('selftest', [found, '--test', os.path.join(HERE, 'selftest.mjs')]))
        else:
            print('\n跳过 JS 自测：找不到 node（可安装 Node 20+ 或设置 PATH）')
    failed = sum(1 for code in codes if code != 0)
    print('\n总计 %d 项，失败 %d' % (len(codes), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
