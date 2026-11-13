# -*- coding: utf-8 -*-
"""
sw_common.py —— SolidWorks COM 基础设施（仅本插件内部使用）。

设计原则：
  * 只用 IDispatch 后期绑定（win32com.client.dynamic），不依赖类型库；
  * 长度在边界处换算：插件对外 mm，SolidWorks API 用米；
  * 文档身份用显式句柄/路径，绝不用 ActiveDoc 当"我要操作谁"；
  * 失败给结构化错误码，不把 COM traceback 直接甩给模型；
  * 跨进程文件锁，保证多个会话/子代理串行使用同一个 SolidWorks。

参考的 API 事实来自本机实测与公开 API 文档；代码为独立实现。
"""
import contextlib
import datetime
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback

MM = 0.001

SW_END_BLIND = 0
SW_END_THROUGH = 1
SW_REV_BLIND = 0
SW_FILLET_UNIFORM_RADIUS = 2
SW_FILLET_SIMPLE = 0
SW_CHAMFER_ANGLE_DIST = 1

VIEWS = {
    'iso': ('*Isometric', 7),
    'front': ('*Front', 1),
    'back': ('*Back', 2),
    'left': ('*Left', 3),
    'right': ('*Right', 4),
    'top': ('*Top', 5),
    'bottom': ('*Bottom', 6),
}

PLANE_ALIASES = {
    'front': ('Front Plane', '前视基准面'),
    'top': ('Top Plane', '上视基准面'),
    'right': ('Right Plane', '右视基准面'),
}

PLANE_FALLBACK = {
    'Front Plane': {'normal_world': [0.0, 0.0, 1.0], 'sketch_y_world': [0.0, 1.0, 0.0], 'sketch_y_sign': 1.0},
    'Top Plane': {'normal_world': [0.0, 1.0, 0.0], 'sketch_y_world': [0.0, 0.0, -1.0], 'sketch_y_sign': -1.0},
    'Right Plane': {'normal_world': [1.0, 0.0, 0.0], 'sketch_y_world': [0.0, 1.0, 0.0], 'sketch_y_sign': 1.0},
}

EXIT_CODES = {
    'GRAPH_INVALID': 2,
    'DOC_NOT_FOUND': 3,
    'DOC_BUSY': 4,
    'COM_UNAVAILABLE': 5,
    'BUILD_FAILED': 6,
    'EXPORT_FAILED': 7,
    'UNSUPPORTED': 8,
    'TIMEOUT': 9,
    'ROLLBACK_FAILED': 10,
}


class CadError(Exception):
    """带错误码的失败。"""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def envelope(self):
        error = {'code': self.code, 'message': self.message}
        if self.details is not None:
            error['details'] = self.details
        return {'ok': False, 'error': error}


# --------------------------------------------------------------------------
# 输入 / 输出
# --------------------------------------------------------------------------
def read_spec():
    """读取 --spec 指向的 JSON；缺省空对象。"""
    argv = sys.argv[1:]
    if '--spec' not in argv:
        return {}
    path = argv[argv.index('--spec') + 1]
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def _json_safe(value):
    """把 NaN/±Infinity 转成 null，保证输出是合法 JSON（JSON.parse 不接受 NaN）。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def emit(payload):
    """输出且只输出一个 JSON 对象到 stdout。"""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    text = json.dumps(_json_safe(payload), ensure_ascii=False, default=str, allow_nan=False)
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def run(handler):
    """脚本统一入口：异常 → 结构化错误信封；退出码按错误码映射。"""
    spec = read_spec()
    try:
        result = handler(spec)
        if not isinstance(result, dict):
            result = {'ok': True, 'value': result}
        result.setdefault('ok', True)
    except CadError as error:
        result = error.envelope()
    except Exception as error:  # noqa: BLE001 - 边界处统一收口
        log_path = None
        try:
            logs = ensure_dir(os.path.join(state_dir(), 'logs'))
            log_path = os.path.join(logs, 'error-%s.log' % datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            with open(log_path, 'w', encoding='utf-8') as handle:
                handle.write(traceback.format_exc())
        except Exception:
            log_path = None
        details = {'exception': error.__class__.__name__}
        if log_path:
            details['log'] = log_path
        result = {
            'ok': False,
            'error': {
                'code': 'INTERNAL',
                'message': str(error) or error.__class__.__name__,
                'details': details,
            },
        }
    emit(result)
    code = 0
    if result.get('ok') is False:
        code = EXIT_CODES.get((result.get('error') or {}).get('code'), 1)
    sys.exit(code)


def workspace_of(spec):
    value = spec.get('workspace')
    if value:
        return os.path.abspath(value)
    return os.getcwd()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def safe_basename(value, fallback='output'):
    """文件名前缀消毒：去路径分隔符、.. 与 Windows 非法字符，保留中文。"""
    raw = str(value or '').strip()
    if not raw:
        return fallback
    cleaned = re.sub(r'[\\/]+', '-', raw)
    cleaned = cleaned.replace('..', '-')
    cleaned = re.sub(r'[:*?"<>|]', '-', cleaned)
    cleaned = cleaned.strip(' .-')
    return cleaned[:120] or fallback


def sha256_file(path):
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


# --------------------------------------------------------------------------
# 跨进程锁
# --------------------------------------------------------------------------
def state_dir():
    override = os.environ.get('CADW_STATE_DIR')
    if override:
        return override
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'dsh-cad-workbench')


def _pid_alive(pid):
    """跨平台判断 pid 是否存活。

    Windows 上 os.kill(pid, 0) 不可靠（会报 OSError），改用 OpenProcess +
    GetExitCodeProcess；失败再退回 tasklist 解析。POSIX 走 os.kill(pid, 0)。
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    code = wintypes.DWORD()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                        return code.value == STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
                return False
        except Exception:
            pass
        try:
            import subprocess
            output = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH', '/FO', 'CSV'],
                                    capture_output=True, text=True, timeout=10).stdout
            for line in output.splitlines():
                parts = [item.strip().strip('"') for item in line.split(',')]
                if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) == pid:
                    return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _try_os_lock(fd):
    """尝试对 fd 加独占非阻塞锁；成功返回 True。

    用 OS 文件锁而不是 O_EXCL + pid/TTL 启发式：进程崩溃由内核自动释放，
    不存在「读到 stale → remove 前被别人抢到新锁」的 check-then-delete 竞态。
    """
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_os_lock(fd):
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass


def _read_lock_info(path):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            return json.load(stream)
    except Exception:
        return {}


@contextlib.contextmanager
def sw_lock(tool, timeout_ms=None, stale_seconds=900, workspace=None):
    """独占 SolidWorks 的跨进程锁（OS 文件锁）。等待超时抛 DOC_BUSY。"""
    directory = ensure_dir(state_dir())
    path = os.path.join(directory, 'sw.lock')
    timeout = (timeout_ms if timeout_ms is not None
               else int(os.environ.get('CADW_LOCK_TIMEOUT_MS', '120000'))) / 1000.0
    deadline = time.time() + max(1.0, timeout)
    fd = None
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR)
        except OSError as error:
            raise CadError('DOC_BUSY', '无法打开 SolidWorks 锁文件：%s' % error)
        if _try_os_lock(fd):
            break
        try:
            os.close(fd)
        except Exception:
            pass
        fd = None
        if time.time() >= deadline:
            info = _read_lock_info(path)
            raise CadError(
                'DOC_BUSY',
                '另一个会话正在使用 SolidWorks（%s，pid=%s）。本工具最多等待 %.0f 秒；'
                '请稍后重试，或确认没有卡死的建模进程。'
                % (info.get('tool') or '未知工具', info.get('pid') or '?', timeout),
                {'lock': info},
            )
        time.sleep(0.1)
    try:
        info = {'pid': os.getpid(), 'tool': tool, 'token': '%s-%s' % (os.getpid(), time.time()),
                'acquired_at': now_iso(), 'timestamp': time.time()}
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(info).encode('utf-8'))
        except Exception:
            pass
        yield
    finally:
        try:
            _release_os_lock(fd)
        finally:
            try:
                os.close(fd)
            except Exception:
                pass


def lock_status():
    """只读探测锁状态：真正试加一次 OS 锁，拿得到说明当前无人持有。"""
    path = os.path.join(state_dir(), 'sw.lock')
    if not os.path.isfile(path):
        return {'held': False, 'method': 'os-lock'}
    info = _read_lock_info(path)
    fd = None
    try:
        fd = os.open(path, os.O_RDWR)
        if _try_os_lock(fd):
            _release_os_lock(fd)
            return {'held': False, 'method': 'os-lock', 'last': info or None}
        info['held'] = True
        info['method'] = 'os-lock'
        return info
    except Exception:
        return {'held': True, 'unreadable': True}
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


# --------------------------------------------------------------------------
# COM 连接
# --------------------------------------------------------------------------
def _win32com():
    try:
        import pythoncom
        import win32com.client
        return pythoncom, win32com.client
    except Exception as error:  # noqa: BLE001
        raise CadError(
            'COM_UNAVAILABLE',
            '缺少 pywin32（win32com），无法连接 SolidWorks。请用带 pywin32 的 Python 运行，或设置 CADW_PYTHON。',
            {'import_error': str(error)},
        )


def _com_error_codes(error):
    codes = []
    try:
        codes.append(int(getattr(error, 'hresult', 0)))
    except Exception:
        pass
    try:
        if error.args:
            codes.append(int(error.args[0]))
    except Exception:
        pass
    return codes


RETRYABLE = {-2147418111, -2147417846, -2147418104, -2147023174, -2147417848}


def retry_busy(function, *args, attempts=6, delay=0.6, **kwargs):
    """COM 忙（RPC_E_CALL_REJECTED 等）时原地退避重试，不重跑整个脚本。"""
    last = None
    for index in range(attempts):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001
            codes = _com_error_codes(error)
            if not any(code in RETRYABLE for code in codes):
                raise
            last = error
            time.sleep(delay * (1.4 ** index))
    raise CadError(
        'DOC_BUSY',
        'SolidWorks 正忙，重试 %d 次仍被拒绝（COM 调用未返回）。请稍后重试。' % attempts,
        {'last_error': str(last)},
    )


def attach(launch=False):
    """连接 SolidWorks。launch=False 时只附着到已运行实例。"""
    pythoncom, win32 = _win32com()
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    client = win32.dynamic
    try:
        if launch:
            app = client.Dispatch('SldWorks.Application')
        else:
            app = win32.GetActiveObject('SldWorks.Application')
    except Exception as error:  # noqa: BLE001
        raise CadError(
            'COM_UNAVAILABLE',
            ('无法连接 SolidWorks。请先启动 SolidWorks 到主界面，并确认已授权；'
             '若刚安装，请重启一次。') if not launch else
            ('无法启动/连接 SolidWorks（SldWorks.Application 未注册或安装损坏）。'),
            {'error': str(error)},
        )
    try:
        app.Visible = True
    except Exception:
        pass
    return app


def version_info(app):
    revision = ''
    try:
        revision = str(app.RevisionNumber)
    except Exception:
        pass
    year = None
    try:
        major = int(float(revision.split('.')[0]))
        year = major + 1992
    except Exception:
        pass
    return {'revision': revision, 'year': year}


def _call_or_value(obj, name, *args):
    value = getattr(obj, name)
    if callable(value):
        return value(*args) if args else value()
    return value


# --------------------------------------------------------------------------
# 模板
# --------------------------------------------------------------------------
TEMPLATE_KINDS = {
    'part': ('.prtdot', 'CADW_PART_TEMPLATE'),
    'assembly': ('.asmdot', 'CADW_ASM_TEMPLATE'),
    'drawing': ('.drwdot', 'CADW_DRW_TEMPLATE'),
}
TEMPLATE_PREFS = {'part': 101, 'assembly': 102, 'drawing': 103}


def _template_dirs(app):
    dirs = []
    if app is not None:
        for pref in (108, 109, 110, 111, 101, 102, 103):
            try:
                value = app.GetUserPreferenceStringValue(pref)
            except Exception:
                continue
            if not value:
                continue
            if os.path.isdir(value):
                dirs.append(value)
            elif os.path.isfile(value):
                dirs.append(os.path.dirname(value))
    for pattern in (
        r'C:\ProgramData\SolidWorks\SOLIDWORKS *\templates',
        r'D:\ProgramData\SolidWorks\SOLIDWORKS *\templates',
    ):
        dirs.extend(glob.glob(pattern))
    return dirs


def find_template(app, kind, prefer=None):
    extension, env_name = TEMPLATE_KINDS[kind]
    override = os.environ.get(env_name)
    if override and os.path.isfile(override):
        return {'path': override, 'source': 'env:' + env_name}
    if app is not None:
        try:
            direct = app.GetUserPreferenceStringValue(TEMPLATE_PREFS[kind])
            if direct and os.path.isfile(direct) and direct.lower().endswith(extension):
                return {'path': direct, 'source': 'user_preference'}
        except Exception:
            pass
    candidates = []
    for directory in _template_dirs(app):
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, '*' + extension)):
            candidates.append(path)
    prefers = []
    if prefer:
        prefers = [str(item).lower() for item in (prefer if isinstance(prefer, (list, tuple)) else [prefer])]
    def score(path):
        lowered = os.path.basename(path).lower()
        value = 0
        if any(token in lowered for token in prefers):
            value += 50
        if any(token in lowered for token in ('gb_', 'gb-', 'part', '零件')):
            value += 5
        if 'ansi' in lowered or 'iso' in lowered:
            value += 1
        return value
    candidates.sort(key=score, reverse=True)
    if candidates:
        return {'path': candidates[0], 'source': 'scan'}
    return {'path': None, 'source': 'missing', 'searched': _template_dirs(app)}


# --------------------------------------------------------------------------
# 文档身份
# --------------------------------------------------------------------------
def doc_identity(model):
    path = ''
    try:
        path = str(model.GetPathName or '')
    except Exception:
        path = ''
    title = ''
    try:
        title = str(model.GetTitle)
    except Exception:
        title = ''
    type_name = 'unknown'
    try:
        value = int(model.GetType)
        type_name = {1: 'part', 2: 'assembly', 3: 'drawing'}.get(value, 'unknown')
    except Exception:
        pass
    saved = None
    try:
        flag = model.GetSaveFlag
        if flag is not None:
            saved = not bool(flag)
    except Exception:
        pass
    key = os.path.normcase(os.path.abspath(path)) if path else 'unsaved:' + title
    token = hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
    return {'doc_id': token, 'title': title, 'path': path, 'type': type_name, 'saved': saved}


def open_documents(app):
    documents = []
    try:
        array = app.GetDocuments
    except Exception:
        array = None
    for model in array or []:
        if model is None:
            continue
        try:
            documents.append(model)
        except Exception:
            continue
    return documents


def find_document(app, token):
    if not token:
        raise CadError('DOC_NOT_FOUND', '缺少 doc 令牌：请先调用 cadw_session 拿到 doc_id。')
    for model in open_documents(app):
        try:
            identity = doc_identity(model)
        except Exception:
            continue
        if identity['doc_id'] == token:
            return model, identity
    raise CadError(
        'DOC_NOT_FOUND',
        '找不到 doc_id=%s 的已打开文档（可能已关闭或换了文档）。请重新调用 cadw_session 获取令牌。' % token,
        {'open_docs': [doc_identity(model) for model in open_documents(app)]},
    )


def verify_identity(model, expected_token):
    identity = doc_identity(model)
    if identity['doc_id'] != expected_token:
        raise CadError(
            'DOC_IDENTITY_MISMATCH',
            '文档身份已变化（期望 %s，实际 %s）。为防止把特征画到别的文档，本次写入已拒绝。'
            % (expected_token, identity['doc_id']),
            {'expected': expected_token, 'actual': identity},
        )
    return identity


def activate(app, model):
    """把文档切到前台（UI 用；写操作不依赖它）。"""
    try:
        identity = doc_identity(model)
        name = identity['path'] or identity['title']
        import pythoncom
        import win32com.client
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        retry_busy(app.ActivateDoc3, name, False, 0, errors)
    except CadError:
        raise
    except Exception:
        pass
    return model


# --------------------------------------------------------------------------
# 草图与特征
# --------------------------------------------------------------------------
def _select_plane(model, plane):
    if plane in PLANE_FALLBACK:
        names = (plane,)
    else:
        names = (plane,)
    empty = None
    try:
        import pythoncom
        import win32com.client
        empty = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception:
        empty = None
    last_error = None
    for name in names:
        try:
            selected = model.Extension.SelectByID2(name, 'PLANE', 0, 0, 0, False, 0, empty, 0)
            if selected:
                return name
        except Exception as error:  # noqa: BLE001
            last_error = error
    raise CadError(
        'BUILD_FAILED',
        '选不中基准面 %s。可用 front/top/right 或模板里的基准面名称。' % plane,
        {'error': str(last_error) if last_error else None},
    )


def resolve_plane_name(plane):
    key = str(plane or '').strip()
    lowered = key.lower()
    if lowered in PLANE_ALIASES:
        return PLANE_ALIASES[lowered]
    if key in PLANE_FALLBACK:
        return key
    return key


def select_plane(model, plane):
    for candidate in _plane_candidates(plane):
        try:
            if _select_plane(model, candidate):
                return candidate
        except CadError:
            continue
    raise CadError('BUILD_FAILED', '选不中基准面 %s（尝试过 %s）。' % (plane, ', '.join(_plane_candidates(plane))))


def _plane_candidates(plane):
    key = str(plane or '').strip()
    lowered = key.lower()
    names = []
    if lowered in PLANE_ALIASES:
        names.extend(PLANE_ALIASES[lowered])
    else:
        names.append(key)
        reverse = {
            'front plane': ('前视基准面',),
            'top plane': ('上视基准面',),
            'right plane': ('右视基准面',),
        }
        names.extend(reverse.get(lowered, ()))
    names.append(key)
    return [name for index, name in enumerate(names) if name and name not in names[:index]]


def select_face_at(model, x_mm, y_mm, z_mm, append=False):
    empty = _empty_variant()
    selected = model.Extension.SelectByID2('', 'FACE', x_mm * MM, y_mm * MM, z_mm * MM, bool(append), 0, empty, 0)
    if not selected:
        raise CadError('BUILD_FAILED', '在 (%s, %s, %s) mm 处选不中面。' % (x_mm, y_mm, z_mm))
    return True


def _empty_variant():
    try:
        import pythoncom
        import win32com.client
        return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception:
        return None


def start_sketch(model, plane=None, face=None):
    if face is not None:
        if isinstance(face, dict):
            select_face_at(model, float(face.get('x', 0)), float(face.get('y', 0)), float(face.get('z', 0)))
        else:
            select_face_at(model, float(face[0]), float(face[1]), float(face[2]))
    else:
        select_plane(model, plane or 'Front Plane')
    model.SketchManager.InsertSketch(True)
    return model.SketchManager.ActiveSketch


def end_sketch(model):
    try:
        active = model.SketchManager.ActiveSketch
        if active is not None:
            try:
                active.MergePoints(0.0005)
            except Exception:
                pass
    except Exception:
        pass
    model.SketchManager.InsertSketch(True)
    # 不在每次结束草图时 ZoomToFit：非视觉模式纯浪费；视觉模式由调用方显式 zoom。
    return last_sketch_name(model)


SKETCH_TYPES = ('profilefeature', 'sketch', 'sketchblockdefinition')


def is_sketch_feature(item):
    type_name = str(item.get('type', '')).lower()
    name = str(item.get('name', '')).lower()
    return type_name in SKETCH_TYPES or 'sketch' in type_name or 'sketch' in name or name.startswith('草图')


def last_sketch_name(model):
    for item in reversed(feature_list(model)):
        if is_sketch_feature(item):
            return item['name']
    return None


def feature_list(model):
    """优先 FeatureManager.GetFeatures(True)（一次拿到全部特征）；失败再退回链式遍历。"""
    features = []
    try:
        array = model.FeatureManager.GetFeatures(True)
        for feature in array or []:
            try:
                features.append({'name': str(_call_or_value(feature, 'Name')),
                                 'type': str(_call_or_value(feature, 'GetTypeName2'))})
            except Exception:
                continue
        if features:
            return features
    except Exception:
        pass
    try:
        feature = model.FirstFeature
    except Exception:
        feature = None
    guard = 0
    while feature is not None and guard < 500:
        guard += 1
        try:
            features.append({'name': str(_call_or_value(feature, 'Name')),
                             'type': str(_call_or_value(feature, 'GetTypeName2'))})
        except Exception:
            pass
        try:
            feature = feature.GetNextFeature
        except Exception:
            feature = None
    return features


def feature_count(model):
    return len(feature_list(model))


def sketch_entities(sketch, spec):
    """在 ISketchManager 上创建草图实体（mm）。

    注意：实体创建走 ISketchManager（model.SketchManager），不是 ISketch；
    ISketch 只用来取 ModelToSketchTransform 等草图信息。
    """
    created = 0
    for entity in spec:
        kind = entity.get('type')
        if kind == 'rect':
            cx, cy = entity.get('center', [0, 0])
            width, height = float(entity['width']), float(entity['height'])
            sketch.CreateCornerRectangle((cx - width / 2) * MM, (cy + height / 2) * MM, 0,
                                         (cx + width / 2) * MM, (cy - height / 2) * MM, 0)
            created += 1
        elif kind == 'circle':
            cx, cy = entity.get('center', [0, 0])
            sketch.CreateCircleByRadius(cx * MM, cy * MM, 0, float(entity['radius']) * MM)
            created += 1
        elif kind == 'polygon':
            import math
            cx, cy = entity.get('center', [0, 0])
            radius = float(entity['radius'])
            sides = int(entity['sides'])
            rotation = float(entity.get('rotation', 0))
            points = []
            for index in range(sides):
                angle = math.radians(rotation + 360.0 * index / sides)
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
            for index in range(sides):
                x1, y1 = points[index]
                x2, y2 = points[(index + 1) % sides]
                sketch.CreateLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
            created += 1
        elif kind == 'polyline':
            points = [(float(x), float(y)) for x, y in entity['points']]
            closed = entity.get('closed', True)
            total = len(points) if closed else len(points) - 1
            for index in range(total):
                x1, y1 = points[index]
                x2, y2 = points[(index + 1) % len(points)]
                sketch.CreateLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
            created += 1
        elif kind == 'centerline':
            x1, y1 = entity['from']
            x2, y2 = entity['to']
            sketch.CreateCenterLine(x1 * MM, y1 * MM, 0, x2 * MM, y2 * MM, 0)
            created += 1
        elif kind == 'arc':
            cx, cy = entity.get('center', [0, 0])
            radius = float(entity['radius'])
            start = float(entity.get('start_deg', 0))
            end = float(entity.get('end_deg', 90))
            import math
            sx, sy = cx + radius * math.cos(math.radians(start)), cy + radius * math.sin(math.radians(start))
            ex, ey = cx + radius * math.cos(math.radians(end)), cy + radius * math.sin(math.radians(end))
            method = getattr(sketch, 'CreateArc', None)
            if method is None:
                raise CadError('UNSUPPORTED', '当前 SolidWorks 不支持 CreateArc；请改用 polyline 近似。')
            method(cx * MM, cy * MM, 0, sx * MM, sy * MM, 0, ex * MM, ey * MM, 0, 1)
            created += 1
        else:
            raise CadError('UNSUPPORTED', '不支持的草图实体：%s（v0.1 支持 rect/circle/polygon/polyline/centerline/arc）。' % kind)
    return created


def select_sketch(model, name):
    empty = _empty_variant()
    try:
        selected = model.Extension.SelectByID2(str(name), 'SKETCH', 0, 0, 0, False, 0, empty, 0)
    except Exception as error:  # noqa: BLE001
        raise CadError('BUILD_FAILED', '选中草图 %s 失败：%s' % (name, error))
    if not selected:
        raise CadError('BUILD_FAILED', '选中草图 %s 失败（草图名可能已变）。' % name)
    return True


def extrude(model, sketch_name, depth_mm, midplane=False, flip=False):
    select_sketch(model, sketch_name)
    end_condition = _midplane_enum(model) if midplane else SW_END_BLIND
    feature = retry_busy(
        model.FeatureManager.FeatureExtrusion3,
        True, bool(flip), False, end_condition, 0, float(depth_mm) * MM, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, True, False, True,
        0, 0, False,
    )
    if feature is None:
        raise CadError('BUILD_FAILED', '拉伸失败（FeatureExtrusion3 返回 None）。')
    return feature


def cut(model, sketch_name, depth_mm=None, through=False, flip=False):
    select_sketch(model, sketch_name)
    end_condition = SW_END_THROUGH if through else SW_END_BLIND
    depth = float(depth_mm or 0) * MM
    feature = retry_busy(
        model.FeatureManager.FeatureCut3,
        True, bool(flip), False, end_condition, 0, depth, 0,
        False, False, False, False, 0, 0,
        False, False, False, False, False, False, True,
        False, False, False, 0, 0, False,
    )
    if feature is None:
        raise CadError('BUILD_FAILED', '切除失败（FeatureCut3 返回 None）。')
    return feature


def revolve(model, sketch_name, angle_deg=360.0, cut_mode=False):
    import math
    select_sketch(model, sketch_name)
    feature = retry_busy(
        model.FeatureManager.FeatureRevolve2,
        True, True, False, bool(cut_mode), False, False,
        SW_REV_BLIND, 0, math.radians(float(angle_deg)), 0,
        False, False, 0, 0, 0, 0, 0,
        True, False, True,
    )
    if feature is None:
        raise CadError('BUILD_FAILED', '旋转失败（FeatureRevolve2 返回 None）。')
    return feature


def _body_edges(model):
    edges = []
    for args in ((0, False), (0, True)):
        try:
            bodies = model.GetBodies2(*args)
        except Exception:
            continue
        for body in bodies or []:
            if body is None:
                continue
            try:
                edges.extend(body.GetEdges() or [])
            except Exception:
                continue
        if edges:
            break
    return edges


def _edge_midpoint(edge):
    try:
        start = edge.GetStartVertex
        end = edge.GetEndVertex
        if start is None or end is None:
            return None
        p1 = _call_or_value(start, 'GetPoint')
        p2 = _call_or_value(end, 'GetPoint')
        return [(float(p1[index]) + float(p2[index])) / 2.0 for index in range(3)]
    except Exception:
        return None


def select_edges_by_points(model, points, tolerance_mm=1.5):
    """按世界坐标中点匹配实体边并选择，不依赖当前视图可见性。

    SelectByID2 按坐标选边在边被遮挡时会失败；遍历实体边、比较中点更稳。
    """
    candidates = []
    for edge in _body_edges(model):
        midpoint = _edge_midpoint(edge)
        if midpoint is not None:
            candidates.append([edge, midpoint, False])
    selected = 0
    for index, target in enumerate(points):
        if isinstance(target, dict):
            point = [float(value) for value in target['at']]
            tolerance = float(target.get('tol', tolerance_mm))
        else:
            point = [float(value) for value in target[:3]]
            tolerance = tolerance_mm
        goal = [value * MM for value in point]
        best = None
        best_distance = 1e18
        for item in candidates:
            if item[2]:
                continue
            midpoint = item[1]
            distance = sum((midpoint[axis] - goal[axis]) ** 2 for axis in range(3)) ** 0.5 * 1000.0
            if distance < best_distance:
                best_distance = distance
                best = item
        if best is None or best_distance > tolerance:
            return selected, '在 (%s, %s, %s) mm 附近找不到边（最近 %s mm，容差 %s mm）' % (
                point[0], point[1], point[2], round(best_distance, 3), tolerance), index
        try:
            best[0].Select(selected > 0)
        except Exception:
            return selected, '边选择失败（索引 %d）' % index, index
        best[2] = True
        selected += 1
    return selected, None, None


def _select_edges(model, edges):
    try:
        model.ClearSelection2(True)
    except Exception:
        pass
    selected, error, index = select_edges_by_points(model, edges)
    if error is None and selected == len(edges):
        return True
    # 退路：按坐标 SelectByID2（可能受视图遮挡影响）
    empty = _empty_variant()
    for position, edge in enumerate(edges):
        if isinstance(edge, dict):
            x, y, z = float(edge['at'][0]), float(edge['at'][1]), float(edge['at'][2])
            tolerance = float(edge.get('tol', 0.2))
        else:
            x, y, z = [float(value) for value in edge[:3]]
            tolerance = 0.2
        selected_now = model.Extension.SelectByID2('', 'EDGE', x * MM, y * MM, z * MM, True, 0, empty, 0)
        if not selected_now:
            raise CadError('BUILD_FAILED', '在 (%s, %s, %s) mm 处选不中边（容差 %s mm）。' % (x, y, z, tolerance))
    return True


def fillet(model, radius_mm, edges):
    _select_edges(model, edges)
    feature = retry_busy(
        model.FeatureManager.FeatureFillet3,
        SW_FILLET_UNIFORM_RADIUS, float(radius_mm) * MM, 0, 0, SW_FILLET_SIMPLE, 0, 0,
        None, None, None, None, None, None, None,
    )
    if feature is None:
        raise CadError('BUILD_FAILED', '圆角失败（FeatureFillet3 返回 None）。')
    return feature


def chamfer(model, distance_mm, edges, angle_deg=45.0):
    _select_edges(model, edges)
    feature = None
    try:
        feature = model.FeatureManager.InsertFeatureChamfer(
            0, SW_CHAMFER_ANGLE_DIST, float(distance_mm) * MM, math.radians(float(angle_deg)), 0, 0, 0, 0)
    except Exception:
        feature = None
    if feature is None:
        try:
            feature = model.FeatureManager.FeatureChamferType(
                SW_CHAMFER_ANGLE_DIST, float(distance_mm) * MM, math.radians(float(angle_deg)), False, 0, 0, 0, 0)
        except Exception:
            feature = None
    if feature is None:
        raise CadError('BUILD_FAILED', '倒角失败（InsertFeatureChamfer / FeatureChamferType 都返回 None）。')
    return feature


def delete_feature(model, name):
    """按特征名删除（先选后 DeleteSelection2）。返回是否删除成功。"""
    empty = _empty_variant()
    for type_name in ('BODYFEATURE', 'SKETCH', 'FEATURE', 'CUTLISTFOLDER'):
        try:
            model.ClearSelection2(True)
            selected = model.Extension.SelectByID2(str(name), type_name, 0, 0, 0, False, 0, empty, 0)
        except Exception:
            continue
        if not selected:
            continue
        try:
            result = model.Extension.DeleteSelection2(0)
        except Exception:
            continue
        if result:
            return True
    return False


def rollback_features(model, before_count, feature_names):
    """按名逐件删除本次创建的特征（逆序），再复验特征数。"""
    info = {'attempted': True, 'done': False, 'before_count': before_count,
            'restored_count': None, 'deleted': [], 'failed': []}
    for name in reversed([item for item in feature_names if item]):
        if delete_feature(model, name):
            info['deleted'].append(name)
        else:
            info['failed'].append(name)
        try:
            model.ClearSelection2(True)
        except Exception:
            pass
    try:
        model.ForceRebuild3(False)
    except Exception:
        pass
    remaining = [item['name'] for item in feature_list(model)]
    info['restored_count'] = len(remaining)
    info['remaining_created'] = [name for name in feature_names if name in remaining]
    info['done'] = not info['remaining_created']
    return info


def select_all_edges(model):
    """选中当前文档所有实体的所有边（用于 edges:"all"）。"""
    selected = 0
    try:
        bodies = model.GetBodies2(0, False)
    except Exception:
        bodies = None
    if not bodies:
        try:
            bodies = model.GetBodies2(0, True)
        except Exception:
            bodies = None
    for body in bodies or []:
        if body is None:
            continue
        try:
            edges = body.GetEdges() or []
        except Exception:
            continue
        for edge in edges:
            try:
                append = selected > 0
                value = getattr(edge, 'Select', None)
                if value is None:
                    continue
                ok = value(append) if callable(value) else None
                if ok is False:
                    continue
                selected += 1
            except Exception:
                continue
    if not selected:
        raise CadError('BUILD_FAILED', '选不中任何边（edges:"all" 失败）。')
    return selected


def fillet_selected(model, radius_mm):
    feature = retry_busy(
        model.FeatureManager.FeatureFillet3,
        SW_FILLET_UNIFORM_RADIUS, float(radius_mm) * MM, 0, 0, SW_FILLET_SIMPLE, 0, 0,
        None, None, None, None, None, None, None,
    )
    if feature is None:
        raise CadError('BUILD_FAILED', '圆角失败（FeatureFillet3 返回 None）。')
    return feature


def chamfer_selected(model, distance_mm, angle_deg=45.0):
    feature = None
    try:
        feature = model.FeatureManager.InsertFeatureChamfer(
            0, SW_CHAMFER_ANGLE_DIST, float(distance_mm) * MM, math.radians(float(angle_deg)), 0, 0, 0, 0)
    except Exception:
        feature = None
    if feature is None:
        raise CadError('BUILD_FAILED', '倒角失败（InsertFeatureChamfer 返回 None）。')
    return feature


def _midplane_enum(model):
    try:
        major = int(float(str(model.RevisionNumber).split('.')[0]))
    except Exception:
        major = 32
    return 6 if major >= 28 else 5


def rebuild(model):
    try:
        model.ForceRebuild3(False)
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------
# 测量
# --------------------------------------------------------------------------
def bbox_mm(model):
    boxes = []
    for args in ((0, False), (0, True)):
        try:
            bodies = model.GetBodies2(*args)
        except Exception:
            continue
        for body in bodies or []:
            if body is None:
                continue
            try:
                box = body.GetBodyBox()
            except Exception:
                continue
            if not box:
                continue
            values = [float(value) for value in box]
            if len(values) >= 6:
                boxes.append(values[:6])
        if boxes:
            break
    if not boxes:
        return None
    minimum = [min(box[index] for box in boxes) for index in range(3)]
    maximum = [max(box[index + 3] for box in boxes) for index in range(3)]
    return {
        'min_mm': [round(value * 1000, 6) for value in minimum],
        'max_mm': [round(value * 1000, 6) for value in maximum],
        'size_mm': [round((maximum[index] - minimum[index]) * 1000, 6) for index in range(3)],
        'sources': len(boxes),
    }


def mass_properties(model):
    value = None
    try:
        value = model.GetMassProperties
        if callable(value):
            value = value()
    except Exception:
        value = None
    if value is None:
        try:
            value = model.Extension.CreateMassProperty2
            if callable(value):
                value = value()
        except Exception:
            value = None
    if value is None:
        raise CadError('UNSUPPORTED', '读取质量属性失败（GetMassProperties 返回 None）。')
    values = [float(item) for item in value]
    if len(values) < 6:
        raise CadError('UNSUPPORTED', '质量属性数组长度异常：%d' % len(values))
    volume = values[3]
    return {
        'volume_mm3': volume * 1e9,
        'surface_area_mm2': values[4] * 1e6,
        'mass_kg': values[5],
        'density_kg_m3': (values[5] / volume) if volume else None,
        'centroid_mm': [values[0] * 1000, values[1] * 1000, values[2] * 1000],
    }


def body_counts(model):
    solids = 0
    faces = 0
    edges = 0
    for args in ((0, False), (0, True)):
        try:
            bodies = model.GetBodies2(*args)
        except Exception:
            continue
        if bodies:
            solids = len([body for body in bodies if body is not None])
            for body in bodies:
                try:
                    faces += len(body.GetFaces() or [])
                except Exception:
                    pass
                try:
                    edges += len(body.GetEdges() or [])
                except Exception:
                    pass
            break
    return {'solid_count': solids, 'face_count': faces or None, 'edge_count': edges or None}


PLANE_FAMILY_NAMES = {
    'front plane': ('前视基准面', '前视基准面1'),
    'top plane': ('上视基准面', '上视基准面1'),
    'right plane': ('右视基准面', '右视基准面1'),
}


def plane_name_candidates(plane_name):
    key = str(plane_name or '').strip()
    candidates = [key]
    for extra in PLANE_FAMILY_NAMES.get(key.lower(), ()):
        if extra not in candidates:
            candidates.append(extra)
    return candidates


def plane_axes_world(model, plane_name):
    """查基准面的世界轴向（IRefPlane::Transform）；查不到退回经验表并标注 source。"""
    candidates = plane_name_candidates(plane_name)
    feature = None
    matched_name = plane_name
    try:
        feature = model.FirstFeature
    except Exception:
        feature = None
    guard = 0
    while feature is not None and guard < 500:
        guard += 1
        try:
            current_name = str(_call_or_value(feature, 'Name'))
            if current_name in candidates:
                matched_name = current_name
                break
        except Exception:
            pass
        try:
            feature = feature.GetNextFeature
        except Exception:
            feature = None
    raw = None
    if feature is not None:
        for getter in ('GetSpecificFeature2', 'GetSpecificFeature'):
            try:
                value = getattr(feature, getter)
                value = value() if callable(value) else value
                if value is not None:
                    transform = value.Transform
                    raw = list(transform.ArrayData)
                    break
            except Exception:
                continue
    if raw and len(raw) >= 9:
        matrix = [float(value) for value in raw[:9]]
        x_axis = [matrix[0], matrix[1], matrix[2]]
        y_axis = [matrix[3], matrix[4], matrix[5]]
        normal = [matrix[6], matrix[7], matrix[8]]
        def unit(vector):
            length = sum(value * value for value in vector) ** 0.5
            return [round(value / length, 6) for value in vector] if length else vector
        return {
            'name': matched_name,
            'source': 'IRefPlane::Transform',
            'normal_world': unit(normal),
            'sketch_x_world': unit(x_axis),
            'sketch_y_world': unit(y_axis),
            'sketch_y_sign': -1.0 if unit(y_axis)[2] < -0.5 else 1.0,
            'raw': [round(value, 6) for value in raw[:16]],
        }
    fallback = PLANE_FALLBACK.get(plane_name)
    if fallback:
        return {
            'name': plane_name,
            'source': 'fallback_table',
            'normal_world': fallback['normal_world'],
            'sketch_y_world': fallback['sketch_y_world'],
            'sketch_y_sign': fallback['sketch_y_sign'],
            'note': '读不到 IRefPlane::Transform；退回经验表，精密镜像判断前请先排查。',
        }
    return {'name': plane_name, 'source': 'unknown', 'note': '既不是默认基准面，也读不到 Transform。'}


def sketch_audit(model):
    sketches = []
    for item in feature_list(model):
        type_name = str(item.get('type', '')).lower()
        if 'sketch' not in type_name and 'profile' not in type_name:
            continue
        entry = {'name': item['name'], 'type': item['type']}
        try:
            feature = model.Extension.GetObjectByName and None
        except Exception:
            feature = None
        sketches.append(entry)
    return sketches


# --------------------------------------------------------------------------
# 保存 / 导出
# --------------------------------------------------------------------------
def _snapshot(path):
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def _written(path, before):
    """本次确实写出：存在、非空，且（若原来已存在）mtime/size 与导出前不同。"""
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return False
    if before is None:
        return True
    return _snapshot(path) != before


def _set_aside(target):
    """把已存在的目标改名备份，避免 SaveAs 失败时旧文件冒充新产物。"""
    if not os.path.exists(target):
        return None
    backup = '%s.bak-%d-%d' % (target, os.getpid(), int(time.time() * 1000))
    try:
        os.replace(target, backup)
        return backup
    except Exception:
        return None


def _restore_backup(backup, target):
    if backup and os.path.exists(backup):
        try:
            os.replace(backup, target)
        except Exception:
            pass


def _drop_backup(backup):
    if backup:
        try:
            os.remove(backup)
        except Exception:
            pass


def _same_path(left, right):
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except Exception:
        return False


def save(model, path=None):
    if path:
        target = os.path.abspath(path)
        ensure_dir(os.path.dirname(target))
        before = _snapshot(target)
        backup = _set_aside(target)
        last = None
        try:
            for options in (1, 0):
                try:
                    retry_busy(model.SaveAs3, target, 0, options)
                except Exception as error:  # noqa: BLE001
                    last = error
                identity = doc_identity(model)
                if identity.get('path') and _same_path(identity['path'], target) and _written(target, before):
                    _drop_backup(backup)
                    return {'path': target, 'bytes': os.path.getsize(target), 'doc': identity}
            raise CadError('EXPORT_FAILED', '另存为失败：%s 未生成或未切换文档路径。' % target,
                           {'last_error': str(last) if last else None})
        except Exception:
            _restore_backup(backup, target)
            raise
    identity = doc_identity(model)
    if not identity.get('path'):
        raise CadError('UNSUPPORTED', '文档还没有保存到磁盘；请用 save_as 指定路径。')
    try:
        retry_busy(model.Save)
        return {'path': identity['path'], 'bytes': None}
    except Exception as error:  # noqa: BLE001
        try:
            result = retry_busy(model.Save3, 1, 1, 0)
            if result == 0:
                return {'path': identity['path'], 'bytes': None}
        except Exception:
            pass
        raise CadError('EXPORT_FAILED', '保存失败：%s' % error)


EXPORT_MAP = {
    'step': '.step',
    'iges': '.igs',
    'stl': '.stl',
    '3mf': '.3mf',
    'pdf': '.pdf',
    'png': '.png',
    'dxf': '.dxf',
    'dwg': '.dwg',
}


def export_document(model, target, overwrite=False):
    target = os.path.abspath(target)
    if os.path.exists(target) and not overwrite:
        raise CadError('EXPORT_FAILED', '目标已存在，拒绝覆盖：%s（确认覆盖请传 confirm=true）。' % target)
    ensure_dir(os.path.dirname(target))
    before = _snapshot(target)
    backup = _set_aside(target)
    last = None
    try:
        for options in (1, 0, 2):
            attempts = (
                lambda: retry_busy(model.Extension.SaveAs3, target, 0, options, None, None),
                lambda: retry_busy(model.SaveAs3, target, 0, options),
            )
            for attempt in attempts:
                try:
                    attempt()
                except Exception as error:  # noqa: BLE001
                    last = error
                if _written(target, before):
                    _drop_backup(backup)
                    return {'path': target, 'bytes': os.path.getsize(target)}
        raise CadError(
            'UNSUPPORTED',
            '导出失败：%s 未生成（格式可能需要额外授权/转换器）。' % os.path.basename(target),
            {'last_error': str(last) if last else None},
        )
    except Exception:
        _restore_backup(backup, target)
        raise


# --------------------------------------------------------------------------
# 图像
# --------------------------------------------------------------------------
def bmp_to_png(bmp_path, png_path=None):
    from PIL import Image
    png_path = png_path or os.path.splitext(bmp_path)[0] + '.png'
    with Image.open(bmp_path) as image:
        image.convert('RGB').save(png_path, 'PNG')
    return png_path


def image_stats(path):
    from PIL import Image
    with Image.open(path) as image:
        rgb = image.convert('RGB')
        width, height = rgb.size
        small = rgb.resize((min(240, width), min(240, height)))
        pixels = list(small.getdata())
        total = len(pixels)
        if not total:
            return {'width': width, 'height': height, 'blank': True, 'unique_colors': 0}
        gray = [(r * 299 + g * 587 + b * 114) / 1000.0 for r, g, b in pixels]
        mean = sum(gray) / total
        variance = sum((value - mean) ** 2 for value in gray) / total
        stddev = variance ** 0.5
        white = sum(1 for value in gray if value > 245) / total
        colors = len(set(pixels))
    blank = stddev < 3.0 or colors < 8 or white > 0.995
    return {
        'width': width,
        'height': height,
        'mean': round(mean, 3),
        'stddev': round(stddev, 3),
        'white_ratio': round(white, 4),
        'unique_colors': colors,
        'blank': bool(blank),
    }


def make_contact_sheet(images, out_path, title=None):
    """2×2 拼图；images: [{path, view}]。"""
    from PIL import Image, ImageDraw
    if not images:
        raise CadError('EXPORT_FAILED', '没有可拼图的视图。')
    tiles = []
    for item in images:
        with Image.open(item['path']) as image:
            tiles.append((item.get('view', ''), image.convert('RGB').copy()))
    columns = 2
    rows = (len(tiles) + 1) // 2
    tile_width = max(tile.width for _, tile in tiles)
    tile_height = max(tile.height for _, tile in tiles)
    pad = 8
    header = 28
    canvas = Image.new('RGB', (columns * tile_width + pad * (columns + 1),
                               rows * (tile_height + header) + pad * (rows + 1)), (245, 246, 248))
    draw = ImageDraw.Draw(canvas)
    for index, (label, tile) in enumerate(tiles):
        row, column = divmod(index, columns)
        x = pad + column * (tile_width + pad)
        y = pad + row * (tile_height + header + pad)
        draw.text((x + 4, y + 6), label, fill=(30, 34, 40))
        canvas.paste(tile, (x + (tile_width - tile.width) // 2, y + header))
    if title:
        draw.text((pad + 4, 6), title, fill=(30, 34, 40))
    canvas.save(out_path, 'PNG')
    return out_path


def file_info(path):
    return {
        'path': path,
        'exists': os.path.isfile(path),
        'bytes': os.path.getsize(path) if os.path.isfile(path) else None,
        'sha256': sha256_file(path),
    }
