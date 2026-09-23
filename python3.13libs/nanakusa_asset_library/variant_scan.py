"""Keep the automatic "lod" / "variant" tags in line with the variant sets USD assets really have.

Run by the panel (VariantTagJob) after a scan and when it opens, with Houdini's plain Python like
scan_worker.py (see asset_info.plain_setup). Only USD files that are new or changed since their
last check are opened (Library.variant_check_rows / the vstamp column), each without payloads and
masked to its top prim - the prim a placement maps onto the placed prim, where Auto Select LOD,
Stage Manager and Set Variant look. Its variant set names set the tags (core.sync_auto_tags):
    variant_scan.py <data dir>
Prints NAL_TAGS:{"checked": n, "changed": n, "errors": [...]}.
"""
from pathlib import Path
import json
import sys

BATCH = 50


def top_variant_sets(path):
    """Variant set names on the file's top prim (default prim, else its first root prim)."""
    from pxr import Sdf, Usd
    layer = Sdf.Layer.FindOrOpen(str(path))
    if layer is None:
        raise ValueError('could not open')
    name = layer.defaultPrim or next((p.name for p in layer.rootPrims), '')
    if not name:
        return []
    mask = Usd.StagePopulationMask([Sdf.Path.absoluteRootPath.AppendChild(name)])
    stage = Usd.Stage.OpenMasked(layer, mask, Usd.Stage.LoadNone)
    prim = stage.GetPrimAtPath('/' + name)
    return list(prim.GetVariantSets().GetNames()) if prim else []


def main(data_dir):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanakusa_asset_library.core import Library
    from nanakusa_asset_library.asset_info import plain_setup
    result = {'checked': 0, 'changed': 0, 'errors': []}
    try:
        plain_setup()
        library = Library(data_dir)
        pending = []
        for row in library.variant_check_rows():
            path = Path(row['root_path']) / row['relpath']
            try:
                names = top_variant_sets(path)
            except Exception as exc:
                names = None
                if len(result['errors']) < 5:
                    result['errors'].append('%s: %s' % (row['relpath'], exc))
            pending.append((row['id'], names, Library.file_stamp(row)))
            result['checked'] += 1
            if len(pending) >= BATCH:
                result['changed'] += library.save_variant_tags(pending); pending = []
        if pending:
            result['changed'] += library.save_variant_tags(pending)
    except Exception as exc:
        result['errors'].append(str(exc))
    print('NAL_TAGS:' + json.dumps(result))


if __name__ == '__main__':
    main(sys.argv[1])
