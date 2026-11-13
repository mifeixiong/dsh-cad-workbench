# -*- coding: utf-8 -*-
"""cad_doctor.py —— CAD 环境自检（只读，不新建/修改文档）。"""
import importlib.util
import os
import platform
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402


MODULES = ('win32com', 'pythoncom', 'PIL', 'numpy', 'ezdxf', 'matplotlib')


def _module_info():
    info = {}
    for name in MODULES:
        try:
            info[name] = importlib.util.find_spec(name) is not None
        except Exception:
            info[name] = False
    versions = {}
    for name in ('win32com', 'PIL', 'numpy', 'ezdxf'):
        if not info.get(name):
            continue
        try:
            module = __import__(name)
            versions[name] = getattr(module, '__version__', None) or getattr(module, 'version', None)
        except Exception:
            versions[name] = None
    return info, versions


def _com_registered():
    try:
        import winreg
        for root in (winreg.HKEY_CLASSES_ROOT,):
            try:
                key = winreg.OpenKey(root, 'SldWorks.Application')
                winreg.CloseKey(key)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return None


def _freecad():
    found = shutil.which('freecadcmd') or shutil.which('freecadcmd.exe')
    if found:
        return {'present': True, 'cmd': found, 'source': 'PATH'}
    for root in (r'C:\Program Files', r'D:\Program Files'):
        try:
            for name in os.listdir(root):
                if name.lower().startswith('freecad'):
                    candidate = os.path.join(root, name, 'bin', 'freecadcmd.exe')
                    if os.path.isfile(candidate):
                        return {'present': True, 'cmd': candidate, 'source': 'scan'}
        except Exception:
            continue
    return {'present': False}


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        handle, probe = tempfile.mkstemp(prefix='.cadw-', dir=path)
        os.close(handle)
        os.remove(probe)
        return True
    except Exception:
        return False


def main(spec):
    workspace = common.workspace_of(spec)
    modules, versions = _module_info()
    result = {
        'ok': True,
        'platform': '%s %s / Python %s' % (platform.system(), platform.release(), platform.python_version()),
        'executable': sys.executable,
        'cwd': os.getcwd(),
        'workspace': workspace,
        'modules': modules,
        'module_versions': versions,
        'state_dir': common.state_dir(),
        'state_dir_writable': _writable(common.state_dir()),
        'workspace_writable': _writable(workspace),
        'freecad': _freecad(),
        'lock': common.lock_status(),
        'solidworks': {
            'registered': _com_registered(),
            'running': False,
            'revision': None,
            'year': None,
            'templates': {'part': None, 'assembly': None, 'drawing': None},
            'documents': [],
        },
        'warnings': [],
        'suggestions': [],
    }
    if spec.get('probe_com', True) and modules.get('win32com'):
        try:
            app = common.attach(launch=False)
            result['solidworks'].update(common.version_info(app))
            result['solidworks']['running'] = True
            result['solidworks']['documents'] = [common.doc_identity(model) for model in common.open_documents(app)]
            for kind in ('part', 'assembly', 'drawing'):
                template = common.find_template(app, kind)
                result['solidworks']['templates'][kind] = template
        except common.CadError as error:
            result['solidworks']['error'] = error.envelope()['error']
            result['warnings'].append(error.message)
    else:
        for kind in ('part', 'assembly', 'drawing'):
            result['solidworks']['templates'][kind] = common.find_template(None, kind)

    templates = result['solidworks'].get('templates') or {}
    capabilities = {
        'com': bool(result['solidworks'].get('registered') and result['solidworks'].get('running')),
        'model': bool(modules.get('win32com') and templates.get('part', {}).get('path')),
        'assembly': bool(modules.get('win32com') and templates.get('assembly', {}).get('path')),
        'drawing': bool(modules.get('win32com') and templates.get('drawing', {}).get('path')),
        'capture': bool(modules.get('win32com') and modules.get('PIL')),
        'step': bool(modules.get('win32com')),
        'dxf_review': bool(modules.get('ezdxf')),
        'stl_review': True,
        'step_review': True,
        'massprops': bool(modules.get('win32com')),
    }
    result['capabilities'] = capabilities

    missing = [name for name, ok in modules.items() if not ok]
    if not modules.get('win32com'):
        result['suggestions'].append('安装 pywin32：pip install pywin32（COM 建模/截图/出图需要）')
    if not modules.get('PIL'):
        result['suggestions'].append('安装 Pillow：pip install Pillow（截图 PNG 与拼图需要）')
    if not modules.get('ezdxf'):
        result['suggestions'].append('安装 ezdxf：pip install ezdxf（DXF 图纸审查需要；不影响建模）')
    if not templates.get('part', {}).get('path'):
        result['suggestions'].append('找不到零件模板：先手动启动一次 SolidWorks 并确认默认模板，或设置 CADW_PART_TEMPLATE')
    if not result['solidworks'].get('running'):
        result['suggestions'].append('SolidWorks 未运行：启动到主界面后再建模（cadw_session 会尝试拉起）')
    if result['lock'].get('held') and not result['lock'].get('stale'):
        result['suggestions'].append('检测到 SolidWorks 锁被占用；等另一个会话结束，或确认卡死后删除 %s' % os.path.join(common.state_dir(), 'sw.lock'))
    if not templates.get('drawing', {}).get('path'):
        result['suggestions'].append('找不到工程图模板：cadw_drawing 会报 UNSUPPORTED；可在 SolidWorks 里新建一次工程图生成默认模板')
    if missing:
        result['suggestions'].append('缺少模块：' + ', '.join(missing))
    result['summary'] = '平台 %s；SolidWorks %s；能力 %s' % (
        result['platform'],
        result['solidworks'].get('revision') or '未连接',
        ', '.join(name for name, ok in capabilities.items() if ok) or '无',
    )
    return result


if __name__ == '__main__':
    common.run(main)
