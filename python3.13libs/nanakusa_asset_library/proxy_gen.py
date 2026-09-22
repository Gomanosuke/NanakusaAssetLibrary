"""Standalone hython worker: add a decimated purpose=proxy sibling to each render mesh of a USD file.

Edits are made directly on the asset's own root layer (Usd.Stage.Open on the file path, or for a
.usdz package, the first entry of the archive - the package's own root layer per the usdz spec) so
any geometry that comes from a referenced or payloaded file is left untouched; only overrides and
new proxy prims are authored into this file. The result is written to a new file (Export() for a
plain layer, UsdUtils.CreateNewUsdzPackage() to repackage a .usdz), never by Save()-ing the source
in place, so the source is never touched here - the caller (ProxyJob in ui.py) backs the source up
and swaps it in only after this succeeds.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import zipfile

# A proxy exists to be cheap to draw, not accurate: one flat target keeps every asset's proxy
# equally light regardless of how heavy the source mesh is.
TARGET_TRIANGLES = 300


def _triangle_count(face_vertex_counts):
    return sum(max(c - 2, 0) for c in face_vertex_counts)


def _sibling_name(parent, base):
    from pxr import Tf
    name = Tf.MakeValidIdentifier(base + '_proxy')
    while parent.GetChild(name):
        name += '_'
    return name


def _decimate(points, face_vertex_counts, face_vertex_indices, target):
    """Reduce a polygon mesh to about `target` triangles using Houdini's polyreduce, off-screen."""
    import hou
    scratch = hou.node('/obj').createNode('geo', 'nanakusa_proxy_scratch')
    try:
        source = scratch.createNode('file')
        handle, obj_name = tempfile.mkstemp(suffix='.obj', prefix='nanakusa_proxy_')
        os.close(handle)
        obj_path = Path(obj_name)
        with obj_path.open('w') as stream:
            for x, y, z in points:
                stream.write('v %.9g %.9g %.9g\n' % (x, y, z))
            offset = 0
            for count in face_vertex_counts:
                idx = face_vertex_indices[offset:offset + count]
                stream.write('f ' + ' '.join(str(i + 1) for i in idx) + '\n')
                offset += count
        try:
            source.parm('file').set(str(obj_path))
            reduce_node = scratch.createNode('polyreduce::2.0')
            reduce_node.setInput(0, source)
            reduce_node.parm('target').set('poly_count')
            reduce_node.parm('finalcount').set(max(int(target), 4))
            reduce_node.cook(force=True)
            if reduce_node.errors():
                raise RuntimeError('; '.join(reduce_node.errors()))
            geo = reduce_node.geometry()
            out_points = [tuple(p.position()) for p in geo.points()]
            out_counts, out_indices = [], []
            for prim in geo.prims():
                verts = prim.vertices()
                out_counts.append(len(verts))
                out_indices.extend(v.point().number() for v in verts)
            return out_points, out_counts, out_indices
        finally:
            obj_path.unlink(missing_ok=True)
    finally:
        scratch.destroy()


def _add_proxies(stage, target_triangles):
    """Author proxy siblings directly onto an already-open, editable stage.

    Returns {'skipped': reason} or {'proxied': [...prim paths...]} - the same shape `generate()`
    returns, so both the plain-file and the .usdz path (which edits an extracted copy of the
    package's root layer) can share this.
    """
    from pxr import UsdGeom

    if any(UsdGeom.Imageable(p).GetPurposeAttr().HasAuthoredValue()
           and UsdGeom.Imageable(p).GetPurposeAttr().Get() == UsdGeom.Tokens.proxy
           for p in stage.Traverse()):
        return {'skipped': 'already has a proxy'}

    targets = [p for p in stage.Traverse()
               if p.IsA(UsdGeom.Mesh) and not p.IsInstanceProxy()
               and UsdGeom.Mesh(p).GetPointsAttr().Get()]
    if not targets:
        return {'skipped': 'no mesh geometry'}

    proxied = []
    for prim in targets:
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if _triangle_count(counts) > target_triangles:
            points, counts, indices = _decimate(points, counts, indices, target_triangles)
        parent = prim.GetParent()
        proxy_path = parent.GetPath().AppendChild(_sibling_name(parent, prim.GetName()))
        proxy = UsdGeom.Mesh.Define(stage, proxy_path)
        proxy.CreatePointsAttr(points)
        proxy.CreateFaceVertexCountsAttr(counts)
        proxy.CreateFaceVertexIndicesAttr(indices)
        UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
        UsdGeom.Imageable(prim).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
        proxied.append(str(prim.GetPath()))
    return {'proxied': proxied}


def _generate_plain(source, destination, target_triangles):
    from pxr import Usd

    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError('USD file could not be opened')
    result = _add_proxies(stage, target_triangles)
    if 'skipped' in result:
        return result
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not stage.GetRootLayer().Export(str(destination)):
        raise RuntimeError('Could not write the proxy layer')
    return result


def _generate_usdz(source, destination, target_triangles):
    from pxr import Usd, UsdUtils

    with zipfile.ZipFile(str(source)) as archive:
        names = archive.namelist()
    if not names:
        raise ValueError('Empty .usdz package')
    # The usdz spec requires the package's root layer to be the first entry of the archive.
    root_name = names[0]

    extract_dir = tempfile.mkdtemp(prefix='nanakusa_proxy_usdz_')
    try:
        if not UsdUtils.ExtractUsdzPackage(str(source), extract_dir, True, False, True):
            raise RuntimeError('Could not extract the .usdz package')
        root_layer = Path(extract_dir) / root_name
        stage = Usd.Stage.Open(str(root_layer))
        if not stage:
            raise ValueError('USD file could not be opened')
        result = _add_proxies(stage, target_triangles)
        if 'skipped' in result:
            return result
        stage.GetRootLayer().Save()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        if not UsdUtils.CreateNewUsdzPackage(str(root_layer), str(destination)):
            raise RuntimeError('Could not repackage the .usdz')
        return result
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def generate(source, destination, target_triangles=TARGET_TRIANGLES):
    if Path(source).suffix.lower() == '.usdz':
        return _generate_usdz(source, destination, target_triangles)
    return _generate_plain(source, destination, target_triangles)


if __name__ == '__main__':
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    result = generate(sys.argv[1], sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')
