# -*- coding: utf-8 -*-
"""sw_measure.py —— 读取 SolidWorks 文档的几何事实。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402


SCOPE_NAMES = ('summary', 'mass', 'sketches', 'planes', 'tree', 'all')
STATUS_TEXT = {0: 'unknown', 1: 'fully_defined', 2: 'under_defined', 3: 'over_defined',
               4: 'no_solution', 5: 'invalid_solution'}


def _sketch_entries(model, deep=False):
    """草图清单；deep=True 时进入编辑模式读段数/约束状态（读后立即退出，不修改几何）。"""
    entries = []
    for item in common.feature_list(model):
        if not common.is_sketch_feature(item):
            continue
        entry = {'name': item['name'], 'type': item['type'], 'segments': None,
                 'points': None, 'fully_defined': None, 'constrained_status': None}
        if deep:
            empty = common._empty_variant()
            try:
                selected = model.Extension.SelectByID2(item['name'], 'SKETCH', 0, 0, 0, False, 0, empty, 0)
                if not selected:
                    entry['audit_error'] = 'select_failed'
                else:
                    model.EditSketch()
                    sketch = model.SketchManager.ActiveSketch
                    if sketch is None:
                        entry['audit_error'] = 'no_active_sketch'
                    else:
                        try:
                            entry['segments'] = len(sketch.GetSketchSegments or [])
                        except Exception:
                            pass
                        try:
                            entry['points'] = len(sketch.GetSketchPoints or [])
                        except Exception:
                            pass
                        try:
                            status = int(sketch.GetConstrainedStatus)
                            entry['constrained_status'] = status
                            entry['constrained_status_text'] = STATUS_TEXT.get(status, str(status))
                            entry['fully_defined'] = status == 1
                        except Exception as error:  # noqa: BLE001
                            entry['audit_error'] = str(error)
                    try:
                        model.SketchManager.InsertSketch(True)
                    except Exception:
                        pass
            except Exception as error:  # noqa: BLE001
                entry['audit_error'] = str(error)
                try:
                    model.SketchManager.InsertSketch(True)
                except Exception:
                    pass
        entries.append(entry)
    return entries


def main(spec):
    workspace = common.workspace_of(spec)
    token = spec.get('doc')
    scope = spec.get('scope') or 'summary'
    if scope not in SCOPE_NAMES:
        raise common.CadError('BAD_ARGS', 'scope 只支持 %s，收到 %s' % ('/'.join(SCOPE_NAMES), scope))
    with common.sw_lock('sw_measure', workspace=workspace):
        app = common.attach(launch=False)
        model, identity = common.find_document(app, token)
        common.verify_identity(model, token)
        result = {'ok': True, 'doc': identity, 'scope': scope}
        if scope in ('summary', 'all', 'mass', 'tree'):
            box = common.bbox_mm(model)
            if box:
                result['bbox_mm'] = box['size_mm']
                result['bbox_min_mm'] = box['min_mm']
                result['bbox_max_mm'] = box['max_mm']
            result.update(common.body_counts(model))
            result['feature_count'] = common.feature_count(model)
        if scope in ('summary', 'all', 'tree'):
            result['features'] = common.feature_list(model)
        if scope in ('summary', 'all', 'mass') and spec.get('with_massprops'):
            try:
                mass = common.mass_properties(model)
                result.update(mass)
                result['volume_source'] = 'massprops'
            except common.CadError as error:
                result['volume_mm3'] = None
                result['volume_source'] = 'unavailable'
                result['warnings'] = [error.message]
        if scope in ('all', 'sketches'):
            result['sketches'] = _sketch_entries(model, deep=spec.get('deep_sketches') is not False)
        if scope in ('all', 'planes'):
            result['planes'] = [
                common.plane_axes_world(model, 'Front Plane'),
                common.plane_axes_world(model, 'Top Plane'),
                common.plane_axes_world(model, 'Right Plane'),
            ]
        return result


if __name__ == '__main__':
    common.run(main)
