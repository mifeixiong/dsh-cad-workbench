# -*- coding: utf-8 -*-
"""sw_measure.py —— 读取 SolidWorks 文档的几何事实。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402


def _sketch_entries(model):
    entries = []
    for item in common.feature_list(model):
        type_name = str(item.get('type', '')).lower()
        if 'sketch' not in type_name and 'profile' not in type_name:
            continue
        entry = {'name': item['name'], 'type': item['type'], 'segments': None,
                 'fully_defined': None, 'constrained_status': None}
        try:
            feature = model.Extension.GetObjectByName(item['name'])
        except Exception:
            feature = None
        entries.append(entry)
    return entries


def main(spec):
    workspace = common.workspace_of(spec)
    token = spec.get('doc')
    scope = spec.get('scope') or 'summary'
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
            result['sketches'] = _sketch_entries(model)
        if scope in ('all', 'planes'):
            result['planes'] = [
                common.plane_axes_world(model, 'Front Plane'),
                common.plane_axes_world(model, 'Top Plane'),
                common.plane_axes_world(model, 'Right Plane'),
            ]
        return result


if __name__ == '__main__':
    common.run(main)
