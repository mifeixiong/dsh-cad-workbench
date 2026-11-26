# -*- coding: utf-8 -*-
"""cad_refkit.py 的离线用例：现场合成参考图与模型，不需要 SolidWorks / 参考图素材。

覆盖：标定（含两轴比例校验）→ 四种量测 → 目标带 → 网格叠图 → 差分（PASS/FAIL）。
运行：python test/refkit_cases.py　（退出码 0 = 全过）
"""
import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import cad_refkit as refkit  # noqa: E402

U_MIN, U_MAX = 0.0, 40.0
V_MIN, V_MAX = 0.0, 30.0
PX_U0, PX_U1 = 40.0, 360.0
PX_V0, PX_V1 = 260.0, 20.0     # v 向上：(x=260) → 0 m，(x=20) → 30 m


def px_u(meters):
    return PX_U0 + (meters - U_MIN) * (PX_U1 - PX_U0) / (U_MAX - U_MIN)


def px_v(meters):
    return PX_V0 + (meters - V_MIN) * (PX_V1 - PX_V0) / (V_MAX - V_MIN)


def make_frame(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (400, 300), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    # 船体：红块 u 2..38, v 0..12
    draw.rectangle([px_u(2), px_v(12), px_u(38), px_v(0)], fill=(200, 40, 30))
    # 上层建筑：灰白块 u 20..28, v 12..20
    draw.rectangle([px_u(20), px_v(20), px_u(28), px_v(12)], fill=(200, 200, 200))
    # 球罩：深色圆，心 (24, 22)，半径 2 m
    draw.ellipse([px_u(22), px_v(24), px_u(26), px_v(20)], fill=(20, 25, 30))
    image.save(path)
    return path


def run(action, workspace, **kwargs):
    spec = {'workspace': workspace, 'action': action}
    spec.update(kwargs)
    return refkit.main(spec)


def spec_boxes():
    """两个盒子的三角网格（u=x, v=z, w=y），米制。"""
    tris = []
    names = []

    def box(x0, y0, z0, x1, y1, z1):
        corners = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
        ]
        faces = [
            (0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]
        return [[list(corners[i]) for i in face] for face in faces]

    tris.append(box(2, -5, 0, 38, 5, 12))
    names.append('hull')
    tris.append(box(20, -4, 12, 28, 4, 20))
    names.append('accommodation')
    return {'names': names, 'tris': tris}


PARTS = [
    {'name': 'hull', 'bbox': [2, -5, 0, 38, 5, 12]},
    {'name': 'accommodation', 'bbox': [20, -4, 12, 28, 4, 20]},
]


CASES = []


def case(function):
    CASES.append(function)
    return function


@case
def case_calibrate(work):
    frame = make_frame(os.path.join(work, 'frame_05.png'))
    result = run('calibrate', work, project='demo', frame={
        'id': 'frame_05', 'path': frame, 'view': 'side',
        'u': {'px0': PX_U0, 'm0': U_MIN, 'px1': PX_U1, 'm1': U_MAX},
        'v': {'px0': PX_V0, 'm0': V_MIN, 'px1': PX_V1, 'm1': V_MAX},
    }, masks={
        'hull': {'type': 'color', 'r_min': 110, 'r_minus_b_min': 55, 'r_minus_g_min': 55},
        'block': {'type': 'color', 'r_min': 180, 'r_max': 230, 'g_min': 180, 'g_max': 230,
                  'b_min': 180, 'b_max': 230},
        'dark': {'type': 'dark', 'r_max': 70, 'g_max': 70, 'b_max': 80},
    }, grid={'u_step': 5, 'v_step': 5})
    assert result['ok'], result
    scale = result['frame']['scale']
    assert abs(scale['m_per_px_u'] - 0.125) < 1e-6, scale
    assert abs(scale['m_per_px_v'] + 0.125) < 1e-6, scale
    assert not [item for item in result['checks'] if '相差' in item], result['checks']


@case
def case_bad_calibration(work):
    try:
        run('calibrate', work, project='demo', frame={
            'id': 'broken', 'path': os.path.join(work, 'frame_05.png'), 'view': 'side',
            'u': {'px0': 10, 'm0': 0, 'px1': 10, 'm1': 10},
            'v': {'px0': 260, 'm0': 0, 'px1': 20, 'm1': 30},
        })
    except Exception as error:
        assert getattr(error, 'code', '') == 'REF_INVALID', (error, getattr(error, 'code', ''))
        return
    raise AssertionError('像素重合的标定应当被拒绝')


@case
def case_measure(work):
    result = run('measure', work, project='demo', frame_id='frame_05', measures=[
        {'id': 'hull_profile', 'kind': 'profile', 'mask': 'hull', 'u': [0, 40], 'step': 1,
         'v_filter': [None, 16], 'model_filter': 'hull'},
        {'id': 'super_box', 'kind': 'bbox', 'mask': 'block', 'roi': {'u': [15, 32], 'v': [10, 25]},
         'target': {'name': 'super_dv', 'part': 'accommodation', 'field': 'dv',
                    'model_field': 'dz', 'tol': 0.3}},
        {'id': 'hull_extent', 'kind': 'extent', 'mask': 'hull', 'at_u': 20, 'band': 1},
        {'id': 'block_cover', 'kind': 'coverage', 'mask': 'block', 'u': [16, 32], 'step': 1,
         'v_filter': [11, 21]},
    ])
    assert result['ok'], result
    measures = {item['id']: item for item in result['measures']}

    profile = measures['hull_profile']['result']['points']
    assert profile, profile
    inside = [point for point in profile if 3 <= point[0] <= 37]
    assert inside, profile
    for point in inside:
        assert abs(point[1] - 0.0) < 0.2, point
        assert abs(point[2] - 12.0) < 0.2, point

    box = measures['super_box']['result']
    assert abs(box['du'] - 8.0) < 0.3, box
    assert abs(box['dv'] - 8.0) < 0.3, box
    assert abs(box['u0'] - 20.0) < 0.3, box
    assert abs(box['v1'] - 20.0) < 0.3, box

    extent = measures['hull_extent']['result']
    assert abs(extent['v0']) < 0.2 and abs(extent['v1'] - 12.0) < 0.2, extent

    cover = measures['block_cover']['result']
    ratios = {round(point[0]): point[1] for point in cover['points']}
    assert ratios.get(24) == 1.0, ratios
    assert cover['gaps'], cover

    assert result['targets'], result['targets']
    target = [item for item in result['targets'] if item['name'] == 'super_dv'][0]
    assert abs(target['ref'][0] - 7.7) < 0.02 and abs(target['ref'][1] - 8.3) < 0.02, target


@case
def case_render(work):
    mesh_path = os.path.join(work, 'model.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    result = run('render', work, project='demo', mesh=mesh_path, parts=PARTS,
                 views=['side', 'top'], label_parts=True)
    assert result['ok'], result
    assert len(result['model_views']) == 2, result['model_views']
    assert result['ref_views'], result
    for item in result['images']:
        assert not item['blank'], item
    assert result['contact_sheet'] and os.path.isfile(result['contact_sheet'])
    cells = result['cells']
    # 合成几何：accommodation 中心 (24, 16) → 列 E（24/5=4）、行 4（16/5=3）
    assert cells['accommodation']['cell'] == 'E4', cells['accommodation']
    assert cells['hull']['cell'] == 'E2', cells['hull']


@case
def case_diff_pass(work):
    result = run('diff', work, project='demo', parts=PARTS, targets=[
        {'name': 'super_dv', 'part': 'accommodation', 'field': 'dz', 'ref': [7.7, 8.3]},
        {'name': 'hull_len', 'part': 'hull', 'field': 'dx', 'ref': [35.5, 36.5]},
    ])
    assert result['ok'], result
    verdicts = {row['name']: row['verdict'] for row in result['rows']}
    assert verdicts == {'super_dv': 'PASS', 'hull_len': 'PASS'}, result['rows']
    assert result['profile_rows'], 'profile 量测存在时应给轮廓行'
    assert all(row['verdict'] == 'PASS' for row in result['profile_rows']), [
        row for row in result['profile_rows'] if row['verdict'] != 'PASS']
    assert os.path.isfile(result['report_path'])
    with open(result['report_path'], 'r', encoding='utf-8') as handle:
        assert '差分表' in handle.read()


@case
def case_diff_fail(work):
    parts = [
        {'name': 'hull', 'bbox': [2, -5, 0, 38, 5, 12]},
        {'name': 'accommodation', 'bbox': [20, -4, 12, 28, 4, 20.9]},
    ]
    result = run('diff', work, project='demo', parts=parts, targets=[
        {'name': 'super_dv', 'part': 'accommodation', 'field': 'dz', 'ref': [7.7, 8.3]},
    ])
    row = [item for item in result['rows'] if item['name'] == 'super_dv'][0]
    assert row['verdict'] == 'FAIL', row
    assert abs(row['delta'] - 0.6) < 0.05, row
    assert row['cell'] == 'E4', row
    assert result['verdict'] == 'FAIL', result['verdict']


@case
def case_profile_fail(work):
    mesh_path = os.path.join(work, 'model_tall.json')
    boxes = spec_boxes()
    shifted = []
    for part in boxes['tris']:
        shifted.append([[[point[0], point[1], point[2] + 3.0] for point in tri] for tri in part])
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump({'names': boxes['names'], 'tris': shifted}, handle)
    result = run('diff', work, project='demo', parts=PARTS, mesh=mesh_path)
    fails = [row for row in result['profile_rows'] if row['verdict'] == 'FAIL']
    assert len(fails) >= 6, result['profile_rows']
    assert all(abs(row['delta_vmax'] - 3.0) < 0.4 for row in fails), fails
    assert all(row['cell'][0] in 'ABCDEFGH' for row in fails), fails


@case
def case_errors(work):
    try:
        run('get', work, project='missing')
    except Exception as error:
        assert getattr(error, 'code', '') == 'REF_NOT_FOUND', error
    else:
        raise AssertionError('不存在的工程应当报 REF_NOT_FOUND')
    listing = run('list', work)
    assert any(item['project'] == 'demo' for item in listing['projects']), listing
    loaded = run('get', work, project='demo')
    assert 'frames' in loaded['project'] and loaded['project']['frames'], loaded


@case
def case_calibrate_ratio_warning(work):
    frame = make_frame(os.path.join(work, 'frame_ratio.png'))
    result = run('calibrate', work, project='ratio', frame={
        'id': 'frame_ratio', 'path': frame, 'view': 'side',
        'u': {'px0': PX_U0, 'm0': U_MIN, 'px1': PX_U1, 'm1': U_MAX},
        'v': {'px0': PX_V0, 'm0': V_MIN, 'px1': PX_V1, 'm1': V_MAX * 0.5},
    })
    assert any('相差' in item for item in result['checks']), result['checks']


@case
def case_measure_unknown_mask(work):
    try:
        run('measure', work, project='demo', frame_id='frame_05',
            measures=[{'id': 'x', 'kind': 'bbox', 'mask': 'not-exist', 'roi': {'u': [0, 1], 'v': [0, 1]}}])
    except Exception as error:
        assert getattr(error, 'code', '') == 'BAD_ARGS', error
        return
    raise AssertionError('未知掩膜应当报 BAD_ARGS')


@case
def case_render_unknown_view(work):
    mesh_path = os.path.join(work, 'model_views.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    try:
        run('render', work, project='demo', mesh=mesh_path, parts=PARTS, views=['sideways'])
    except Exception as error:
        assert getattr(error, 'code', '') == 'BAD_ARGS', error
        return
    raise AssertionError('未知视图应当报 BAD_ARGS')


@case
def case_diff_targets_json_string(work):
    result = run('diff', work, project='demo', parts=PARTS,
                 targets=json.dumps([{'name': 'hull_len', 'part': 'hull', 'field': 'dx', 'ref': [35.5, 36.5]}]))
    assert result['rows'][0]['verdict'] == 'PASS', result['rows']


@case
def case_render_slice(work):
    mesh_path = os.path.join(work, 'model_slice.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    result = run('render', work, project='demo', mesh=mesh_path, parts=PARTS,
                 views=['side'], slice={'axis': 'x', 'from': 2, 'to': 10})
    assert result['model_views'], result
    assert not result['images'][0]['blank'], result['images'][0]


@case
def case_render_section_requires_slice(work):
    mesh_path = os.path.join(work, 'model_section.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    try:
        run('render', work, project='demo', mesh=mesh_path, parts=PARTS, views=['section'])
    except Exception as error:
        assert getattr(error, 'code', '') == 'BAD_ARGS', error
        return
    raise AssertionError('section 视图缺 slice 应当报 BAD_ARGS')
    result = run('render', work, project='demo', mesh=mesh_path, parts=PARTS,
                 views=['section'], slice={'axis': 'x', 'from': 2, 'to': 10})
    assert result['model_views'], result


def write_binary_stl(path, triangles):
    import struct
    with open(path, 'wb') as handle:
        handle.write(b' ' * 80)
        handle.write(struct.pack('<I', len(triangles)))
        for triangle in triangles:
            handle.write(struct.pack('<fff', 0.0, 0.0, 1.0))
            for point in triangle:
                handle.write(struct.pack('<fff', *point))
            handle.write(struct.pack('<H', 0))
    return path


@case
def case_render_from_stl(work):
    # 二进制 STL 分支：两个盒子（单位 mm），渲染必须出图且不空白
    boxes = spec_boxes()
    triangles = []
    for part in boxes['tris']:
        for triangle in part:
            triangles.append([[point[0] * 1000, point[1] * 1000, point[2] * 1000] for point in triangle])
    path = write_binary_stl(os.path.join(work, 'model.stl'), triangles)
    result = run('render', work, project='demo', stl=path, stl_units='mm', parts=PARTS, views=['side'])
    assert result['model_views'], result
    assert not [item for item in result['images'] if item['blank']], result['images']


@case
def case_profile_bins_cover_tail(work):
    # [0,10] step 4 必须出 3 箱且覆盖到 10（原来 2 箱，尾部静默丢失）
    result = run('measure', work, project='demo', frame_id='frame_05', measures=[
        {'id': 'tail', 'kind': 'profile', 'mask': 'hull', 'u': [0, 10], 'step': 4}])
    points = result['measures'][0]['result']['points']
    assert len(points) == 3, points
    assert points[-1][0] >= 8.0, points
    coverage = run('measure', work, project='demo', frame_id='frame_05', measures=[
        {'id': 'cover_tail', 'kind': 'coverage', 'mask': 'hull', 'u': [0, 10], 'step': 4}])
    assert len(coverage['measures'][0]['result']['points']) == 3, coverage['measures'][0]['result']


@case
def case_extent_band_zero(work):
    result = run('measure', work, project='demo', frame_id='frame_05', measures=[
        {'id': 'band0', 'kind': 'extent', 'mask': 'hull', 'at_u': 20, 'band': 0}])
    box = result['measures'][0]['result']
    assert box['v0'] is not None and box['v1'] is not None, box
    assert box['u0'] == box['u1'] == 20 or abs(box['u0'] - 20) < 1e-6, box


@case
def case_save_targets_top_level(work):
    result = run('measure', work, project='demo', frame_id='frame_05', save_targets=True, tol=0.4, measures=[
        {'id': 'auto_box', 'kind': 'bbox', 'mask': 'block', 'roi': {'u': [15, 32], 'v': [10, 25]}}])
    names = [item['name'] for item in result['targets']]
    assert 'auto_box_du' in names, names


@case
def case_measure_requires_id(work):
    try:
        run('measure', work, project='demo', frame_id='frame_05', measures=[{'kind': 'bbox', 'mask': 'hull'}])
    except Exception as error:
        assert getattr(error, 'code', '') == 'BAD_ARGS', error
        return
    raise AssertionError('缺 id 的量测应当报 BAD_ARGS')


@case
def case_warn_uses_half_band(work):
    parts = [{'name': 'hull', 'bbox': [2, -5, 0, 38, 5, 12]},
             {'name': 'accommodation', 'bbox': [20, -4, 12, 28, 4, 20.55]}]
    result = run('diff', work, project='demo', parts=parts, profile=False,
                 targets=[{'name': 't', 'part': 'accommodation', 'field': 'dz', 'ref': [7.7, 8.3]}])
    row = result['rows'][0]
    assert abs(row['delta'] - 0.25) < 1e-6, row
    assert row['verdict'] == 'FAIL', row  # 0.25 > 0.5 × 0.3


@case
def case_diff_mesh_only(work):
    mesh_path = os.path.join(work, 'mesh_only.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    result = run('diff', work, project='demo', mesh=mesh_path, targets=[])
    assert result['profile_rows'], result
    assert result['rows'] == [], result['rows']


@case
def case_model_filter_missing(work):
    mesh_path = os.path.join(work, 'mesh_filter.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    run('measure', work, project='demo', frame_id='frame_05', measures=[
        {'id': 'filtered', 'kind': 'profile', 'mask': 'hull', 'u': [2, 38], 'step': 4,
         'model_filter': 'zzz-nothing', 'compare': ['vmax']}])
    result = run('diff', work, project='demo', mesh=mesh_path, parts=PARTS,
                 targets=[], profile_measures=['filtered'])
    assert result['profile_rows'], result
    assert all(row['verdict'] == 'MISSING' for row in result['profile_rows']), result['profile_rows'][:2]


@case
def case_frame_slice_takes_effect(work):
    mesh_path = os.path.join(work, 'mesh_slice2.json')
    with open(mesh_path, 'w', encoding='utf-8') as handle:
        json.dump(spec_boxes(), handle)
    frame = os.path.join(work, 'frame_slice.png')
    make_frame(frame)
    run('calibrate', work, project='sliceproj', frame={
        'id': 'f', 'path': frame, 'view': 'side',
        'u': {'px0': PX_U0, 'm0': U_MIN, 'px1': PX_U1, 'm1': U_MAX},
        'v': {'px0': PX_V0, 'm0': V_MIN, 'px1': PX_V1, 'm1': V_MAX},
        'slice': {'axis': 'x', 'from': 15, 'to': 25}}, grid={'u_step': 5, 'v_step': 5})
    result = run('render', work, project='sliceproj', mesh=mesh_path, views=['side'])
    assert result['model_views'], result


@case
def case_view_axes_and_cells(work):
    from PIL import Image
    boxes = run('render', work, project='demo', parts=PARTS, views=['front'], scale=10)
    assert boxes['model_views'], boxes
    with Image.open(boxes['model_views'][0]) as image:
        width = image.width
    # front 视图横轴是 y ∈ [-4, 4]（accommodation）→ 画布宽度远小于 x 跨度
    assert width < 400, width
    by_view = boxes['cells_by_view']['front']
    assert by_view['u_axis'] == 'y' and by_view['v_axis'] == 'z', by_view


@case
def case_cells_full_range(work):
    cell, touched = refkit.cells_of_bbox([0, -8, 0, 20, 8, 10], {'u_step': 5, 'v_step': 5})
    assert cell == 'C2', (cell, touched)
    assert {'A1', 'B2', 'D2', 'E3'} <= set(touched), touched
    assert len(touched) >= 15, touched


@case
def case_stl_trailing_bytes(work):
    boxes = spec_boxes()
    triangles = []
    for part in boxes['tris']:
        for triangle in part:
            triangles.append([[point[0] * 1000, point[1] * 1000, point[2] * 1000] for point in triangle])
    path = write_binary_stl(os.path.join(work, 'trailing.stl'), triangles)
    with open(path, 'ab') as handle:
        handle.write(b'\n')
    result = run('render', work, project='demo', stl=path, views=['side'])
    assert result['model_views'], result


@case
def case_no_partial_artifacts_on_bad_view(work):
    out_dir = os.path.join(work, 'partial_out')
    try:
        run('render', work, project='demo', parts=PARTS, views=['side', 'bogus'], out_dir=out_dir)
    except Exception as error:
        assert getattr(error, 'code', '') == 'BAD_ARGS', error
        assert not os.path.isdir(out_dir) or not os.listdir(out_dir), os.listdir(out_dir) if os.path.isdir(out_dir) else []
        return
    raise AssertionError('未知视图应当报 BAD_ARGS')


import cad_refkit  # noqa: E402  (供 case_cells_full_range 直接用)


@case
def case_diff_report_name_unique(work):
    # 紧循环里连跑 20 次：毫秒级时间戳在快机器/CI 上仍会撞（真实 CI 失败就是这条）
    paths = set()
    for _index in range(20):
        result = run('diff', work, project='demo', parts=PARTS,
                     targets=[{'name': 'x', 'part': 'hull', 'field': 'dx', 'ref': [35, 37]}])
        path = result['report_path']
        assert path.endswith('.md'), path
        assert path not in paths, ('报告名重复：', path)
        paths.add(path)


def main():
    passed = 0
    failed = 0
    work = tempfile.mkdtemp(prefix='cadw-refkit-')
    try:
        for case in CASES:
            try:
                case(work)
                print('PASS ' + case.__name__)
                passed += 1
            except Exception as error:  # noqa: BLE001 - 用例边界
                print('FAIL ' + case.__name__ + ' — ' + str(error))
                import traceback
                traceback.print_exc()
                failed += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('\nREFKIT CASES %d/%d PASS' % (passed, passed + failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())