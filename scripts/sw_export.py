# -*- coding: utf-8 -*-
"""sw_export.py —— 文档导出（step/iges/stl/3mf/pdf/png），逐格式复核。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402


def main(spec):
    workspace = common.workspace_of(spec)
    out_dir = os.path.abspath(spec.get('out_dir') or os.path.join(workspace, 'cad-output'))
    common.ensure_dir(out_dir)
    formats = [str(item).lower() for item in (spec.get('formats') or ['step'])]
    overwrite = bool(spec.get('overwrite'))
    with common.sw_lock('sw_export', workspace=workspace):
        app = common.attach(launch=False)
        model, identity = common.find_document(app, spec.get('doc'))
        common.verify_identity(model, identity['doc_id'])
        common.activate(app, model)
        basename = spec.get('basename') or os.path.splitext(identity.get('title') or 'model')[0]
        basename = str(basename)
        artifacts = []
        failures = []
        for fmt in formats:
            extension = common.EXPORT_MAP.get(fmt)
            if extension is None:
                failures.append({'format': fmt, 'code': 'UNSUPPORTED',
                                 'message': '不支持的格式（可用 %s）' % ', '.join(sorted(common.EXPORT_MAP))})
                continue
            target = os.path.join(out_dir, basename + extension)
            if os.path.exists(target) and not overwrite:
                failures.append({'format': fmt, 'code': 'EXPORT_FAILED',
                                 'message': '目标已存在，拒绝覆盖：%s' % target})
                continue
            try:
                if fmt == 'png':
                    bmp = os.path.join(out_dir, basename + '.bmp')
                    ok = model.SaveBMP(bmp, 1600, 1000)
                    if not ok or not os.path.isfile(bmp):
                        raise common.CadError('EXPORT_FAILED', 'SaveBMP 失败。')
                    common.bmp_to_png(bmp, target)
                    os.remove(bmp)
                    saved = {'path': target, 'bytes': os.path.getsize(target)}
                else:
                    saved = common.export_document(model, target, overwrite=overwrite)
                artifacts.append({'format': fmt, 'path': saved['path'], 'bytes': saved['bytes'],
                                  'sha256': common.sha256_file(saved['path'])})
            except common.CadError as error:
                failures.append({'format': fmt, 'code': error.code, 'message': error.message})
            except Exception as error:  # noqa: BLE001
                failures.append({'format': fmt, 'code': 'EXPORT_FAILED', 'message': str(error)})
        return {
            'ok': True,
            'doc': identity,
            'artifacts': artifacts,
            'failures': failures,
            'warnings': [('%s：%s' % (item['format'], item['message'])) for item in failures],
        }


if __name__ == '__main__':
    common.run(main)
