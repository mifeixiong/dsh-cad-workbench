# -*- coding: utf-8 -*-
"""sw_session 窗口管理的离线回归（不需要 SolidWorks）。

用假 app/model 覆盖：
  * 窗口清单：序号、当前文档、隐藏/参考文档、未保存标记；
  * 批量 close / close_others / close_all 的选择与保护；
  * 脏文档默认拒绝、confirm 丢弃、save_unsaved 先保存；
  * dry_run 只预览不改状态；
  * 隐藏文档必须显式 include_hidden。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')
sys.path.insert(0, SCRIPTS)

import sw_common  # noqa: E402
import sw_session  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    print(('  PASS ' if condition else '  FAIL ') + name + ' ' + str(detail)[:240])
    if not condition:
        FAILURES.append(name)


class FakeModel:
    def __init__(self, title, path='', kind=1, dirty=False, window=True):
        self.title = title
        self.path = path
        self.kind = kind
        self.dirty = dirty
        self.window = window
        self.closed_by = None
        self.save_calls = 0

    @property
    def GetTitle(self):
        return self.title

    @property
    def GetPathName(self):
        return self.path

    @property
    def GetType(self):
        return self.kind

    @property
    def GetSaveFlag(self):
        return self.dirty

    def Save(self):
        if not self.path:
            raise RuntimeError('文档没有磁盘路径')
        self.dirty = False
        self.save_calls += 1
        return True


class UnknownSaveModel(FakeModel):
    @property
    def GetSaveFlag(self):
        raise RuntimeError('读不到保存标记')


class FakeWindow:
    def __init__(self, model, hwnd):
        self.ModelDoc = model
        self.Title = model.title
        self.HWnd = hwnd


class FakeFrame:
    def __init__(self, app):
        self.app = app

    @property
    def ModelWindows(self):
        return [FakeWindow(model, 1000 + index)
                for index, model in enumerate(self.app.models) if model.window]


class FakeApp:
    def __init__(self, models, active=None):
        self.models = list(models)
        self._active = active or (models[0] if models else None)
        self.frame = FakeFrame(self)

    @property
    def GetDocuments(self):
        return list(self.models)

    @property
    def ActiveDoc(self):
        if self._active in self.models:
            return self._active
        return self.models[-1] if self.models else None

    @property
    def Frame(self):
        return self.frame

    def _find(self, name):
        for model in self.models:
            if name in (model.title, model.path):
                return model
        return None

    def _remove(self, name, method):
        model = self._find(name)
        if model is None:
            return False
        model.closed_by = method
        self.models.remove(model)
        if self._active is model:
            self._active = self.models[-1] if self.models else None
        return True

    def CloseDoc(self, name):
        self._remove(name, 'CloseDoc')

    def QuitDoc(self, name):
        self._remove(name, 'QuitDoc')

    def ActivateDoc3(self, name, *args):
        model = self._find(name)
        if model is not None:
            self._active = model
        return model


def outcome(function, app, spec):
    try:
        return function(app, spec)
    except sw_common.CadError as error:
        return {'ok': False, 'error': {'code': error.code, 'message': error.message,
                                       'details': error.details}}


def by_title(app, title):
    for model in app.models:
        if model.title == title:
            return model
    return None


def test_inventory():
    assembly = FakeModel('装配体1.SLDASM', 'C:\\work\\装配体1.SLDASM', kind=2, dirty=False)
    part = FakeModel('侧板.SLDPRT', 'C:\\work\\侧板.SLDPRT', kind=1, dirty=True)
    hidden = FakeModel('参考轮.SLDPRT', 'C:\\work\\参考轮.SLDPRT', kind=1, dirty=False, window=False)
    app = FakeApp([assembly, part, hidden], active=part)
    payload = sw_session.action_list(app)
    entries = payload['documents']
    check('inventory.count', len(entries) == 3, len(entries))
    check('inventory.window_count', payload['window_count'] == 2, payload['window_count'])
    check('inventory.document_count', payload['document_count'] == 3, payload['document_count'])
    check('inventory.dirty_count', payload['dirty_count'] == 1, payload['dirty_count'])
    check('inventory.active', payload['active_doc_id'] == sw_common.doc_identity(part)['doc_id'],
          payload.get('active_doc_id'))
    windows = [item for item in entries if item.get('window')]
    check('inventory.window_order', [item['title'] for item in windows] == ['装配体1.SLDASM', '侧板.SLDPRT'],
          [item['title'] for item in windows])
    check('inventory.window_index', windows[0]['index'] == 1 and windows[1]['index'] == 2,
          [item.get('index') for item in windows])
    hidden_entry = next(item for item in entries if item['title'] == '参考轮.SLDPRT')
    check('inventory.hidden', hidden_entry['window'] is False and hidden_entry['index'] is None, hidden_entry)
    check('inventory.summary', isinstance(payload.get('summary'), str) and '未保存' in payload['summary'],
          payload.get('summary'))


def test_close_requires_confirm():
    part = FakeModel('侧板.SLDPRT', 'C:\\work\\侧板.SLDPRT', dirty=True)
    app = FakeApp([part], active=part)
    token = sw_common.doc_identity(part)['doc_id']
    result = outcome(sw_session.action_close, app, {'doc': token})
    check('close.dirty_requires_confirm', result.get('ok') is False and
          result['error']['code'] == 'CONFIRM_REQUIRED', result)
    check('close.dirty_not_closed', by_title(app, '侧板.SLDPRT') is not None, app.models)
    check('close.dirty_no_quit', part.closed_by is None, part.closed_by)


def test_close_discard_and_clean():
    dirty = FakeModel('侧板.SLDPRT', 'C:\\work\\侧板.SLDPRT', dirty=True)
    clean = FakeModel('底板.SLDPRT', 'C:\\work\\底板.SLDPRT', dirty=False)
    app = FakeApp([dirty, clean], active=dirty)
    dirty_token = sw_common.doc_identity(dirty)['doc_id']
    clean_token = sw_common.doc_identity(clean)['doc_id']
    result = outcome(sw_session.action_close, app, {'docs': [dirty_token, clean_token], 'confirm': True})
    check('close.multi_ok', result.get('ok') is True, result)
    check('close.multi_count', len(result.get('closed_documents') or []) == 2,
          result.get('closed_documents'))
    check('close.discard_uses_quit', dirty.closed_by == 'QuitDoc', dirty.closed_by)
    check('close.clean_uses_close', clean.closed_by == 'CloseDoc', clean.closed_by)
    check('close.discard_reported', len(result.get('discarded') or []) == 1, result.get('discarded'))
    check('close.remaining_empty', result['remaining']['documents'] == 0, result.get('remaining'))


def test_close_by_index_and_others():
    assembly = FakeModel('装配体1.SLDASM', 'C:\\work\\装配体1.SLDASM', kind=2)
    part_a = FakeModel('A.SLDPRT', 'C:\\work\\A.SLDPRT')
    part_b = FakeModel('B.SLDPRT', 'C:\\work\\B.SLDPRT')
    app = FakeApp([assembly, part_a, part_b], active=assembly)
    # 窗口序号：装配体=1，A=2，B=3；按序号关闭 A
    by_index = outcome(sw_session.action_close, app, {'indexes': [2]})
    check('close.index_ok', by_index.get('ok') is True and by_title(app, 'A.SLDPRT') is None, by_index)
    # 只保留装配体
    result = outcome(sw_session.action_close_others, app, {'keep': [1], 'confirm': True})
    check('close_others.ok', result.get('ok') is True, result)
    check('close_others.kept', by_title(app, '装配体1.SLDASM') is not None, app.models)
    check('close_others.closed', by_title(app, 'B.SLDPRT') is None, app.models)


def test_close_all_and_hidden():
    assembly = FakeModel('装配体1.SLDASM', 'C:\\work\\装配体1.SLDASM', kind=2)
    part = FakeModel('A.SLDPRT', 'C:\\work\\A.SLDPRT')
    hidden = FakeModel('参考.SLDPRT', 'C:\\work\\参考.SLDPRT', window=False)
    app = FakeApp([assembly, part, hidden], active=assembly)
    result = outcome(sw_session.action_close_all, app, {'confirm': True})
    check('close_all.ok', result.get('ok') is True, result)
    check('close_all.hidden_kept', by_title(app, '参考.SLDPRT') is not None, app.models)
    check('close_all.windows_closed', by_title(app, '装配体1.SLDASM') is None and
          by_title(app, 'A.SLDPRT') is None, app.models)
    check('close_all.skipped_hidden', any(item.get('reason') == 'hidden'
                                          for item in (result.get('skipped') or [])),
          result.get('skipped'))
    # 显式关闭隐藏文档需要 include_hidden
    app2 = FakeApp([hidden], active=None)
    token = sw_common.doc_identity(hidden)['doc_id']
    refused = outcome(sw_session.action_close, app2, {'doc': token})
    check('hidden.refused', refused.get('ok') is False and
          refused['error']['code'] == 'HIDDEN_DOC', refused)
    allowed = outcome(sw_session.action_close, app2, {'doc': token, 'include_hidden': True})
    check('hidden.allowed', allowed.get('ok') is True and by_title(app2, '参考.SLDPRT') is None, allowed)


def test_close_all_requires_confirm():
    part = FakeModel('A.SLDPRT', 'C:\\work\\A.SLDPRT')
    app = FakeApp([part], active=part)
    result = outcome(sw_session.action_close_all, app, {})
    check('close_all.confirm_required', result.get('ok') is False and
          result['error']['code'] == 'CONFIRM_REQUIRED', result)
    check('close_all.confirm_kept_open', by_title(app, 'A.SLDPRT') is not None, app.models)


def test_dry_run_and_save():
    dirty = FakeModel('侧板.SLDPRT', 'C:\\work\\侧板.SLDPRT', dirty=True)
    clean = FakeModel('底板.SLDPRT', 'C:\\work\\底板.SLDPRT', dirty=False)
    app = FakeApp([dirty, clean], active=dirty)
    tokens = [sw_common.doc_identity(dirty)['doc_id'], sw_common.doc_identity(clean)['doc_id']]
    preview = outcome(sw_session.action_close, app, {'docs': tokens, 'dry_run': True})
    check('dry_run.ok', preview.get('ok') is True and preview.get('dry_run') is True, preview)
    check('dry_run.no_mutation', by_title(app, '侧板.SLDPRT') is not None, app.models)
    check('dry_run.requires_confirm', preview.get('requires_confirm') is True, preview)
    actions = {item['title']: item['action'] for item in (preview.get('plan') or [])}
    check('dry_run.plan', actions.get('侧板.SLDPRT') == 'blocked' and
          actions.get('底板.SLDPRT') == 'close', actions)
    saved = outcome(sw_session.action_close, app, {'doc': tokens[0], 'save_unsaved': True})
    check('save_unsaved.ok', saved.get('ok') is True and dirty.save_calls == 1, saved)
    check('save_unsaved.close', dirty.closed_by == 'CloseDoc', dirty.closed_by)


def test_save_unsaved_without_path_fails():
    dirty = FakeModel('新零件', '', dirty=True)
    app = FakeApp([dirty], active=dirty)
    token = sw_common.doc_identity(dirty)['doc_id']
    result = outcome(sw_session.action_close, app, {'doc': token, 'save_unsaved': True})
    check('save_unsaved.no_path_failed', result.get('ok') is False and
          result['error']['code'] == 'SAVE_FAILED', result)
    check('save_unsaved.no_path_kept', by_title(app, '新零件') is not None, app.models)


def test_unknown_save_flag_is_dirty():
    model = UnknownSaveModel('怪文档.SLDPRT', 'C:\\work\\怪文档.SLDPRT')
    app = FakeApp([model], active=model)
    token = sw_common.doc_identity(model)['doc_id']
    refused = outcome(sw_session.action_close, app, {'doc': token})
    check('unknown_save_flag.refused', refused.get('ok') is False and
          refused['error']['code'] == 'CONFIRM_REQUIRED', refused)
    allowed = outcome(sw_session.action_close, app, {'doc': token, 'confirm': True})
    check('unknown_save_flag.quit', allowed.get('ok') is True and
          model.closed_by == 'QuitDoc', allowed)


def test_unknown_target():
    part = FakeModel('A.SLDPRT', 'C:\\work\\A.SLDPRT')
    app = FakeApp([part], active=part)
    result = outcome(sw_session.action_close, app, {'indexes': [99]})
    check('target.unknown_index', result.get('ok') is False and
          result['error']['code'] == 'DOC_NOT_FOUND', result)
    result = outcome(sw_session.action_close, app, {'docs': ['deadbeefdeadbeef']})
    check('target.unknown_doc', result.get('ok') is False and
          result['error']['code'] == 'DOC_NOT_FOUND', result)
    result = outcome(sw_session.action_close, app, {})
    check('target.missing', result.get('ok') is False and
          result['error']['code'] == 'BAD_ARGS', result)


def main():
    test_inventory()
    test_close_requires_confirm()
    test_close_discard_and_clean()
    test_close_by_index_and_others()
    test_close_all_and_hidden()
    test_close_all_requires_confirm()
    test_dry_run_and_save()
    test_save_unsaved_without_path_fails()
    test_unknown_save_flag_is_dirty()
    test_unknown_target()
    print('\nsession_cases: 失败 %d' % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
