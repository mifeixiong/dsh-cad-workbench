# -*- coding: utf-8 -*-
"""
sw_build.py —— 把 Feature Graph 变成 SolidWorks 特征（确定性执行 + 回滚 + 实测）。

顺序：图校验 → 取文档 → 逐节点执行 → 重建 → 实测 → 回滚（失败时）。
单位：对外 mm；调用 SolidWorks API 时在 sw_common 内换算成米。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import graph as graph_module  # noqa: E402
import sw_common as common  # noqa: E402


def _sketch_name_for(node, built):
    index = node.get('sketch_index')
    if index is None:
        raise common.CadError('BUILD_FAILED', '节点 %s 没有可用的草图引用。' % node.get('id'))
    entry = built[index]
    name = entry.get('sketch_name')
    if not name:
        raise common.CadError('BUILD_FAILED', '节点 %s 的草图没有建成功。' % node.get('id'))
    return name


def _world_to_sketch(active_sketch, x_mm, y_mm, z_mm):
    """把世界坐标（mm）换算到当前草图坐标（mm）。

    SolidWorks 的 IRefPlane/Sketch 变换是刚体变换：数组 0..8 是旋转矩阵（行/列），
    9..11 是平移。这里用 R^T * (p - t) 求逆变换，不依赖特定版本的 TransformPoint。
    """
    try:
        transform = active_sketch.ModelToSketchTransform
        data = [float(value) for value in transform.ArrayData]
    except Exception as error:  # noqa: BLE001
        raise common.CadError(
            'UNSUPPORTED',
            '读不到草图的 ModelToSketchTransform，无法把世界坐标换算到草图坐标。',
            {'error': str(error)},
        )
    if len(data) < 12:
        raise common.CadError('UNSUPPORTED', '草图变换数组长度异常：%d' % len(data))
    # IModelToSketchTransform 是「模型 → 草图」，ArrayData 的 0..8 按**列主序**存旋转，
    # 9..11 是平移：s = R·p + t。实测（Top 面 y=10）用 R^T(p-t) 会把孔位镜像 20mm。
    px, py, pz = x_mm * common.MM, y_mm * common.MM, z_mm * common.MM
    sx = (data[0] * px + data[3] * py + data[6] * pz + data[9]) * 1000.0
    sy = (data[1] * px + data[4] * py + data[7] * pz + data[10]) * 1000.0
    return sx, sy


def _sketch_node(model, node):
    plane = node.get('plane')
    face = plane if isinstance(plane, dict) else None
    plane_name = None if face else plane
    common.start_sketch(model, plane=plane_name, face=face.get('face') if face else None)
    manager = model.SketchManager
    # AddToDB：跳过推理/捕捉求解，显著加快实体创建；结束草图时 MergePoints 负责缝合端点。
    try:
        manager.AddToDB = True
    except Exception:
        pass
    try:
        common.sketch_entities(manager, node.get('entities', []))
    finally:
        try:
            manager.AddToDB = False
        except Exception:
            pass
    if node.get('centerline'):
        (x1, y1), (x2, y2) = node['centerline']
        manager.CreateCenterLine(x1 * common.MM, y1 * common.MM, 0, x2 * common.MM, y2 * common.MM, 0)
    name = common.end_sketch(model)
    if not name:
        raise common.CadError('BUILD_FAILED', '草图创建后找不到特征名。')
    return name


def execute_node(model, node, built):
    op = node['op']
    if op == 'sketch':
        name = _sketch_node(model, node)
        feature = {'name': name, 'type': 'sketch'}
    elif op == 'extrude':
        name = _sketch_name_for(node, built)
        feature = common.extrude(model, name, node['depth'], midplane=node.get('direction') == 'midplane',
                                 flip=node.get('flip', False))
        feature = {'name': str(common._call_or_value(feature, 'Name')), 'type': 'extrude'}
    elif op == 'cut':
        name = _sketch_name_for(node, built)
        feature = common.cut(model, name, node.get('depth'), through=node.get('through_all', False),
                             flip=node.get('flip', False))
        feature = {'name': str(common._call_or_value(feature, 'Name')), 'type': 'cut'}
    elif op == 'revolve':
        name = _sketch_name_for(node, built)
        feature = common.revolve(model, name, node.get('angle', 360), cut_mode=node.get('type') == 'cut')
        feature = {'name': str(common._call_or_value(feature, 'Name')), 'type': 'revolve'}
    elif op == 'hole':
        x, y, z = node['at']
        common.select_face_at(model, x, y, z, append=False)
        model.SketchManager.InsertSketch(True)
        manager = model.SketchManager
        active = manager.ActiveSketch
        sx, sy = _world_to_sketch(active, x, y, z)
        try:
            manager.AddToDB = True
        except Exception:
            pass
        try:
            manager.CreateCircleByRadius(sx * common.MM, sy * common.MM, 0, node['diameter'] / 2.0 * common.MM)
        finally:
            try:
                manager.AddToDB = False
            except Exception:
                pass
        name = common.end_sketch(model)
        if not name:
            raise common.CadError('BUILD_FAILED', '孔草图没有建成功。')
        try:
            feat = common.cut(model, name, node.get('depth'), through=node.get('through_all', True), flip=False)
        except common.CadError:
            feat = common.cut(model, name, node.get('depth'), through=node.get('through_all', True), flip=True)
        feature = {'name': str(common._call_or_value(feat, 'Name')), 'type': 'hole'}
    elif op == 'fillet':
        if node.get('edges') == 'all':
            common.select_all_edges(model)
            feat = common.fillet_selected(model, node['radius'])
        else:
            feat = common.fillet(model, node['radius'], node['edges'])
        feature = {'name': str(common._call_or_value(feat, 'Name')), 'type': 'fillet'}
    elif op == 'chamfer':
        if node.get('edges') == 'all':
            common.select_all_edges(model)
            feat = common.chamfer_selected(model, node['distance'], node.get('angle', 45))
        else:
            feat = common.chamfer(model, node['distance'], node['edges'], node.get('angle', 45))
        feature = {'name': str(common._call_or_value(feat, 'Name')), 'type': 'chamfer'}
    else:
        raise common.CadError('UNSUPPORTED', '未实现的 op：%s' % op)
    if not feature.get('name'):
        raise common.CadError('BUILD_FAILED', '节点 %s 没有产生特征。' % node.get('id'))
    return feature


def rollback(model, before_count, feature_names):
    """按名删除本次创建的特征（精确，不碰用户原有特征），再复验。"""
    info = common.rollback_features(model, before_count, feature_names)
    if not info.get('done'):
        # 退路：逐次 Undo（有睡眠，undo 有时前几次是空操作），但绝不越过 before_count
        info['undo_attempts'] = 0
        for _ in range(12):
            current = common.feature_count(model)
            if current <= before_count:
                info['done'] = True
                break
            try:
                model.ClearSelection2(True)
                model.EditUndo2(1)
            except Exception:
                break
            info['undo_attempts'] += 1
            time.sleep(0.25)
            try:
                model.ForceRebuild3(False)
            except Exception:
                pass
        info['restored_count'] = common.feature_count(model)
        info['done'] = info['restored_count'] <= before_count
    return info


def main(spec):
    try:
        graph, stats = graph_module.validate(spec.get('graph'))
    except graph_module.GraphError as error:
        raise common.CadError('GRAPH_INVALID', error.message,
                              {'node': error.node, 'path': error.path, 'hint': error.hint})
    graph_digest = graph_module.graph_hash(graph)
    if spec.get('dry_run'):
        return {
            'ok': True,
            'dry_run': True,
            'graph': graph,
            'stats': stats,
            'graph_hash': graph_digest,
            'plan': _plan_text(graph),
        }

    workspace = common.workspace_of(spec)
    token = spec.get('doc')
    with common.sw_lock('sw_build', workspace=workspace):
        app = common.attach(launch=True)
        model, identity = common.find_document(app, token)
        common.verify_identity(model, token)
        common.activate(app, model)
        before = common.feature_count(model)
        initial = common.feature_list(model)
        initial_names = [item['name'] for item in initial]
        known_names = set(initial_names)
        built = []
        created = []
        warnings = []
        try:
            for index, node in enumerate(graph['nodes']):
                try:
                    feature = execute_node(model, node, built)
                    names_after = [item['name'] for item in common.feature_list(model)]
                    new_features = [name for name in names_after if name not in known_names]
                    known_names.update(names_after)
                    created.append({'index': index, 'op': node['op'], 'name': feature['name'],
                                    'features': new_features or [feature['name']]})
                    built.append({
                        'sketch_name': feature['name'] if node['op'] == 'sketch' else None,
                        'feature_name': feature['name'],
                    })
                except common.CadError as error:
                    raise common.CadError(
                        'BUILD_FAILED',
                        '第 %d 个节点（%s）失败：%s' % (index + 1, node['op'], error.message),
                        {'node_index': index, 'node': node.get('id'), 'op': node['op'],
                         'cause': error.envelope().get('error')},
                    )
                except Exception as error:  # noqa: BLE001 - 非 CadError 也必须回滚
                    raise common.CadError(
                        'BUILD_FAILED',
                        '第 %d 个节点（%s）异常：%s: %s' % (index + 1, node['op'],
                                                          error.__class__.__name__, error),
                        {'node_index': index, 'node': node.get('id'), 'op': node['op'],
                         'exception': error.__class__.__name__},
                    )
            common.rebuild(model)
        except common.CadError as error:
            rollback_info = {'attempted': False, 'done': False, 'before_count': before}
            current_names = [item['name'] for item in common.feature_list(model)]
            leftovers = [name for name in current_names if name not in set(initial_names)]
            if spec.get('rollback_on_fail', True):
                all_names = [name for item in created for name in item.get('features', [])]
                for name in leftovers:
                    if name not in all_names:
                        all_names.append(name)
                rollback_info = rollback(model, before, all_names)
            info = error.details if isinstance(error.details, dict) else {}
            info['features_before'] = before
            info['features_after'] = common.feature_count(model)
            info['leftovers'] = leftovers
            info['rollback'] = rollback_info
            info['created'] = created
            if rollback_info.get('attempted') and not rollback_info.get('done'):
                error.code = 'ROLLBACK_FAILED'
                error.message = error.message + '（回滚未完成，文档可能残留特征）'
            error.details = info
            raise

        after = common.feature_count(model)
        measurements = {'feature_count': after, 'bbox_mm': None, 'volume_mm3': None, 'volume_source': 'not_requested'}
        box = common.bbox_mm(model)
        if box:
            measurements['bbox_mm'] = box['size_mm']
            measurements['bbox_min_mm'] = box['min_mm']
            measurements['bbox_max_mm'] = box['max_mm']
        expect = spec.get('expect') or {}
        if isinstance(expect, dict) and expect.get('volume_mm3') is not None:
            try:
                mass = common.mass_properties(model)
                measurements.update({
                    'volume_mm3': mass['volume_mm3'],
                    'surface_area_mm2': mass['surface_area_mm2'],
                    'mass_kg': mass['mass_kg'],
                    'volume_source': 'massprops',
                })
            except common.CadError as error:
                measurements['volume_source'] = 'unavailable'
                warnings.append('体积读取失败：' + error.message)
        return {
            'ok': True,
            'dry_run': False,
            'graph_hash': graph_digest,
            'doc': identity,
            'created': created,
            'created_count': len(created),
            'features_before': before,
            'features_after': after,
            'measurements': measurements,
            'verification': {'status': 'measured' if measurements.get('bbox_mm') else 'unverified',
                             'checks': [], 'failures': []},
            'warnings': warnings,
        }


def _plan_text(graph):
    lines = []
    for index, node in enumerate(graph['nodes']):
        if node['op'] == 'sketch':
            count = len(node.get('entities', []))
            plane = node.get('plane')
            plane_text = plane if isinstance(plane, str) else 'face@' + str(plane.get('face'))
            lines.append('%d. 草图（%s，%d 个实体）' % (index + 1, plane_text, count))
        elif node['op'] == 'extrude':
            lines.append('%d. 拉伸 %s mm（%s）' % (index + 1, node['depth'], node['direction']))
        elif node['op'] == 'cut':
            lines.append('%d. 切除 %s' % (index + 1, '完全贯穿' if node.get('through_all') else str(node.get('depth')) + ' mm'))
        elif node['op'] == 'revolve':
            lines.append('%d. 旋转 %s°' % (index + 1, node['angle']))
        elif node['op'] == 'hole':
            lines.append('%d. 孔 Ø%s @%s' % (index + 1, node['diameter'], node['at']))
        elif node['op'] in ('fillet', 'chamfer'):
            lines.append('%d. %s %s mm' % (index + 1, '圆角' if node['op'] == 'fillet' else '倒角',
                                           node.get('radius') or node.get('distance')))
    return lines


if __name__ == '__main__':
    common.run(main)
