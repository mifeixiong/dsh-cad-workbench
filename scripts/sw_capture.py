# -*- coding: utf-8 -*-
"""sw_capture.py —— 视图截图 + 像素自检 + 2×2 拼图。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_common as common  # noqa: E402

VIEW_ALIASES = {
    'iso': 'iso', 'isometric': 'iso', '等轴测': 'iso',
    'front': 'front', '前视': 'front',
    'back': 'back', '后视': 'back',
    'left': 'left', '左视': 'left',
    'right': 'right', '右视': 'right',
    'top': 'top', '俯视': 'top', '上视': 'top',
    'bottom': 'bottom', '仰视': 'bottom',
}


def main(spec):
    workspace = common.workspace_of(spec)
    out_dir = os.path.abspath(spec.get('out_dir') or os.path.join(workspace, 'cad-output', 'captures'))
    common.ensure_dir(out_dir)
    basename = str(spec.get('basename') or 'capture')
    width = int(spec.get('width') or 1600)
    height = int(spec.get('height') or 1000)
    requested = [VIEW_ALIASES.get(str(item).lower(), str(item)) for item in (spec.get('views') or ['iso'])]
    with common.sw_lock('sw_capture', workspace=workspace):
        app = common.attach(launch=False)
        model, identity = common.find_document(app, spec.get('doc'))
        common.verify_identity(model, identity['doc_id'])
        common.activate(app, model)
        images = []
        warnings = []
        for view in requested:
            if view not in common.VIEWS:
                warnings.append('未知视图 "%s"，跳过（可用 %s）' % (view, ', '.join(common.VIEWS)))
                continue
            name, view_id = common.VIEWS[view]
            try:
                model.ShowNamedView2(name, view_id)
                model.ViewZoomtofit2()
                try:
                    model.ClearSelection2(True)
                except Exception:
                    pass
            except Exception as error:  # noqa: BLE001
                warnings.append('切视图 %s 失败：%s' % (view, error))
                continue
            bmp_path = os.path.join(out_dir, '%s-%s.bmp' % (basename, view))
            png_path = os.path.join(out_dir, '%s-%s.png' % (basename, view))
            ok = False
            try:
                ok = bool(model.SaveBMP(bmp_path, width, height))
            except Exception as error:  # noqa: BLE001
                warnings.append('SaveBMP %s 失败：%s' % (view, error))
                ok = False
            if not ok or not os.path.isfile(bmp_path):
                warnings.append('视图 %s 没有生成位图。' % view)
                continue
            try:
                common.bmp_to_png(bmp_path, png_path)
            finally:
                try:
                    os.remove(bmp_path)
                except Exception:
                    pass
            stats = common.image_stats(png_path)
            images.append({'view': view, 'path': png_path, **stats})
            if stats.get('blank'):
                warnings.append('视图 %s 疑似空白/全黑（stddev=%s，unique_colors=%s）。' % (
                    view, stats.get('stddev'), stats.get('unique_colors')))
        if not images:
            raise common.CadError('EXPORT_FAILED', '没有生成任何视图截图。', {'warnings': warnings})
        contact = None
        if spec.get('contact_sheet') and len(images) > 1:
            contact_path = os.path.join(out_dir, '%s-contact.png' % basename)
            common.make_contact_sheet(images, contact_path, title=identity.get('title') or '')
            contact = {'path': contact_path, **common.image_stats(contact_path)}
            if contact.get('blank'):
                warnings.append('拼图疑似空白。')
        return {
            'ok': True,
            'doc': identity,
            'images': images,
            'contact_sheet': contact,
            'warnings': warnings,
            'snapshot_stats': {'count': len(images), 'blank': sum(1 for item in images if item.get('blank'))},
        }


if __name__ == '__main__':
    common.run(main)
