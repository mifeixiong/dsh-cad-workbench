# -*- coding: utf-8 -*-
"""sw_session.py —— SolidWorks 文档会话（显式句柄 + 身份令牌）。"""
import os
import sys

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


def _doc_payload(model):
    identity = common.doc_identity(model)
    try:
        identity['active'] = bool(model == _active(app))
    except Exception:
        identity['active'] = None
    return identity


_ACTIVE_APP = None


def _active(app):
    global _ACTIVE_APP
    if _ACTIVE_APP is app:
        return app.ActiveDoc
    _ACTIVE_APP = app
    return app.ActiveDoc


def action_status(app):
    return {
        'ok': True,
        'connected': app is not None,
        'version': common.version_info(app) if app is not None else None,
        'documents': [common.doc_identity(model) for model in common.open_documents(app)] if app is not None else [],
    }


def action_list(app):
    return {'ok': True, 'documents': [common.doc_identity(model) for model in common.open_documents(app)]}


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
    model, identity = common.find_document(app, spec.get('doc'))
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


def action_close(app, spec):
    model, identity = common.find_document(app, spec.get('doc'))
    if identity.get('saved') is not True and not spec.get('confirm'):
        raise common.CadError(
            'CONFIRM_REQUIRED',
            '文档 %s 有未保存修改；关闭会丢弃。确认请传 confirm=true。' % (identity.get('title') or identity['doc_id']),
            {'doc': identity},
        )
    common.retry_busy(app.CloseDoc, identity.get('title') or identity.get('path'))
    return {'ok': True, 'closed': identity.get('title') or identity.get('path'), 'doc': identity}


def action_close_all(app, spec):
    if not spec.get('confirm'):
        raise common.CadError('CONFIRM_REQUIRED', 'close_all 需要 confirm=true。')
    closed = []
    for model in common.open_documents(app):
        try:
            identity = common.doc_identity(model)
            common.retry_busy(app.CloseDoc, identity.get('title') or identity.get('path'))
            closed.append(identity)
        except Exception:
            continue
    return {'ok': True, 'closed_documents': closed}


def main(spec):
    action = str(spec.get('action') or 'status')
    workspace = common.workspace_of(spec)
    if action == 'status':
        try:
            app = common.attach(launch=False)
            return action_status(app)
        except common.CadError:
            return {'ok': True, 'connected': False, 'version': None, 'documents': [],
                    'summary': 'SolidWorks 未运行（status 只附着，不拉起）'}
    with common.sw_lock('sw_session:' + action, workspace=workspace):
        app = common.attach(launch=action not in ('list',))
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
        if action == 'close_all':
            return action_close_all(app, spec)
        raise common.CadError('BAD_ARGS', '未知 action：%s' % action)


if __name__ == '__main__':
    common.run(main)
