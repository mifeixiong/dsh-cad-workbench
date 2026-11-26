# -*- coding: utf-8 -*-
"""cad_audit.py 的离线用例：合成事实数据触发九角度，不需要 SolidWorks。

运行：python test/audit_cases.py（退出码 0 = 全过）
"""
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

import cad_audit  # noqa: E402

WORK = tempfile.mkdtemp(prefix='cadw-audit-')
CASES = []


def case(function):
    CASES.append(function)
    return function


def audit(**kwargs):
    spec = {'workspace': WORK, 'write_report': False}
    spec.update(kwargs)
    return cad_audit.main(spec)


def angles(result, angle):
    return [item for item in result['findings'] if item['angle'] == angle]


CLEAN_PARTS = [{'name': 'plate', 'bbox': [0, 0, 0, 120, 80, 10]}]
CLEAN_FACTS = {'bbox_mm': [120, 80, 10], 'volume_mm3': 95214.6, 'solid_count': 1,
               'sketches': [{'name': 'Sketch1', 'segments': 4, 'fully_defined': True}]}


@case
def case_clean():
    result = audit(parts=CLEAN_PARTS, facts=CLEAN_FACTS,
                   support_planes=[0.0], model_bbox=[-0.1, -0.1, -0.1, 120.1, 80.1, 10.1],
                   rules={'cantilever': ['plate']})
    assert result['verdict'] == 'PASS', result['findings']
    assert result['counts']['total'] == 0, result['findings']


@case
def case_float():
    parts = [{'name': 'deck', 'bbox': [0, 0, 0, 50, 10, 9.3]},
             {'name': 'box on deck', 'bbox': [5, 0, 9.7, 10, 5, 11.0]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3])
    items = angles(result, 'float')
    assert len(items) == 1, items
    assert items[0]['severity'] == 'P0', items[0]
    assert abs(items[0]['delta'] - 0.4) < 1e-6, items[0]
    assert 'box on deck' in items[0]['subject']


@case
def case_buried():
    # 只有盒状大件能当宿主（船体/线型件 bbox 是覆盖半条船的大空盒，不当宿主）
    parts = [{'name': 'hangar box', 'bbox': [0, -10, 0, 30, 10, 14]},
             {'name': 'inner tank', 'bbox': [10, -1, 5, 14, 1, 7]}]
    result = audit(parts=parts, support_planes=[0.0])
    items = angles(result, 'buried')
    assert items, result['findings']
    assert items[0]['severity'] == 'P0'
    assert 'hangar box' in items[0]['actual'] or 'hangar box' in str(items[0]['evidence'])


@case
def case_buried_host_caliber():
    # 船体当宿主时不判埋入（避免甲板设备全量误报）
    parts = [{'name': 'hull loft', 'bbox': [0, -8.9, 0, 89.95, 8.9, 13.75]},
             {'name': 'a frame cross 1', 'bbox': [2, -5, 9.4, 4, 5, 9.8]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3], rules={'cantilever': ['a frame']})
    assert not angles(result, 'buried'), angles(result, 'buried')


@case
def case_buried_ignores_surface_parts():
    # 贴面件（舷窗）bbox 与大壳体相交，但一面共面 —— 不应报埋入
    parts = [{'name': 'hull loft', 'bbox': [0, -8.9, 0, 89.95, 8.9, 13.75]},
             {'name': 'porthole row1', 'bbox': [40, 8.7, 9.7, 41, 8.9, 10.2]}]
    result = audit(parts=parts, support_planes=[0.0])
    assert not angles(result, 'buried'), angles(result, 'buried')


@case
def case_clash():
    # 交叠占小件体积 83% > clash_frac 0.8 才算穿插（bbox 局部相碰不算）
    parts = [{'name': 'block a', 'bbox': [0, 0, 0, 3, 3, 3]},
             {'name': 'block b', 'bbox': [0.5, 0, 0, 3.5, 3, 3]}]
    result = audit(parts=parts, support_planes=[0.0])
    items = angles(result, 'clash')
    assert len(items) == 1, items
    assert items[0]['severity'] == 'P1'
    assert abs(items[0]['delta'] - 22.5) < 1e-6, items[0]


@case
def case_clash_partial_overlap_is_quiet():
    # 局部相碰（33%）不报：这条判据只抓"几乎整件插进对方"
    parts = [{'name': 'block a', 'bbox': [0, 0, 0, 3, 3, 3]},
             {'name': 'block b', 'bbox': [2, 0, 0, 5, 3, 3]}]
    result = audit(parts=parts, support_planes=[0.0])
    assert not angles(result, 'clash'), angles(result, 'clash')


@case
def case_dup():
    parts = [{'name': 'copy', 'bbox': [0, 0, 0, 2, 2, 2]},
             {'name': 'copy', 'bbox': [10, 0, 0, 12, 2, 2]},
             {'name': 'twin a', 'bbox': [20, 0, 0, 22, 2, 2]},
             {'name': 'twin b', 'bbox': [20, 0, 0, 22, 2, 2]}]
    result = audit(parts=parts, support_planes=[0.0])
    items = angles(result, 'dup')
    assert len(items) >= 2, items
    same_name = [item for item in items if '2 个同名' in item['actual']]
    iou_items = [item for item in items if 'IoU' in item['actual']]
    assert same_name and same_name[0]['severity'] == 'P1', items
    # bbox IoU 只做 P2 提示：交叉撑/对角杆共用包络时 IoU 也是 1.00
    assert iou_items and iou_items[0]['severity'] == 'P2', items


@case
def case_sketch():
    facts = dict(CLEAN_FACTS)
    facts['sketches'] = [{'name': 'Sketch1', 'segments': 12, 'fully_defined': False},
                         {'name': 'Sketch2', 'segments': 8, 'fully_defined': True, 'over_defined': 2}]
    result = audit(parts=CLEAN_PARTS, facts=facts, support_planes=[0.0], rules={'cantilever': ['plate']})
    items = angles(result, 'sketch')
    assert len(items) == 2, items
    over = [item for item in items if item['severity'] == 'P0']
    under = [item for item in items if item['severity'] == 'P1']
    assert len(over) == 1 and 'Sketch2' in over[0]['subject'], over
    assert len(under) == 1 and 'Sketch1' in under[0]['subject'], under


@case
def case_sketch_real_fields():
    # 真实字段来自 sw_measure：constrained_status(int) + constrained_status_text
    facts = dict(CLEAN_FACTS)
    facts['sketches'] = [
        {'name': 'Sketch_over', 'segments': 8, 'fully_defined': False,
         'constrained_status': 3, 'constrained_status_text': 'over_defined'},
        {'name': 'Sketch_under', 'segments': 12, 'fully_defined': False,
         'constrained_status': 2, 'constrained_status_text': 'under_defined'},
        {'name': 'Sketch_broken', 'segments': 4, 'fully_defined': None,
         'constrained_status': 4, 'constrained_status_text': 'no_solution'},
        {'name': 'Sketch_ok', 'segments': 4, 'fully_defined': True,
         'constrained_status': 1, 'constrained_status_text': 'fully_defined'},
    ]
    result = audit(parts=CLEAN_PARTS, facts=facts, support_planes=[0.0],
                   rules={'cantilever': ['plate']})
    items = angles(result, 'sketch')
    by_subject = {item['subject']: item for item in items}
    assert by_subject['Sketch_over']['severity'] == 'P0', items
    assert by_subject['Sketch_broken']['severity'] == 'P0', items
    assert by_subject['Sketch_under']['severity'] == 'P1', items
    assert 'Sketch_ok' not in by_subject, items


@case
def case_shape():
    diff = {
        'rows': [{'cell': 'I4', 'name': 'funnel_len', 'part': 'funnel casing', 'field': 'dx',
                  'ref': [8.5, 9.0], 'model': 10.4, 'delta': 1.4, 'verdict': 'FAIL',
                  'hint': '把烟囱纵向收到 38.2~47.0'}],
        'profile_rows': [{'cell': 'K4', 'u_range': [50, 55], 'ref': {'vmax': 22.0}, 'model': {'vmax': 23.6},
                          'delta_vmax': 1.6, 'delta_vmin': 0.0, 'verdict': 'FAIL'}],
    }
    result = audit(diff=diff)
    items = angles(result, 'shape')
    assert len(items) == 2, items
    assert all(item['severity'] == 'P1' for item in items), items
    assert {item['cell'] for item in items} == {'I4', 'K4'}, items
    assert any('烟囱' in item['hint'] for item in items), items


@case
def case_mass():
    facts = {'bbox_mm': [100, 100, 100], 'volume_mm3': 5000.0, 'solid_count': 1}
    result = audit(facts=facts)
    items = angles(result, 'mass')
    assert items and items[0]['severity'] == 'P0', items
    empty = audit(facts={'solid_count': 0, 'bbox_mm': [10, 10, 10], 'volume_mm3': 0})
    datum = angles(empty, 'datum')
    assert any(item['severity'] == 'P0' and 'solid' in item['subject'] for item in datum), datum


@case
def case_mass_near_solid():
    # 带孔的板填充率 99.2% 属正常，不应误报；满包络 99.99% 才提示
    facts = {'bbox_mm': [100, 100, 100], 'volume_mm3': 999900.0, 'solid_count': 1}
    result = audit(facts=facts)
    items = angles(result, 'mass')
    assert items and items[0]['severity'] == 'P2', items


@case
def case_units_mm():
    # units=mm：parts / support_planes / model_bbox 必须一起换算，否则悬空全误判
    parts = [{'name': 'porthole', 'bbox': [0, 0, 9.70, 5, 3, 12.30]}]
    result = audit(parts=parts, units='mm', support_planes=[9300.0],
                   model_bbox=[0, 0, 0, 90000, 90000, 20000],
                   rules={'cantilever': ['porthole']})
    floats = angles(result, 'float')
    assert not floats, floats
    assert not angles(result, 'datum'), angles(result, 'datum')


@case
def case_units_mm_envelope():
    parts = [{'name': 'plate', 'bbox': [0, 0, 0, 120, 80, 10]}]
    result = audit(parts=parts, units='mm', support_planes=[0.0],
                   model_bbox=[0, 0, 0, 100, 100, 100],
                   rules={'cantilever': ['plate'], 'tolerances': {'bbox_slack': 0.005}})
    datum = angles(result, 'datum')
    assert any(item['severity'] == 'P1' and 'envelope' in item['subject'] for item in datum), datum


@case
def case_float_direction():
    # z0=5.0 夹在 0 与 9.3 之间：应报"悬空于 plane 0"，不是"低于 plane 9.3 -4.3"
    parts = [{'name': 'box', 'bbox': [0, 0, 5.0, 1, 1, 6.0]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3])
    floats = angles(result, 'float')
    assert len(floats) == 1, floats
    assert 'plane 0.000' in floats[0]['expect'], floats[0]
    assert abs(floats[0]['delta'] - 5.0) < 1e-6, floats[0]


@case
def case_float_embedded_into_support():
    # 球罩底面 24.50 坐进基座顶 24.60（嵌进 0.1 m）：贴合，不是悬空
    parts = [{'name': 'bridge roof red', 'bbox': [50.4, -7.35, 22.95, 65.4, 7.35, 23.6]},
             {'name': 'radome pedestal', 'bbox': [58.7, -1.0, 23.6, 60.7, 1.0, 24.6]},
             {'name': 'radome', 'bbox': [58.0, -1.7, 24.5, 61.4, 1.7, 27.9]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3, 13.75, 19.15, 22.95],
                   rules={'cantilever': ['radome']})
    assert not angles(result, 'float'), angles(result, 'float')


@case
def case_float_large_part_over_small_bracket():
    # 甲板室底 13.75 低于救生艇架顶 13.90：是架立在上面，不是甲板室插穿
    parts = [{'name': 'accommodation deck 1', 'bbox': [41.5, -6.4, 13.75, 67.0, 6.4, 16.55]},
             {'name': 'rescue boat cradle', 'bbox': [44.0, 3.0, 13.75, 45.0, 3.6, 13.90]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3, 13.75])
    assert not angles(result, 'float'), angles(result, 'float')


@case
def case_naming():
    parts = [{'name': 'Funnel Casing', 'bbox': [0, 0, 0, 1, 1, 1]}]
    result = audit(parts=parts, support_planes=[0.0], rules={'cantilever': ['funnel']})
    items = angles(result, 'naming')
    assert len(items) == 1, items
    assert items[0]['severity'] == 'P2'
    assert '大写' in items[0]['actual'], items[0]


@case
def case_rules_off():
    parts = [{'name': 'deck', 'bbox': [0, 0, 0, 50, 10, 9.3]},
             {'name': 'box on deck', 'bbox': [5, 0, 9.7, 10, 5, 11.0]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3], rules={'angles': {'float': False}})
    assert not angles(result, 'float'), angles(result, 'float')
    assert result['rules_used']['angles'].get('float') is False


@case
def case_idempotent():
    parts = [{'name': 'deck', 'bbox': [0, 0, 0, 50, 10, 9.3]},
             {'name': 'box on deck', 'bbox': [5, 0, 9.7, 10, 5, 11.0]},
             {'name': 'Block B', 'bbox': [5, 0, 9.7, 10, 5, 11.0]}]
    first = audit(parts=parts, support_planes=[0.0, 9.3])
    second = audit(parts=parts, support_planes=[0.0, 9.3])
    assert [item['id'] for item in first['findings']] == [item['id'] for item in second['findings']]
    assert first['counts'] == second['counts']


@case
def case_report_written():
    parts = [{'name': 'deck', 'bbox': [0, 0, 0, 50, 10, 9.3]},
             {'name': 'box on deck', 'bbox': [5, 0, 9.7, 10, 5, 11.0]}]
    result = audit(parts=parts, support_planes=[0.0, 9.3], write_report=True,
                   out_dir=os.path.join(WORK, 'audit'))
    assert result['report_path'] and os.path.isfile(result['report_path']), result['report_path']
    with open(result['report_path'], 'r', encoding='utf-8') as handle:
        text = handle.read()
    assert '对抗审查报告' in text and 'P0' in text and '改法' in text


@case
def case_stl_crosscheck_facts_only():
    # 没有 parts 时用 facts.bbox_mm 与 STL 交叉核对
    import struct
    path = os.path.join(WORK, 'facts.stl')
    with open(path, 'wb') as handle:
        handle.write(b' ' * 80)
        handle.write(struct.pack('<I', 1))
        handle.write(struct.pack('<fff', 0.0, 0.0, 1.0))
        for point in ((0, 0, 0), (100, 0, 0), (0, 100, 0)):
            handle.write(struct.pack('<fff', *point))
        handle.write(struct.pack('<H', 0))
    result = audit(facts={'bbox_mm': [10.0, 10.0, 10.0], 'solid_count': 1}, stl=path)
    items = angles(result, 'datum')
    assert any('stl' in item['subject'].lower() for item in items), items


@case
def case_report_name_unique():
    # 同一秒内两次审查不能覆盖同一份报告（CI 上 diff 就栽在这）
    first = audit(parts=CLEAN_PARTS, facts=CLEAN_FACTS, support_planes=[0.0],
                  rules={'cantilever': ['plate']}, write_report=True,
                  out_dir=os.path.join(WORK, 'audit_unique'))
    second = audit(parts=CLEAN_PARTS, facts=CLEAN_FACTS, support_planes=[0.0],
                   rules={'cantilever': ['plate']}, write_report=True,
                   out_dir=os.path.join(WORK, 'audit_unique'))
    assert first['report_path'] != second['report_path'], (first['report_path'], second['report_path'])


@case
def case_stl_crosscheck():
    import struct
    path = os.path.join(WORK, 'block.stl')
    with open(path, 'wb') as handle:
        handle.write(b' ' * 80)
        handle.write(struct.pack('<I', 1))
        handle.write(struct.pack('<fff', 0.0, 0.0, 1.0))
        for point in ((0, 0, 0), (100, 0, 0), (0, 100, 0)):
            handle.write(struct.pack('<fff', *point))
        handle.write(struct.pack('<H', 0))
    parts = [{'name': 'plate', 'bbox': [0, 0, 0, 10, 10, 10]}]
    result = audit(parts=parts, stl=path, support_planes=[0.0], rules={'cantilever': ['plate']})
    items = angles(result, 'datum')
    assert any('stl' in item['subject'].lower() for item in items), items


def main():
    passed = 0
    failed = 0
    try:
        for function in CASES:
            try:
                function()
                print('PASS ' + function.__name__)
                passed += 1
            except Exception as error:  # noqa: BLE001 - 用例边界
                print('FAIL ' + function.__name__ + ' — ' + str(error))
                import traceback
                traceback.print_exc()
                failed += 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print('\nAUDIT CASES %d/%d PASS' % (passed, passed + failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())