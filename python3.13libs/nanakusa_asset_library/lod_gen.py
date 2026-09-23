"""Standalone hython worker: add a "LOD" variant set (LOD_1 = as authored, LOD_2.. = reduced).

Built for Houdini's own LOD tools, which expect what Create LOD LOP makes: one variant set on the
prim that is placed and measured, with variants whose names sort from the most to the least
detailed. So the set goes on the asset's top prim (element_gen.anchor - the prim a Reference or a
Stage Manager placement maps onto the placed prim), named LOD_1 .. LOD_N (at most 9, so the names
also sort by level):
- Auto Select LOD LOP (variant set "LOD") picks a level by camera distance for each placed prim;
- Stage Manager's Inspector lists "LOD" for a placed prim and writes the choice itself;
- Set Variant LOP can pick one explicitly.

LOD_1 changes nothing (the asset exactly as authored). LOD_k defines a reduced copy of every render
mesh as a sibling "<mesh>_LOD_k" and hides the original (visibility=invisible) - only inside that
variant, so the original data are never touched and removing the set restores the file exactly
(remove() uses element_gen.remove_variant_set, like Delete Element Switch). A mesh whose visibility
is already authored (the variant's opinion would lose to it) is refused instead of producing an
LOD that never shows.

Reduction (Houdini SOP verbs, no nodes): UVs, normals and other float primvars are carried per face
corner, so coincident seam points can be fused first - otherwise every UV island would be reduced
on its own and the shape would fall apart - then the mesh is triangulated and PolyReduce keeps a
percentage of the triangles, with attribute seams preserved. The result is split back into one value
per point (vertex interpolation, like the source meshes) only where corners differ.

Shares its file handling (plain USD vs .usdz, edit only the entry layer, write a new file) with
proxy_gen.py.
"""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # run directly by hython (see element_gen.py)
    from nanakusa_asset_library import proxy_gen, element_gen
else:
    from . import proxy_gen, element_gen

VARIANT_SET = 'LOD'
DEFAULT_LEVELS = 4
DEFAULT_KEEP = 50.0   # percent of the previous level's triangles kept at each step
MAX_LEVELS = 9        # LOD_1..LOD_9 still sort by level (Auto Select LOD walks the names in order)


def level_names(levels):
    return ['LOD_%d' % (i + 1) for i in range(levels)]


def _render_meshes(stage):
    from pxr import UsdGeom
    return [p for p in stage.Traverse()
            if p.IsA(UsdGeom.Mesh) and not p.IsInstanceProxy()
            and UsdGeom.Imageable(p).GetPurposeAttr().Get() != UsdGeom.Tokens.proxy
            and UsdGeom.Mesh(p).GetPointsAttr().Get()]


def _corner_data(mesh):
    """Per-mesh data to carry through the reduction: {name: (kind, interpolation-in, values, type,
    element size)} for normals and every primvar. Raises ValueError for data that cannot be
    carried faithfully (so the asset is skipped rather than given a broken LOD)."""
    from pxr import UsdGeom
    import numpy
    counts = mesh.GetFaceVertexCountsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    carried = {}

    def add(key, interpolation, values, type_name, target):
        if values is None or len(values) == 0:
            return
        if interpolation == UsdGeom.Tokens.constant:
            carried[key] = ('constant', values, type_name, target)
            return
        array = numpy.asarray(values)
        if array.dtype.kind != 'f':
            raise ValueError('unsupported %s primvar %s' % (interpolation, key))
        array = array.reshape(len(array), -1).astype(numpy.float64)
        if interpolation in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
            if len(array) != len(mesh.GetPointsAttr().Get()):
                raise ValueError('primvar %s does not match the points' % key)
            corner = array[numpy.asarray(indices)]
        elif interpolation == UsdGeom.Tokens.faceVarying:
            if len(array) != len(indices):
                raise ValueError('primvar %s does not match the face corners' % key)
            corner = array
        elif interpolation == UsdGeom.Tokens.uniform:
            if len(array) != len(counts):
                raise ValueError('primvar %s does not match the faces' % key)
            carried[key] = ('uniform', array, type_name, target)
            return
        else:
            raise ValueError('unsupported interpolation %s on %s' % (interpolation, key))
        carried[key] = ('corner', corner, type_name, target)

    normals = mesh.GetNormalsAttr()
    if normals.HasAuthoredValue():
        add('normals', mesh.GetNormalsInterpolation(), normals.Get(), normals.GetTypeName(), ('normals', None))
    for primvar in UsdGeom.PrimvarsAPI(mesh).GetPrimvarsWithValues():
        name = primvar.GetPrimvarName()
        if primvar.GetElementSize() > 1 and primvar.GetInterpolation() != UsdGeom.Tokens.constant:
            raise ValueError('unsupported element size on primvar %s' % name)
        add('primvars:' + name, primvar.GetInterpolation(), primvar.ComputeFlattened(), primvar.GetTypeName(),
            ('primvar', (name, primvar.GetElementSize())))
    return carried


def _prepare(points, counts, indices, carried):
    """Fused, triangulated Houdini geometry with the carried data as vertex / primitive attributes."""
    import hou
    import numpy
    geo = hou.Geometry()
    geo.createPoints([tuple(p) for p in points])
    polys, offset = [], 0
    for count in counts:
        polys.append(tuple(indices[offset:offset + count]))
        offset += count
    geo.createPolygons(polys)
    names = {}
    for number, (key, (kind, values, _type, _target)) in enumerate(sorted(carried.items())):
        if kind == 'constant':
            continue
        attrib = 'nal_%d' % number   # plain names: never P / N, which SOPs treat specially
        names[key] = attrib
        size = values.shape[1]
        owner = hou.attribType.Vertex if kind == 'corner' else hou.attribType.Prim
        geo.addAttrib(owner, attrib, (0.0,) * size)
        flat = numpy.ascontiguousarray(values, dtype=numpy.float64).ravel().tolist()
        if kind == 'corner':
            geo.setVertexFloatAttribValues(attrib, flat)
        else:
            geo.setPrimFloatAttribValues(attrib, flat)
    lo, hi = numpy.min(points, axis=0), numpy.max(points, axis=0)
    diagonal = float(numpy.linalg.norm(numpy.asarray(hi) - numpy.asarray(lo)))
    fused = _verb('fuse::2.0', geo, tol3d=max(diagonal * 1e-6, 1e-9), recomputenml=0, deldegen=1)
    return _verb('divide', fused, convex=1, usemaxsides=1, numsides=3), names


def _verb(name, geo, **parms):
    import hou
    verb = hou.sopNodeTypeCategory().nodeVerb(name)
    verb.setParms(parms)
    out = hou.Geometry()
    verb.execute(out, [geo])
    return out


def _reduce(base, names, keep_percent, right_handed=True):
    """(points, counts, indices, {key: values}) of `base` with `keep_percent` of its triangles.

    Normals are recomputed on the reduced surface (angle-weighted, split at 60 degrees) instead of
    interpolated: a few large triangles carrying normals from collapsed detail shade in dark
    smudges (seen on a flat-sided concrete beam at 6 % of its triangles)."""
    import numpy
    reduced = _verb('polyreduce::2.0', base, target=0, percentage=float(keep_percent))
    if 'normals' in names:
        # Faces keep USD's winding; Houdini takes the other winding as the front of a
        # rightHanded (default) mesh, so its normals are reversed back.
        reduced = _verb('normal', reduced, type=1, method=1, cuspangle=60.0, normalize=1, reverse=int(right_handed))
        names = dict(names, normals='N')
    points = numpy.asarray(reduced.pointFloatAttribValues('P'), dtype=numpy.float32).reshape(-1, 3)
    counts, indices = [], []
    for prim in reduced.prims():
        vertices = prim.vertices()
        counts.append(len(vertices))
        indices.extend(v.point().number() for v in vertices)
    data, corner_keys, corner_columns = {}, [], []
    for key, attrib in names.items():
        found = reduced.findVertexAttrib(attrib)
        if found is not None:
            corner_keys.append((key, found.size()))
            corner_columns.append(numpy.asarray(reduced.vertexFloatAttribValues(attrib)).reshape(-1, found.size()))
        else:
            found = reduced.findPrimAttrib(attrib)
            data[key] = ('uniform', numpy.asarray(reduced.primFloatAttribValues(attrib)).reshape(-1, found.size()))
    if corner_keys:
        # Back to one value per point, as the source meshes store them: a point is split only
        # where its corners really differ (UV seams, hard normals). Corner-wise (faceVarying) data
        # would take about three times the space.
        corners = numpy.column_stack([numpy.asarray(indices, dtype=numpy.float64)] + corner_columns)
        unique, inverse = numpy.unique(corners, axis=0, return_inverse=True)
        points = points[unique[:, 0].astype(numpy.int64)]
        indices = inverse.ravel().astype(numpy.int64).tolist()
        column = 1
        for key, size in corner_keys:
            data[key] = ('point', unique[:, column:column + size])
            column += size
    return points, counts, indices, data


def _vt_array(type_name, values):
    """A Vt array of `type_name` from an (n, k) float array."""
    from pxr import Sdf
    cls = type_name.type.pythonClass
    if values.shape[1] == 1:
        values = values[:, 0]
    return cls.FromNumpy(values.astype('float32' if 'double' not in str(type_name) else 'float64'))


def _define_lod_mesh(stage, source, name, points, counts, indices, data, carried):
    from pxr import UsdGeom, UsdShade, Gf, Vt
    import numpy
    mesh = UsdGeom.Mesh.Define(stage, source.GetPath().GetParentPath().AppendChild(name))
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))
    if len(points):
        mesh.CreateExtentAttr([Gf.Vec3f(*map(float, points.min(axis=0))), Gf.Vec3f(*map(float, points.max(axis=0)))])
    src = UsdGeom.Mesh(source)
    for attr_name in ('subdivisionScheme', 'orientation', 'doubleSided', 'purpose'):
        attr = source.GetAttribute(attr_name)
        if attr and attr.HasAuthoredValue():
            mesh.GetPrim().CreateAttribute(attr_name, attr.GetTypeName()).Set(attr.Get())
    order = src.GetXformOpOrderAttr()
    if order.HasAuthoredValue():
        for op in src.GetOrderedXformOps():
            mesh.GetPrim().CreateAttribute(op.GetName(), op.GetAttr().GetTypeName()).Set(op.Get())
        mesh.GetXformOpOrderAttr().Set(order.Get())
    for rel in source.GetRelationships():
        if rel.GetName().startswith('material:binding') and rel.GetTargets():
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
            mesh.GetPrim().CreateRelationship(rel.GetName()).SetTargets(rel.GetTargets())
    primvars = UsdGeom.PrimvarsAPI(mesh)
    for key, (kind, values, type_name, (what, extra)) in carried.items():
        if kind == 'constant':
            out, interpolation = values, UsdGeom.Tokens.constant
        else:
            reduced_kind, reduced = data[key]
            out = _vt_array(type_name, reduced)
            interpolation = UsdGeom.Tokens.vertex if reduced_kind == 'point' else UsdGeom.Tokens.uniform
        if what == 'normals':
            mesh.CreateNormalsAttr(out)
            mesh.SetNormalsInterpolation(interpolation)
        else:
            primvar = primvars.CreatePrimvar(extra[0], type_name, interpolation, extra[1] if extra[1] > 1 else -1)
            primvar.Set(out)
    return mesh


def _add_lods(stage, levels, keep):
    from pxr import UsdGeom, Tf
    levels = int(levels)
    if not 2 <= levels <= MAX_LEVELS:
        raise ValueError('levels must be 2..%d' % MAX_LEVELS)
    if not 1.0 <= float(keep) < 100.0:
        raise ValueError('keep must be at least 1 and below 100 percent')
    if any(p.GetVariantSets().HasVariantSet(VARIANT_SET) for p in stage.Traverse()):
        return {'skipped': 'already has LODs (delete them first to change the levels)'}
    meshes = _render_meshes(stage)
    if not meshes:
        return {'skipped': 'no mesh geometry'}
    top = element_gen.anchor(stage, meshes[0])
    if any(not m.GetPath().HasPrefix(top.GetPath()) for m in meshes):
        return {'skipped': 'meshes are spread over several top-level prims (no single prim to place)'}
    for m in meshes:
        if m.GetAttribute('visibility').HasAuthoredValue():
            return {'skipped': 'visibility is already authored on %s (an LOD could not hide it)' % m.GetPath()}

    # Reduce everything first: nothing is authored unless every mesh can be done.
    prepared = []
    for m in meshes:
        mesh = UsdGeom.Mesh(m)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        try:
            carried = _corner_data(mesh)
        except ValueError as exc:
            return {'skipped': '%s: %s' % (m.GetPath(), exc)}
        base, names = _prepare(points, counts, indices, carried)
        right_handed = mesh.GetOrientationAttr().Get() != UsdGeom.Tokens.leftHanded
        levels_data = [_reduce(base, names, 100.0 * (keep / 100.0) ** level, right_handed) for level in range(1, levels)]
        prepared.append((m, carried, levels_data, proxy_gen._triangle_count(counts)))

    variant_set = top.GetVariantSets().AddVariantSet(VARIANT_SET)
    names = level_names(levels)
    triangles = [sum(p[3] for p in prepared)]
    for level, name in enumerate(names):
        variant_set.AddVariant(name)
        variant_set.SetVariantSelection(name)
        if level == 0:
            continue   # LOD_1: the asset as authored
        total = 0
        with variant_set.GetVariantEditContext():
            for m, carried, levels_data, _ in prepared:
                points, counts, indices, data = levels_data[level - 1]
                lod_name = Tf.MakeValidIdentifier('%s_%s' % (m.GetName(), name))
                _define_lod_mesh(stage, m, lod_name, points, counts, indices, data, carried)
                UsdGeom.Imageable(m).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                total += proxy_gen._triangle_count(counts)
        triangles.append(total)
    variant_set.SetVariantSelection(names[0])
    return {'lods': {'prim': str(top.GetPath()), 'variants': names, 'triangles': triangles, 'meshes': len(prepared)}}


def _remove_lods(stage):
    removed = element_gen.remove_variant_set(stage, VARIANT_SET)
    if not removed:
        return {'skipped': 'no LODs found in this file'}
    return {'removed_lods': removed}


def generate(source, destination, levels=DEFAULT_LEVELS, keep=DEFAULT_KEEP):
    add_fn = lambda stage: _add_lods(stage, levels, keep)
    if Path(source).suffix.lower() == '.usdz':
        return proxy_gen.generate_usdz(source, destination, add_fn)
    return proxy_gen.generate_plain(source, destination, add_fn)


def remove(source, destination):
    if Path(source).suffix.lower() == '.usdz':
        return proxy_gen.generate_usdz(source, destination, _remove_lods)
    return proxy_gen.generate_plain(source, destination, _remove_lods)


if __name__ == '__main__':
    import json
    mode = sys.argv[4] if len(sys.argv) > 4 else 'add'
    if mode == 'remove':
        result = remove(sys.argv[1], sys.argv[2])
    else:
        levels = int(sys.argv[5]) if len(sys.argv) > 5 else DEFAULT_LEVELS
        keep = float(sys.argv[6]) if len(sys.argv) > 6 else DEFAULT_KEEP
        result = generate(sys.argv[1], sys.argv[2], levels, keep)
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')
