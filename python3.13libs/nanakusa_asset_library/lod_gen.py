"""Standalone hython worker: add a "lod" variant set (LOD0..LOD{levels-1}) to each render mesh.

Shares its file-handling (plain USD vs .usdz extract/repackage, edit only the asset's own entry
layer, write to a new file and never Save() the source) with proxy_gen.py; see generate_plain and
generate_usdz there.
"""
from pathlib import Path
import sys

if __package__ in (None, ''):
    # Run directly by hython as a worker subprocess (see _MeshGenerateJob in ui.py), this file has
    # no parent package, so the relative import below would fail with "attempted relative import
    # with no known parent package". Make python3.13libs importable and import proxy_gen from
    # there instead; this branch is a no-op when lod_gen is imported normally as part of the
    # nanakusa_asset_library package (tests, ui.py).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanakusa_asset_library import proxy_gen
else:
    from . import proxy_gen

DEFAULT_LEVELS = 4
DEFAULT_REDUCTION = 50.0   # percent of the previous level's triangle count kept at each step


def _add_lods(stage, levels, reduction):
    from pxr import UsdGeom

    if any(prim.GetVariantSets().HasVariantSet('lod') for prim in stage.Traverse()):
        return {'skipped': 'already has LODs'}

    targets = [p for p in stage.Traverse()
               if p.IsA(UsdGeom.Mesh) and not p.IsInstanceProxy()
               and UsdGeom.Mesh(p).GetPointsAttr().Get()]
    if not targets:
        return {'skipped': 'no mesh geometry'}

    processed = []
    for prim in targets:
        mesh = UsdGeom.Mesh(prim)
        base_points = mesh.GetPointsAttr().Get()
        base_counts = mesh.GetFaceVertexCountsAttr().Get()
        base_indices = mesh.GetFaceVertexIndicesAttr().Get()
        original = proxy_gen._triangle_count(base_counts)
        # A local opinion on this prim would always win over a variant's, no matter which is
        # selected (variants are weaker than local opinions but stronger than a reference), so
        # the base geometry - wherever it actually comes from - has to stop being the strongest
        # opinion before the variants can be switched between.
        mesh.GetPointsAttr().Clear()
        mesh.GetFaceVertexCountsAttr().Clear()
        mesh.GetFaceVertexIndicesAttr().Clear()

        variant_set = prim.GetVariantSets().AddVariantSet('lod')
        triangles = []
        for level in range(levels):
            name = f'LOD{level}'
            variant_set.AddVariant(name)
            variant_set.SetVariantSelection(name)
            with variant_set.GetVariantEditContext():
                if level == 0:
                    points, counts, indices = base_points, base_counts, base_indices
                else:
                    target = max(round(original * (reduction / 100.0) ** level), 4)
                    points, counts, indices, _ = proxy_gen._decimate(base_points, base_counts, base_indices, target)
                mesh.CreatePointsAttr(points)
                mesh.CreateFaceVertexCountsAttr(counts)
                mesh.CreateFaceVertexIndicesAttr(indices)
            triangles.append(proxy_gen._triangle_count(counts))
        variant_set.SetVariantSelection('LOD0')
        processed.append({'prim': str(prim.GetPath()), 'triangles': triangles})
    return {'lods': processed}


def generate(source, destination, levels=DEFAULT_LEVELS, reduction=DEFAULT_REDUCTION):
    add_fn = lambda stage: _add_lods(stage, levels, reduction)
    if Path(source).suffix.lower() == '.usdz':
        return proxy_gen.generate_usdz(source, destination, add_fn)
    return proxy_gen.generate_plain(source, destination, add_fn)


if __name__ == '__main__':
    import json
    levels = int(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_LEVELS
    reduction = float(sys.argv[5]) if len(sys.argv) > 5 else DEFAULT_REDUCTION
    result = generate(sys.argv[1], sys.argv[2], levels, reduction)
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')
