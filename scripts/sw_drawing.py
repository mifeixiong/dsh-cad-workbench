# -*- coding: utf-8 -*-
"""sw_drawing.py —— 从零件/装配体生成工程图（第一角法布局）并导出。

实现要点（本机 SW 2024 实测）：
  * 逐视图用 CreateDrawViewFromModelView3 插入；动态分派下返回 IView，
    但**必须用中文命名视图**（*前视/*上视/*右视/*等轴测）才生效，
    英文 *Front/*Isometric 会静默返回 None；所以中英候选逐个试。
  * 新建的未保存图纸用 GetFirstView 遍历不到视图，保存后再取新指针才可靠；
    因此流程是「先按坐标建视图 → 保存 → 重新取指针 → 数视图/注尺寸/导出」。
  * Save3 在动态分派下会「类型不匹配」，改用 Save / SaveAs3。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402

SHEETS = {
    'A4': (0.297, 0.210),
    'A3': (0.420, 0.297),
    'A2': (0.594, 0.420),
    'A1': (0.841, 0.594),
    'A0': (1.189, 0.841),
}

VIEW_CANDIDATES = {
    'front': ('*前视', '*Front'),
    'top': ('*上视', '*Top'),
    'right': ('*右视', '*Right'),
    'left': ('*左视', '*Left'),
    'back': ('*后视', '*Back'),
    'bottom': ('*下视', '*Bottom'),
    'iso': ('*等轴测', '*Isometric'),
}


def _positions(width, height, views, first_angle):
    """按图幅给视图位置（米）。第一角法：俯视在下、右视在左。"""
    positions = {
        'front': (width * 0.48, height * 0.57),
        'top': (width * 0.48, height * 0.24 if first_angle else height * 0.88),
        'right': (width * 0.22 if first_angle else width * 0.76, height * 0.57),
        'left': (width * 0.76 if first_angle else width * 0.22, height * 0.57),
        'back': (width * 0.76, height * 0.24),
        'bottom': (width * 0.48, height * 0.06),
        'iso': (width * 0.82 if first_angle else width * 0.82, height * 0.24),
    }
    if len(views) == 1:
        only = views[0]
        return {only: (width * 0.5, height * 0.5)}
    return {view: positions.get(view, (width * 0.5, height * 0.5)) for view in views}


def _view_chain(drawing):
    """返回除图纸外的视图对象列表。"""
    result = []
    try:
        sheet = drawing.GetFirstView
    except Exception:
        return result
    if sheet is None:
        return result
    try:
        current = sheet.GetNextView
    except Exception:
        return result
    guard = 0
    while current is not None and guard < 100:
        guard += 1
        result.append(current)
        try:
            current = current.GetNextView
        except Exception:
            break
    return result


def _view_name(view):
    for attribute in ('GetName2', 'Name', 'GetName'):
        try:
            value = getattr(view, attribute)
            value = value() if callable(value) else value
            if value:
                return str(value)
        except Exception:
            continue
    return None


def _view_annotations(view):
    for attribute in ('GetAnnotationCount', 'GetDisplayDimensionCount', 'GetDimensionCount'):
        try:
            value = getattr(view, attribute)
            value = value() if callable(value) else value
            if value is not None:
                return int(value)
        except Exception:
            continue
    return None


def _view_list(drawing):
    views = []
    for view in _view_chain(drawing):
        entry = {'name': _view_name(view), 'annotations': _view_annotations(view)}
        try:
            position = view.Position
            entry['position_m'] = [round(float(value), 4) for value in position]
        except Exception:
            pass
        try:
            entry['scale'] = float(view.ScaleDecimal)
        except Exception:
            pass
        views.append(entry)
    return views


def _insert_view(drawing, model_path, view, x, y):
    """逐个候选名插入视图；返回 (成功?, 实际视图名)。"""
    for name in VIEW_CANDIDATES.get(view, ()):
        try:
            result = common.retry_busy(drawing.CreateDrawViewFromModelView3, model_path, name, x, y, 0)
        except Exception:  # noqa: BLE001
            continue
        if result is not None:
            return True, name
    return False, None


def main(spec):
    workspace = common.workspace_of(spec)
    out_dir = os.path.abspath(spec.get('out_dir') or os.path.join(workspace, 'cad-output', 'drawings'))
    common.ensure_dir(out_dir)
    views = [str(item).lower() for item in (spec.get('views') or ['front', 'top', 'right', 'iso'])]
    unknown = [view for view in views if view not in VIEW_CANDIDATES]
    if unknown:
        raise common.CadError('UNSUPPORTED', '不支持的视图：%s（可用 %s）' % (
            ', '.join(unknown), ', '.join(VIEW_CANDIDATES)))
    sheet = str(spec.get('sheet') or 'A3').upper()
    if sheet not in SHEETS:
        raise common.CadError('BAD_ARGS', '未知图幅：%s（可用 %s）' % (sheet, ', '.join(SHEETS)))
    overwrite = bool(spec.get('overwrite'))
    warnings = []
    with common.sw_lock('sw_drawing', workspace=workspace):
        app = common.attach(launch=False)
        model, identity = common.find_document(app, spec.get('doc'))
        common.verify_identity(model, identity['doc_id'])
        model_path = identity.get('path')
        if not model_path or not os.path.isfile(model_path):
            raise common.CadError('UNSUPPORTED', '源文档还没有保存到磁盘；请先用 cadw_session save_as 保存零件，再出工程图。')
        template = common.find_template(app, 'drawing', prefer='gb_' + sheet.lower())
        if not template.get('path'):
            raise common.CadError('UNSUPPORTED', '找不到工程图模板（.drwdot）。', {'template': template})
        drawing = common.retry_busy(app.NewDocument, template['path'], 0, 0, 0)
        if drawing is None:
            raise common.CadError('BUILD_FAILED', '新建工程图失败（NewDocument 返回 None）。')
        common.activate(app, drawing)
        width, height = SHEETS[sheet]
        positions = _positions(width, height, views, spec.get('first_angle', True))
        inserted = []
        for view in views:
            x, y = positions[view]
            ok, used_name = _insert_view(drawing, model_path, view, x, y)
            if ok:
                inserted.append({'view': view, 'api_name': used_name, 'position_m': [round(x, 4), round(y, 4)]})
            else:
                warnings.append('视图 %s 插入失败（中英命名都试过）。' % view)
        if not inserted:
            raise common.CadError('BUILD_FAILED', '一个视图都没有插入成功。', {'warnings': warnings})

        basename = spec.get('basename') or (os.path.splitext(identity.get('title') or 'drawing')[0] + '-drawing')
        drawing_path = os.path.join(out_dir, str(basename) + '.slddrw')
        if os.path.exists(drawing_path) and not overwrite:
            raise common.CadError('EXPORT_FAILED', '图纸已存在，拒绝覆盖：%s（确认请传 confirm=true）' % drawing_path)
        common.save(drawing, drawing_path)

        # 保存后重新取指针（未保存图纸遍历不到视图）
        fresh = None
        for candidate in common.open_documents(app):
            try:
                candidate_identity = common.doc_identity(candidate)
            except Exception:
                continue
            if candidate_identity.get('path') and os.path.normcase(os.path.abspath(candidate_identity['path'])) == os.path.normcase(os.path.abspath(drawing_path)):
                fresh = candidate
                break
        if fresh is None:
            raise common.CadError('BUILD_FAILED', '图纸保存后找不到已打开副本：%s' % drawing_path)

        dimension_count = None
        if spec.get('insert_model_dims', True):
            for args in ((0, 2, True, True, True, True), (0, 0, True, True, True, True), (0, 1, True, True, True, True)):
                try:
                    fresh.InsertModelAnnotations3(*args)
                    break
                except Exception:  # noqa: BLE001
                    continue
            else:
                warnings.append('导入模型尺寸失败（InsertModelAnnotations3 全部候选都报错）。')
            try:
                fresh.EditRebuild3()
            except Exception:
                pass
        common.save(fresh)

        view_entries = _view_list(fresh)
        if len(view_entries) < len(inserted):
            warnings.append('插入 %d 个视图，但保存后只数到 %d 个。' % (len(inserted), len(view_entries)))
        annotations = [entry.get('annotations') for entry in view_entries if entry.get('annotations')]
        if annotations:
            dimension_count = sum(annotations)
        elif spec.get('insert_model_dims', True):
            warnings.append('模型尺寸导入后注解数为 0（可能是零件没有标注尺寸）。')

        artifacts = []
        failures = []
        for fmt in [str(item).lower() for item in (spec.get('export') or ['pdf'])]:
            if fmt not in ('pdf', 'dxf', 'dwg'):
                failures.append({'format': fmt, 'code': 'UNSUPPORTED', 'message': '工程图只支持 pdf/dxf/dwg'})
                continue
            target = os.path.join(out_dir, str(basename) + '.' + fmt)
            if os.path.exists(target) and not overwrite:
                failures.append({'format': fmt, 'code': 'EXPORT_FAILED', 'message': '目标已存在：%s' % target})
                continue
            try:
                saved = common.export_document(fresh, target, overwrite=overwrite)
                artifacts.append({'format': fmt, 'path': saved['path'], 'bytes': saved['bytes'],
                                  'sha256': common.sha256_file(saved['path'])})
            except common.CadError as error:
                failures.append({'format': fmt, 'code': error.code, 'message': error.message})
        return {
            'ok': True,
            'doc': identity,
            'drawing': common.doc_identity(fresh),
            'drawing_path': drawing_path,
            'template': template,
            'sheet': sheet,
            'first_angle': bool(spec.get('first_angle', True)),
            'views': view_entries,
            'inserted': inserted,
            'view_count': len(view_entries),
            'dimension_count': dimension_count,
            'artifacts': artifacts,
            'failures': failures,
            'warnings': warnings + ['%s：%s' % (item['format'], item['message']) for item in failures],
        }


if __name__ == '__main__':
    common.run(main)
