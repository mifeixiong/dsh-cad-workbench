# -*- coding: utf-8 -*-
"""sw_session.py —— SolidWorks 文档会话 + 已打开窗口管理。

除了新建/打开/保存，还负责回答“现在开了哪些窗口、哪个是当前、哪个没保存”，
并按 doc_id / 窗口序号批量关闭。关闭策略：

  * 只动可见窗口（Frame.ModelWindows）；隐藏/参考文档默认不碰，需 include_hidden；
  * 有未保存修改的文档默认拒绝关闭（CONFIRM_REQUIRED），
    confirm=true 走 QuitDoc 丢弃（不弹保存框），save_unsaved=true 先保存再关；
  * dry_run=true 只给计划，不改 SolidWorks 状态；
  * 关闭后轮询 GetDocuments 复核，没关掉就报 CLOSE_FAILED，不谎报。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402


def _show(app, model):
    try:
        app.Visible = True
    except Exception:
        pass
    try:
        model.ShowNamedView2('*Isometric', 7)
        model.ViewZoomtofit2()
    except Exception:
        pass


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _counts(entries):
    return {
        'documents': len(entries),
        'windows': len([entry for entry in entries if entry.get('window')]),
        'hidden': len([entry for entry in entries if not entry.get('window')]),
        'dirty': len([entry for entry in entries if entry.get('saved') is not True]),
    }


def _inventory_payload(app):
    entries = common.document_inventory(app)
    counts = _counts(entries)
    active = next((entry['doc_id'] for entry in entries if entry.get('active')), None)
    return {
        'documents': entries,
        'document_count': counts['documents'],
        'window_count': counts['windows'],
        'hidden_count': counts['hidden'],
        'dirty_count': counts['dirty'],
        'active_doc_id': active,
        'summary': _inventory_summary(entries, counts),
    }


def _inventory_summary(entries, counts):
    lines = ['已打开 %d 个文档：窗口 %d，隐藏 %d，未保存 %d' % (
        counts['documents'], counts['windows'], counts['hidden'], counts['dirty'])]
    for entry in entries[:40]:
        state = '未保存' if entry.get('saved') is not True else '已保存'
        path = entry.get('path') or ''
        if entry.get('window'):
            lines.append(' %2s %s %s（%s，%s）%s' % (
                entry.get('index') or '?',
                '*' if entry.get('active') else ' ',
                entry.get('title') or '(无标题)',
                entry.get('type') or 'unknown',
                state,
                path,
            ))
        else:
            lines.append('    [隐藏] %s（%s，%s）%s' % (
                entry.get('title') or '(无标题)',
                entry.get('type') or 'unknown',
                state,
                path,
            ))
    if len(entries) > 40:
        lines.append('…（其余 %d 个见 documents 字段）' % (len(entries) - 40))
    return '\n'.join(lines)


def _empty_inventory(message):
    return {
        'ok': True,
        'connected': False,
        'version': None,
        'documents': [],
        'document_count': 0,
        'window_count': 0,
        'hidden_count': 0,
        'dirty_count': 0,
        'active_doc_id': None,
        'summary': message,
    }


def action_status(app):
    if app is None:
        return _empty_inventory('SolidWorks 未运行（status 只附着，不拉起）')
    payload = _inventory_payload(app)
    payload.update({'ok': True, 'connected': True, 'version': common.version_info(app)})
    return payload


def action_list(app):
    if app is None:
        return _empty_inventory('SolidWorks 未运行（list 只附着，不拉起）')
    payload = _inventory_payload(app)
    payload.update({'ok': True, 'connected': True})
    return payload


def _find_doc(entries, token):
    text = str(token)
    for entry in entries:
        if entry['doc_id'] == text:
            return entry
    return None


def _find_index(entries, value):
    try:
        number = int(value)
    except Exception:
        return None
    for entry in entries:
        if entry.get('index') == number:
            return entry
    return None


def _resolve_token(entries, token, label='文档'):
    entry = _find_doc(entries, token)
    if entry is None:
        entry = _find_index(entries, token)
    if entry is None:
        raise common.CadError(
            'DOC_NOT_FOUND',
            '找不到%s：%s（可用 doc_id 或窗口序号；先 cadw_session action=list）' % (label, token),
            {'open_docs': entries},
        )
    return entry


def _add_unique(selected, entry):
    if all(item['doc_id'] != entry['doc_id'] for item in selected):
        selected.append(entry)


def _explicit_targets(app, spec):
    entries = common.document_inventory(app)
    selected = []
    if spec.get('doc'):
        _add_unique(selected, _resolve_token(entries, spec['doc']))
    for token in _as_list(spec.get('docs')):
        _add_unique(selected, _resolve_token(entries, token))
    for index in _as_list(spec.get('indexes')):
        entry = _find_index(entries, index)
        if entry is None:
            raise common.CadError(
                'DOC_NOT_FOUND',
                '找不到窗口序号：%s（窗口可能已变化；先 action=list 重新取序号）' % index,
                {'windows': [item for item in entries if item.get('window')]},
            )
        _add_unique(selected, entry)
    if not selected:
        raise common.CadError('BAD_ARGS', 'close 需要 doc、docs 或 indexes 之一。')
    return entries, selected


def _model_for(app, doc_id):
    for model in common.open_documents(app):
        try:
            if common.doc_identity(model)['doc_id'] == doc_id:
                return model
        except Exception:
            continue
    raise common.CadError('DOC_NOT_FOUND', '文档已不在打开列表里：%s' % doc_id, {'doc_id': doc_id})


def _skip(entry, reason):
    return {
        'doc_id': entry.get('doc_id'),
        'title': entry.get('title'),
        'path': entry.get('path'),
        'index': entry.get('index'),
        'reason': reason,
    }


def _failure(entry, code, message, cause=None):
    item = {
        'doc_id': entry.get('doc_id'),
        'title': entry.get('title'),
        'index': entry.get('index'),
        'code': code,
        'message': message,
    }
    if cause:
        item['cause'] = cause
    return item


def _filter_hidden(selected, include_hidden):
    targets = []
    skipped = []
    for entry in selected:
        if entry.get('window') or include_hidden:
            targets.append(entry)
        else:
            skipped.append(_skip(entry, 'hidden'))
    if not targets and skipped:
        raise common.CadError(
            'HIDDEN_DOC',
            '目标都是隐藏/参考文档（不是可见窗口）。确认要关请传 include_hidden=true；'
            '它们可能被装配体/工程图引用。',
            {'skipped': skipped},
        )
    return targets, skipped


def _plan(entry, spec):
    if entry.get('saved') is True:
        action = 'close'
    elif spec.get('save_unsaved') is True:
        action = 'save_close'
    elif spec.get('confirm') is True:
        action = 'discard_close'
    else:
        action = 'blocked'
    return {
        'doc_id': entry.get('doc_id'),
        'title': entry.get('title'),
        'index': entry.get('index'),
        'window': entry.get('window'),
        'saved': entry.get('saved'),
        'action': action,
    }


def _close_summary(action, result):
    parts = ['关闭 %d 个文档' % len(result['closed_documents'])]
    if result.get('saved_documents'):
        parts.append('保存后关闭 %d 个' % len(result['saved_documents']))
    if result.get('discarded'):
        parts.append('丢弃未保存修改 %d 个：%s' % (
            len(result['discarded']),
            '、'.join(item.get('title') or item.get('doc_id') for item in result['discarded']),
        ))
    if result.get('failed'):
        parts.append('失败 %d 个：%s' % (
            len(result['failed']),
            '、'.join((item.get('title') or '?') + '=' + (item.get('code') or '?')
                     for item in result['failed']),
        ))
    if result.get('skipped'):
        parts.append('跳过隐藏/参考文档 %d 个（需要 include_hidden=true）' % len(result['skipped']))
    remaining = result.get('remaining') or {}
    parts.append('剩余窗口 %s 个 / 文档 %s 个' % (remaining.get('windows', '?'), remaining.get('documents', '?')))
    return '；'.join(parts)


def _wait_saved(model, timeout=2.0):
    """保存后短暂轮询 GetSaveFlag，避免 SW 还没清脏标记就误报 SAVE_FAILED。"""
    deadline = time.time() + max(0.2, float(timeout))
    while True:
        identity = common.doc_identity(model)
        if identity.get('saved') is True:
            return identity
        if time.time() >= deadline:
            return identity
        time.sleep(0.1)


def _close_targets(app, targets, spec, action, skipped):
    include_hidden = spec.get('include_hidden') is True
    save_unsaved = spec.get('save_unsaved') is True
    confirm = spec.get('confirm') is True
    dry_run = spec.get('dry_run') is True
    dirty = [entry for entry in targets if entry.get('saved') is not True]
    requires_confirm = bool(dirty) and not save_unsaved and not confirm

    if dry_run:
        plan = [_plan(entry, spec) for entry in targets]
        return {
            'ok': True,
            'action': action,
            'dry_run': True,
            'would_close': targets,
            'dirty': dirty,
            'skipped': skipped,
            'requires_confirm': requires_confirm,
            'plan': plan,
            'summary': '预览：将处理 %d 个文档，其中未保存 %d 个%s；没有改动 SolidWorks。' % (
                len(targets), len(dirty),
                '（需要 confirm=true 丢弃或 save_unsaved=true 先保存）' if requires_confirm else '',
            ),
        }

    if requires_confirm:
        raise common.CadError(
            'CONFIRM_REQUIRED',
            '%d 个文档有未保存修改；关闭会丢弃。确认请传 confirm=true（丢弃），'
            '或 save_unsaved=true（先保存）。' % len(dirty),
            {'dirty': dirty, 'would_close': targets, 'skipped': skipped},
        )

    closed = []
    discarded = []
    saved_documents = []
    failed = []
    for entry in targets:
        try:
            model = _model_for(app, entry['doc_id'])
        except common.CadError as error:
            failed.append(_failure(entry, error.code, error.message))
            continue
        current = common.doc_identity(model)
        discard = False
        if current.get('saved') is not True:
            if save_unsaved:
                try:
                    common.save(model)
                except common.CadError as error:
                    failed.append(_failure(current, 'SAVE_FAILED', error.message, error.code))
                    continue
                except Exception as error:  # noqa: BLE001
                    failed.append(_failure(current, 'SAVE_FAILED', str(error)))
                    continue
                current = _wait_saved(model)
                if current.get('saved') is not True:
                    failed.append(_failure(current, 'SAVE_FAILED', '保存后仍检测到未保存修改，未关闭。'))
                    continue
                saved_documents.append(dict(current, saved=True))
            elif confirm:
                discard = True
            else:
                failed.append(_failure(current, 'CONFIRM_REQUIRED',
                                       '文档在关闭前变成未保存状态，未关闭。'))
                continue
        try:
            identity = common.close_document(app, model, discard=discard)
        except common.CadError as error:
            failed.append(_failure(current, error.code, error.message))
            continue
        record = dict(identity)
        record['discarded'] = bool(discard)
        closed.append(record)
        if discard:
            discarded.append(record)

    remaining_entries = common.document_inventory(app)
    result = {
        'ok': True,
        'action': action,
        'closed_documents': closed,
        'closed_count': len(closed),
        'saved_documents': saved_documents,
        'discarded': discarded,
        'failed': failed,
        'skipped': skipped,
        'partial': bool(closed and failed),
        'remaining': _counts(remaining_entries),
    }
    result['closed'] = closed[0].get('title') if len(closed) == 1 else None
    result['doc'] = closed[0] if len(closed) == 1 else None
    result['summary'] = _close_summary(action, result)
    if not closed and failed:
        first = failed[0]
        raise common.CadError(
            first.get('code') or 'CLOSE_FAILED',
            '没有文档被关闭：' + (first.get('message') or ''),
            {'failed': failed, 'skipped': skipped, 'closed_documents': []},
        )
    if failed:
        result['warnings'] = ['部分文档未关闭：' + '；'.join(
            (item.get('title') or '?') + '=' + (item.get('message') or '') for item in failed)]
    return result


def _bulk_targets(app, spec, mode):
    if spec.get('doc') or spec.get('docs') or spec.get('indexes'):
        raise common.CadError('BAD_ARGS', '%s 不接受 doc/docs/indexes；要按 id 关闭请用 action=close。' % mode)
    entries = common.document_inventory(app)
    include_hidden = spec.get('include_hidden') is True
    if mode == 'close_all':
        targets = [entry for entry in entries if entry.get('window') or include_hidden]
        skipped = [_skip(entry, 'hidden') for entry in entries
                   if not entry.get('window') and not include_hidden]
        return targets, skipped
    keep_values = _as_list(spec.get('keep'))
    if not keep_values:
        raise common.CadError('BAD_ARGS', 'close_others 需要 keep（要保留的 doc_id 或窗口序号，至少一个）。')
    keep = set()
    for token in keep_values:
        keep.add(_resolve_token(entries, token, '要保留的文档')['doc_id'])
    targets = [entry for entry in entries
               if entry['doc_id'] not in keep and (entry.get('window') or include_hidden)]
    skipped = [_skip(entry, 'hidden') for entry in entries
               if entry['doc_id'] not in keep and not entry.get('window') and not include_hidden]
    return targets, skipped


def action_close(app, spec):
    _, selected = _explicit_targets(app, spec)
    targets, skipped = _filter_hidden(selected, spec.get('include_hidden') is True)
    return _close_targets(app, targets, spec, 'close', skipped)


def action_close_all(app, spec):
    if not spec.get('confirm') and not spec.get('dry_run'):
        raise common.CadError('CONFIRM_REQUIRED', 'close_all 需要 confirm=true（可先 dry_run=true 预览）。')
    targets, skipped = _bulk_targets(app, spec, 'close_all')
    if not targets:
        payload = _inventory_payload(app)
        return {
            'ok': True, 'action': 'close_all', 'closed_documents': [], 'closed_count': 0,
            'saved_documents': [], 'discarded': [], 'failed': [], 'skipped': skipped,
            'partial': False, 'remaining': _counts(payload['documents']),
            'summary': '没有可关闭的可见窗口' + ('（隐藏文档未动）' if skipped else ''),
        }
    return _close_targets(app, targets, spec, 'close_all', skipped)


def action_close_others(app, spec):
    if not spec.get('confirm') and not spec.get('dry_run'):
        raise common.CadError('CONFIRM_REQUIRED', 'close_others 需要 confirm=true（可先 dry_run=true 预览）。')
    targets, skipped = _bulk_targets(app, spec, 'close_others')
    if not targets:
        payload = _inventory_payload(app)
        return {
            'ok': True, 'action': 'close_others', 'closed_documents': [], 'closed_count': 0,
            'saved_documents': [], 'discarded': [], 'failed': [], 'skipped': skipped,
            'partial': False, 'remaining': _counts(payload['documents']),
            'summary': '没有需要关闭的窗口（保留列表之外没有可见窗口）',
        }
    return _close_targets(app, targets, spec, 'close_others', skipped)


def action_new(app, spec, kind):
    template_kind = 'assembly' if kind == 'assembly' else 'part'
    template = common.find_template(app, template_kind)
    if not template.get('path'):
        raise common.CadError('UNSUPPORTED', '找不到%s模板。%s' % (
            '装配体' if template_kind == 'assembly' else '零件',
            '请先启动一次 SolidWorks 确认默认模板，或设置环境变量 CADW_PART_TEMPLATE / CADW_ASM_TEMPLATE。'),
            {'template': template})
    if spec.get('template'):
        template = {'path': os.path.abspath(spec['template']), 'source': 'explicit'}
    model = common.retry_busy(app.NewDocument, template['path'], 0, 0.1, 0.1)
    if model is None:
        raise common.CadError('BUILD_FAILED', '新建文档失败（NewDocument 返回 None）。', {'template': template})
    identity = common.doc_identity(model)
    if spec.get('visual', True):
        common.activate(app, model)
        _show(app, model)
    return {'ok': True, 'doc': identity, 'template': template, 'created': True}


def action_open(app, spec):
    path = os.path.abspath(spec.get('path') or '')
    if not os.path.isfile(path):
        raise common.CadError('DOC_NOT_FOUND', '文件不存在：%s' % path)
    extension = os.path.splitext(path)[1].lower()
    doc_type = {'.sldprt': 1, '.sldasm': 2, '.slddrw': 3}.get(extension)
    if doc_type is None:
        raise common.CadError('UNSUPPORTED', '只支持打开 .sldprt / .sldasm / .slddrw，收到 %s' % extension)
    import pythoncom
    import win32com.client
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    model = common.retry_busy(app.OpenDoc6, path, doc_type, 1, '', errors, warnings)
    if model is None:
        raise common.CadError('DOC_NOT_FOUND', '打开失败：%s' % path,
                              {'errors': int(errors.value), 'warnings': int(warnings.value)})
    if spec.get('visual', True):
        common.activate(app, model)
        _show(app, model)
    return {'ok': True, 'doc': common.doc_identity(model), 'opened': True}


def action_activate(app, spec):
    if spec.get('index') is not None and not spec.get('indexes'):
        spec = dict(spec, indexes=[spec['index']])
    _, selected = _explicit_targets(app, spec)
    if len(selected) != 1:
        raise common.CadError('BAD_ARGS', 'activate 一次只能激活一个文档（收到 %d 个）。' % len(selected))
    entry = selected[0]
    if not entry.get('window') and not spec.get('include_hidden'):
        raise common.CadError('HIDDEN_DOC', '文档 %s 不是可见窗口，无法激活。' % (entry.get('title') or entry['doc_id']),
                              {'doc': entry})
    model = _model_for(app, entry['doc_id'])
    common.activate(app, model)
    if spec.get('visual', True):
        _show(app, model)
    return {'ok': True, 'doc': common.doc_identity(model)}


def action_save(app, spec):
    model, identity = common.find_document(app, spec.get('doc'))
    common.verify_identity(model, identity['doc_id'])
    if spec.get('path'):
        guard = os.path.abspath(spec['path'])
        if os.path.exists(guard) and not spec.get('confirm'):
            raise common.CadError('CONFIRM_REQUIRED', '目标已存在，拒绝覆盖：%s（确认请传 confirm=true）' % guard)
        saved = common.save(model, guard)
        return {'ok': True, 'doc': common.doc_identity(model), 'saved': saved}
    saved = common.save(model)
    return {'ok': True, 'doc': common.doc_identity(model), 'saved': saved}


def _connection_error_payload(result, error):
    result['connection_error'] = {'code': error.code, 'message': error.message}
    if error.details is not None:
        result['connection_error']['details'] = error.details
    result['summary'] = (result.get('summary') or '') + '\n附着失败：' + error.message
    return result


def main(spec):
    action = str(spec.get('action') or 'status')
    workspace = common.workspace_of(spec)
    if action == 'status':
        try:
            app = common.attach(launch=False)
        except common.CadError as error:
            return _connection_error_payload(action_status(None), error)
        return action_status(app)
    with common.sw_lock('sw_session:' + action, workspace=workspace):
        launch = action in ('new_part', 'new_assembly', 'open')
        try:
            app = common.attach(launch=launch)
        except common.CadError as error:
            if action == 'list':
                return _connection_error_payload(action_list(None), error)
            raise
        if action == 'list':
            return action_list(app)
        if action == 'new_part':
            return action_new(app, spec, 'part')
        if action == 'new_assembly':
            return action_new(app, spec, 'assembly')
        if action == 'open':
            return action_open(app, spec)
        if action == 'activate':
            return action_activate(app, spec)
        if action == 'save' or action == 'save_as':
            if action == 'save_as' and not spec.get('path'):
                raise common.CadError('BAD_ARGS', 'save_as 需要 path。')
            return action_save(app, spec)
        if action == 'close':
            return action_close(app, spec)
        if action == 'close_others':
            return action_close_others(app, spec)
        if action == 'close_all':
            return action_close_all(app, spec)
        raise common.CadError('BAD_ARGS', '未知 action：%s' % action)


if __name__ == '__main__':
    common.run(main)
