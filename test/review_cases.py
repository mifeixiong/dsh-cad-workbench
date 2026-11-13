# -*- coding: utf-8 -*-
"""cad_review.py 的离线用例：现场生成 DXF/STL/STEP，不需要 SolidWorks。"""
import os
import struct
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import cad_review  # noqa: E402


def make_good_dxf(path):
    import ezdxf
    doc = ezdxf.new('R2010', setup=True)
    doc.header['$INSUNITS'] = 4
    for name in ('OUTLINE', 'DIM', 'TEXT'):
        doc.layers.add(name)
    doc.layers.add('CENTER', linetype='CENTER')
    doc.layers.add('HIDDEN', linetype='HIDDEN')
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={'layer': 'OUTLINE'})
    msp.add_line((100, 0), (100, 60), dxfattribs={'layer': 'OUTLINE'})
    msp.add_text('TEST', height=3.5, dxfattribs={'layer': 'TEXT'}).set_placement((10, 10))
    msp.add_mtext('NOTE', dxfattribs={'layer': 'TEXT', 'char_height': 3.5}).set_location((10, 20))
    msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(100, 0),
                       dxfattribs={'layer': 'DIM'}).render()
    msp.add_linear_dim(base=(110, 0), p1=(100, 0), p2=(100, 60),
                       dxfattribs={'layer': 'DIM'}).render()
    msp.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(100, 60),
                       dxfattribs={'layer': 'DIM'}).render()
    doc.saveas(path)


def make_small_mtext_dxf(path):
    import ezdxf
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    msp = doc.modelspace()
    msp.add_mtext('SMALL', dxfattribs={'layer': '0', 'char_height': 1.0}).set_location((0, 0))
    doc.saveas(path)


def make_bad_dxf(path):
    import ezdxf
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    msp.add_line((0, 0), (0, 0))
    msp.add_line((0, 0), (10, 0))
    msp.add_line((10, 0), (0, 0))
    doc.saveas(path)


def make_stl(path, degenerate=False):
    tri = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0)]
    faces = [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)]
    if degenerate:
        faces.append((0, 0, 0))
    with open(path, 'wb') as handle:
        handle.write(b'\0' * 80)
        handle.write(struct.pack('<I', len(faces)))
        for face in faces:
            handle.write(struct.pack('<3f', 0.0, 0.0, 0.0))
            for index in face:
                handle.write(struct.pack('<3f', *tri[index]))
            handle.write(struct.pack('<H', 0))


def make_step(path):
    lines = [
        'ISO-10303-21;',
        "HEADER;",
        "FILE_DESCRIPTION((''), '2;1');",
        "FILE_NAME('demo.step','2026-01-01T00:00:00',(''),(''),'','','');",
        "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));",
        'ENDSEC;',
        'DATA;',
        '#1=SI_UNIT(.MILLI.,.METRE.);',
        "#2=CARTESIAN_POINT('',(0.,0.,0.));",
        "#3=CARTESIAN_POINT('',(120.,80.,10.));",
        'ENDSEC;',
        'END-ISO-10303-21;',
    ]
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines))


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix='cadw-review-')
    rules = cad_review._load_rules(None)
    try:
        good = os.path.join(tmp, 'good.dxf')
        make_good_dxf(good)
        result = cad_review.review_dxf(good, rules)
        if result['verdict'] != 'PASS':
            failures.append('good.dxf 期望 PASS，实际 %s：%s' % (
                result['verdict'], [item for item in result['checks'] if item['status'] != 'PASS']))
        bad = os.path.join(tmp, 'bad.dxf')
        make_bad_dxf(bad)
        result = cad_review.review_dxf(bad, rules)
        if result['verdict'] not in ('WARN', 'FAIL'):
            failures.append('bad.dxf 期望 WARN/FAIL，实际 %s' % result['verdict'])
        ids = {item['id']: item['status'] for item in result['checks']}
        if ids.get('zero_length') != 'FAIL':
            failures.append('bad.dxf 的零长线检查应为 FAIL，实际 %s' % ids.get('zero_length'))
        mtext = os.path.join(tmp, 'small-mtext.dxf')
        make_small_mtext_dxf(mtext)
        result = cad_review.review_dxf(mtext, rules)
        ids = {item['id']: item['status'] for item in result['checks']}
        if ids.get('text_height') != 'WARN':
            failures.append('MTEXT char_height=1.0 应触发 text_height WARN，实际 %s' % ids.get('text_height'))
    except ImportError:
        print('review_cases: 跳过 DXF（未安装 ezdxf）')

    stl = os.path.join(tmp, 'good.stl')
    make_stl(stl)
    result = cad_review.review_stl(stl)
    if result['verdict'] != 'PASS':
        failures.append('good.stl 期望 PASS，实际 %s：%s' % (
            result['verdict'], [item for item in result['checks'] if item['status'] != 'PASS']))
    bad_stl = os.path.join(tmp, 'bad.stl')
    make_stl(bad_stl, degenerate=True)
    result = cad_review.review_stl(bad_stl)
    if result['verdict'] != 'FAIL':
        failures.append('bad.stl 期望 FAIL，实际 %s' % result['verdict'])

    step = os.path.join(tmp, 'demo.step')
    make_step(step)
    result = cad_review.review_step(step)
    if result['verdict'] != 'PASS':
        failures.append('demo.step 期望 PASS，实际 %s：%s' % (
            result['verdict'], [item for item in result['checks'] if item['status'] != 'PASS']))
    if result['stats'].get('unit') != 'mm':
        failures.append('demo.step 单位应为 mm，实际 %s' % result['stats'].get('unit'))
    if result['stats'].get('bbox') != [120.0, 80.0, 10.0]:
        failures.append('demo.step 包围盒异常：%s' % result['stats'].get('bbox'))

    unsupported = os.path.join(tmp, 'x.obj')
    open(unsupported, 'w').write('v 0 0 0')
    try:
        payload = cad_review.main({'paths': [unsupported], 'rules': None})
        if payload['verdict'] != 'FAIL':
            failures.append('未知扩展名期望 FAIL，实际 %s' % payload['verdict'])
    except Exception as error:  # noqa: BLE001
        failures.append('未知扩展名审查抛异常：%s' % error)

    print('review_cases: 完成，失败 %d' % len(failures))
    for item in failures:
        print('  FAIL ' + item)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
