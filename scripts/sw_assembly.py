# -*- coding: utf-8 -*-
"""sw_assembly.py —— 装配体：新建、插入零部件、平面配合、固定、列表。

动作：
  new      新建装配体（模板自动探测）
  insert   [{path,x,y,z,fixed}] 插入零部件（mm）
  mate     [{type,a,b,...}] 在装配体上下文创建配合（v0.1 支持平面）
  fix      {component, fixed} 固定/浮动
  list     列出零部件与配合

配合选择器：a/b = {component: 名称或序号, plane: front|top|right|自定义基准面名}
单位：位置 mm；配合距离 mm；配合角度度（API 用弧度，脚本内换算）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pythoncom
import win32com.client

import sw_common as common  # noqa: E402

PLANE_ALIASES = {
    'front': ('Front Plane', '前视基准面'),
    'top': ('Top Plane', '上视基准面'),
    'right': ('Right Plane', '右视基准面'),
}
MATE_TYPES = {
    'coincident': 0,
    'concentric': 1,
    'perpendicular': 2,
    'parallel': 3,
    'tangent': 4,
    'distance': 5,
    'angle': 6,
}


def _open_part(app, path):
    """确保零件已打开（GetCorresponding 需要引用模型文档存在）。"""
    absolute = os.path.abspath(path)
    for doc in common.open_documents(app):
        identity = common.doc_identity(doc)
        if identity.get('path') and os.path.normcase(identity['path']) == os.path.normcase(absolute):
            return doc, False
    extension = os.path.splitext(absolute)[1].lower()
    doc_type = {'.sldprt': 1, '.sldasm': 2}.get(extension, 1)
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = common.retry_busy(app.OpenDoc6, absolute, doc_type, 1, '', errors, warnings)
    if doc is None:
        raise common.CadError('DOC_NOT_FOUND', '打不开零部件：%s（errors=%s）'
                              % (absolute, errors.value))
    return doc, True


def _part_of_component(app, component):
    path = str(common._call_or_value(component, 'GetPathName') or '')
    if not path:
        raise common.CadError('DOC_NOT_FOUND', '组件没有路径（可能是虚拟零件）。')
    part, opened = _open_part(app, path)
    return part, opened


def _component_name(component):
    for attr in ('Name2', 'Name'):
        try:
            value = common._call_or_value(component, attr)
            if value:
                return str(value)
        except Exception:
            continue
    return ''


def _components(asm):
    try:
        return list(asm.GetComponents(False) or [])
    except Exception:
        return []


def _find_component(asm, selector):
    components = _components(asm)
    if isinstance(selector, int) and not isinstance(selector, bool):
        if 0 <= selector < len(components):
            return components[selector]
        raise common.CadError('DOC_NOT_FOUND', '组件序号越界：%s' % selector)
    text = str(selector or '').strip()
    for component in components:
        name = _component_name(component)
        if name == text:
            return component
    matches = [component for component in components if text and text in _component_name(component)]
    if len(matches) == 1:
        return matches[0]
    raise common.CadError('DOC_NOT_FOUND',
                          '找不到组件 %r；现有组件：%s'
                          % (text, '、'.join(_component_name(item) for item in components)))


def _component_payload(component):
    payload = {'name': _component_name(component),
               'path': str(common._call_or_value(component, 'GetPathName') or '')}
    try:
        payload['fixed'] = bool(component.IsFixed)
    except Exception:
        payload['fixed'] = None
    return payload


def _plane_entity(app, component, plane, opened_parts):
    part, opened = _part_of_component(app, component)
    if opened:
        opened_parts.append(part)
    candidates = []
    key = str(plane or '').strip()
    for alias in PLANE_ALIASES.get(key.lower(), (key,)):
        if alias:
            candidates.append(alias)
    feature = None
    for name in candidates:
        try:
            feature = part.FeatureByName(name)
        except Exception:
            feature = None
        if feature is not None:
            break
    if feature is None:
        raise common.CadError('UNSUPPORTED',
                              '组件 %s 缺少基准面 %r（尝试过 %s）'
                              % (_component_name(component), plane, '、'.join(candidates)))
    entity = component.GetCorresponding(feature)
    if entity is None:
        raise common.CadError('UNSUPPORTED', '无法把组件 %s 的基准面映射到装配体上下文。'
                              % _component_name(component))
    return entity


def _add_mate(asm, component_a, component_b, entity_a, entity_b, options):
    asm.ClearSelection2(True)
    if not entity_a.Select2(False, 1):
        raise common.CadError('BUILD_FAILED', '选择第一个配合实体失败。')
    if not entity_b.Select2(True, 1):
        raise common.CadError('BUILD_FAILED', '选择第二个配合实体失败。')
    count = asm.SelectionManager.GetSelectedObjectCount2(-1)
    if count != 2:
        raise common.CadError('BUILD_FAILED', '配合选择数量错误（%s，应为 2）。' % count)
    mate_type_name = str(options.get('type') or 'coincident').lower()
    if mate_type_name not in MATE_TYPES:
        raise common.CadError('BAD_ARGS', '不支持的配合类型：%s（可用 %s）'
                              % (mate_type_name, '/'.join(MATE_TYPES)))
    distance_m = float(options.get('distance', 0.0) or 0.0) * common.MM
    angle_rad = math.radians(float(options.get('angle', 0.0) or 0.0))
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    mate = common.retry_busy(
        asm.AddMate5,
        MATE_TYPES[mate_type_name],
        int(options.get('align', 0) or 0),
        bool(options.get('flip', False)),
        distance_m, distance_m, distance_m,
        0.0, 0.0,
        angle_rad, angle_rad, angle_rad,
        False,
        bool(options.get('lock_rotation', False)),
        0,
        errors,
    )
    asm.ClearSelection2(True)
    if mate is None or int(errors.value) not in (0, 1):
        raise common.CadError('BUILD_FAILED',
                              'AddMate5 失败：type=%s，error_status=%s'
                              % (mate_type_name, errors.value),
                              {'error_status': int(errors.value)})
    name = ''
    try:
        name = str(common._call_or_value(mate, 'Name'))
    except Exception:
        name = ''
    return {'type': mate_type_name, 'name': name,
            'a': _component_name(component_a), 'b': _component_name(component_b),
            'error_status': int(errors.value)}


def _mates(asm):
    """配合在 MateGroup 的子特征链里；动态分派下 GetMates 不可用。"""
    result = []
    group = None
    for feature in common.feature_list(asm):
        if 'mategroup' in str(feature.get('type', '')).lower() or feature.get('name') == '配合':
            try:
                for candidate in asm.FeatureManager.GetFeatures(True):
                    if str(common._call_or_value(candidate, 'Name')) == feature['name']:
                        group = candidate
                        break
            except Exception:
                group = None
            break
    if group is None:
        return result
    try:
        current = group.GetFirstSubFeature
    except Exception:
        current = None
    guard = 0
    while current is not None and guard < 200:
        guard += 1
        entry = {}
        for attr in ('Name', 'GetTypeName2'):
            try:
                entry['name' if attr == 'Name' else 'type'] = common._call_or_value(current, attr)
            except Exception:
                entry['name' if attr == 'Name' else 'type'] = None
        result.append(entry)
        try:
            current = current.GetNextSubFeature
        except Exception:
            current = None
    return result


def action_new(app, spec):
    template = common.find_template(app, 'assembly', prefer='gb_')
    if not template.get('path'):
        raise common.CadError('UNSUPPORTED', '找不到装配体模板（.asmdot）。', {'template': template})
    asm = common.retry_busy(app.NewDocument, template['path'], 0, 0, 0)
    if asm is None:
        raise common.CadError('BUILD_FAILED', '新建装配体失败（NewDocument 返回 None）。')
    common.activate(app, asm)
    return {'ok': True, 'doc': common.doc_identity(asm), 'template': template}


def action_insert(app, asm, spec):
    opened_parts = []
    components = []
    try:
        for item in spec.get('components') or []:
            path = os.path.abspath(str(item.get('path') or ''))
            if not os.path.isfile(path):
                raise common.CadError('DOC_NOT_FOUND', '零部件不存在：%s' % path)
            x = float(item.get('x', 0.0) or 0.0) * common.MM
            y = float(item.get('y', 0.0) or 0.0) * common.MM
            z = float(item.get('z', 0.0) or 0.0) * common.MM
            component = common.retry_busy(asm.AddComponent5, path, 0, '', False, '', x, y, z)
            if component is None:
                # 回退：先把零件打开，再重试（SW 有时要求引用文档已加载）
                _, _ = _open_part(app, path)
                common.activate(app, asm)
                component = common.retry_busy(asm.AddComponent5, path, 0, '', False, '', x, y, z)
            if component is None:
                raise common.CadError('BUILD_FAILED', 'AddComponent5 返回 None：%s' % path)
            try:
                component.SetSuppression2(2)
            except Exception:
                pass
            fixed = item.get('fixed') is True
            if fixed:
                asm.ClearSelection2(True)
                if component.Select2(False, 0):
                    asm.FixComponent()
                    asm.ClearSelection2(True)
            payload = _component_payload(component)
            payload['requested_path'] = path
            components.append(payload)
        return {'ok': True, 'doc': common.doc_identity(asm), 'components': components,
                'component_count': len(_components(asm))}
    finally:
        for doc in opened_parts:
            try:
                app.CloseDoc(common.doc_identity(doc)['title'])
            except Exception:
                pass


def action_mate(app, asm, spec):
    opened_parts = []
    results = []
    try:
        for item in spec.get('mates') or []:
            selector_a = item.get('a') or {}
            selector_b = item.get('b') or {}
            component_a = _find_component(asm, selector_a.get('component'))
            component_b = _find_component(asm, selector_b.get('component'))
            common.activate(app, asm)
            entity_a = _plane_entity(app, component_a, selector_a.get('plane'), opened_parts)
            entity_b = _plane_entity(app, component_b, selector_b.get('plane'), opened_parts)
            results.append(_add_mate(asm, component_a, component_b, entity_a, entity_b, item))
        return {'ok': True, 'doc': common.doc_identity(asm), 'mates': results,
                'mate_count': len(_mates(asm)), 'all_mates': _mates(asm)}
    finally:
        for doc in opened_parts:
            try:
                app.CloseDoc(common.doc_identity(doc)['title'])
            except Exception:
                pass


def action_fix(app, asm, spec):
    component = _find_component(asm, spec.get('component'))
    common.activate(app, asm)
    asm.ClearSelection2(True)
    if not component.Select2(False, 0):
        raise common.CadError('BUILD_FAILED', '选不中组件 %s。' % _component_name(component))
    if spec.get('fixed', True):
        asm.FixComponent()
    else:
        asm.UnfixComponent()
    asm.ClearSelection2(True)
    return {'ok': True, 'doc': common.doc_identity(asm), 'component': _component_payload(component)}


def action_list(asm):
    return {'ok': True, 'doc': common.doc_identity(asm),
            'components': [_component_payload(item) for item in _components(asm)],
            'mates': _mates(asm)}


def main(spec):
    action = str(spec.get('action') or 'list')
    workspace = common.workspace_of(spec)
    with common.sw_lock('sw_assembly:' + action, workspace=workspace):
        app = common.attach(launch=True)
        if action == 'new':
            return action_new(app, spec)
        token = spec.get('doc')
        asm, identity = common.find_document(app, token)
        common.verify_identity(asm, token)
        common.activate(app, asm)
        if action == 'insert':
            return action_insert(app, asm, spec)
        if action == 'mate':
            return action_mate(app, asm, spec)
        if action == 'fix':
            return action_fix(app, asm, spec)
        if action == 'list':
            return action_list(asm)
        raise common.CadError('BAD_ARGS', '未知 action：%s（可用 new/insert/mate/fix/list）' % action)


if __name__ == '__main__':
    common.run(main)
