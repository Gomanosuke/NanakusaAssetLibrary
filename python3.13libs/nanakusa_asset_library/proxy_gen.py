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


def _decimate(points, face_vertex_counts, face_vertex_indices, target=None, texture_path=None, uvs=None):
    """Off-screen Houdini pass over a polygon mesh: optionally bake a per-point color from a
    texture (attribfrommap, using `uvs` - one (u, v) pair per face-vertex, matching
    `face_vertex_indices`), and/or reduce to about `target` triangles (fuse+triangulate+polyreduce).

    Returns (points, face_vertex_counts, face_vertex_indices, colors_or_None). Callers skip this
    entirely when there is neither a target nor a texture, since even a no-op call round-trips
    through an OBJ file and a node network.
    """
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
            if uvs:
                for u, v in uvs:
                    stream.write('vt %.9g %.9g\n' % (u, v))
            offset = 0
            for count in face_vertex_counts:
                if uvs:
                    # face_vertex_indices[i] is the point (v); i itself is this face-vertex's
                    # position in the flat, face-vertex-ordered uvs list (vt) written above.
                    corners = ('%d/%d' % (face_vertex_indices[i]+1, i+1) for i in range(offset, offset+count))
                else:
                    corners = (str(i+1) for i in face_vertex_indices[offset:offset+count])
                stream.write('f ' + ' '.join(corners) + '\n')
                offset += count
        try:
            source.parm('file').set(str(obj_path))
            node = source
            if texture_path and uvs:
                bake = scratch.createNode('attribfrommap')
                bake.setInput(0, node)
                bake.parm('use_file').set('on')
                bake.parm('filename').set(str(texture_path))
                bake.parm('uvattrib').set('uv')
                bake.parm('export_attribute').set('Cd')
                bake.parm('attrib_type').set('vector')
                node = bake
            if target is not None:
                # Points meant to be connected often arrive as separate coincident copies (UV or
                # material seams), so without this polyreduce treats every seam-bound island as
                # its own tiny piece and shrinks each independently, breaking the overall shape.
                lo = [min(p[axis] for p in points) for axis in range(3)]
                hi = [max(p[axis] for p in points) for axis in range(3)]
                diagonal = sum((hi[axis]-lo[axis])**2 for axis in range(3))**0.5
                fuse = scratch.createNode('fuse')
                fuse.setInput(0, node)
                fuse.parm('usetol3d').set(True)
                fuse.parm('tol3d').set(max(diagonal*0.0002, 1e-6))
                # polyreduce's "Output Polygon Count" counts primitives, not triangles; triangulating
                # first (quads and n-gons are common in source meshes) makes that count exactly the
                # triangle count `target` means, instead of leaving roughly twice as many as asked.
                triangulate = scratch.createNode('divide')
                triangulate.setInput(0, fuse)
                triangulate.parm('convex').set(True)
                triangulate.parm('usemaxsides').set(True)
                triangulate.parm('numsides').set(3)
                reduce_node = scratch.createNode('polyreduce::2.0')
                reduce_node.setInput(0, triangulate)
                reduce_node.parm('target').set('poly_count')
                reduce_node.parm('finalcount').set(max(int(target), 4))
                node = reduce_node
            node.cook(force=True)
            if node.errors():
                raise RuntimeError('; '.join(node.errors()))
            geo = node.geometry()
            out_points = [tuple(p.position()) for p in geo.points()]
            out_counts, out_indices = [], []
            for prim in geo.prims():
                verts = prim.vertices()
                out_counts.append(len(verts))
                out_indices.extend(v.point().number() for v in verts)
            colors = None
            cd = geo.findPointAttrib('Cd')
            if cd is not None:
                # polyreduce's attribute blending can overshoot slightly at sharp color edges.
                colors = [tuple(min(1.0, max(0.0, c)) for c in p.attribValue('Cd')) for p in geo.points()]
            return out_points, out_counts, out_indices, colors
        finally:
            obj_path.unlink(missing_ok=True)
    finally:
        scratch.destroy()


def _find_base_color_texture(prim):
    """(resolved texture path, uv primvar name) from the prim's bound material, or None.

    Handles the common UsdPreviewSurface shape: a diffuseColor/baseColor input connected to a
    UsdUVTexture shader, whose own "st" input is connected to a UsdPrimvarReader naming the
    primvar to read (or "st" if that reader is missing, per the UsdPreviewSurface convention).
    """
    from pxr import UsdShade

    binding = UsdShade.MaterialBindingAPI(prim)
    material, _ = binding.ComputeBoundMaterial()
    if not material:
        return None
    shader, _, _ = material.ComputeSurfaceSource()
    if not shader:
        return None
    for name in ('diffuseColor', 'baseColor', 'base_color'):
        color_input = shader.GetInput(name)
        if not color_input:
            continue
        connection = color_input.GetConnectedSource()
        if not connection:
            continue
        tex_shader = UsdShade.Shader(connection[0].GetPrim())
        file_input = tex_shader.GetInput('file')
        file_value = file_input.Get() if file_input else None
        if not file_value or not file_value.resolvedPath:
            continue
        uv_name = 'st'
        st_input = tex_shader.GetInput('st') or tex_shader.GetInput('uv') or tex_shader.GetInput('texcoord')
        if st_input:
            st_connection = st_input.GetConnectedSource()
            if st_connection:
                varname_input = UsdShade.Shader(st_connection[0].GetPrim()).GetInput('varname')
                value = varname_input.Get() if varname_input else None
                if value:
                    uv_name = str(value)
        return file_value.resolvedPath, uv_name
    return None


def _face_vertex_uvs(prim, uv_name, face_vertex_counts, face_vertex_indices):
    """UV per face-vertex (matching `face_vertex_indices`), from a primvar of any interpolation."""
    from pxr import UsdGeom

    primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar(uv_name)
    if not primvar or not primvar.HasValue():
        return None
    values = primvar.ComputeFlattened()
    if not values:
        return None
    interpolation = primvar.GetInterpolation()
    if interpolation == UsdGeom.Tokens.faceVarying:
        return list(values) if len(values) == len(face_vertex_indices) else None
    if interpolation == UsdGeom.Tokens.vertex:
        return [values[i] for i in face_vertex_indices]
    if interpolation == UsdGeom.Tokens.uniform:
        if len(values) != len(face_vertex_counts):
            return None
        out = []
        for face_index, count in enumerate(face_vertex_counts):
            out.extend([values[face_index]] * count)
        return out
    if interpolation == UsdGeom.Tokens.constant:
        return [values[0]] * len(face_vertex_indices)
    return None


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

        # Give the proxy a sense of the render mesh's look: bake its base color texture down to
        # one color per point, the way an Attribute from Map SOP would, so a plain grey stand-in
        # is not the only option in the viewport.
        texture = _find_base_color_texture(prim)
        uvs = _face_vertex_uvs(prim, texture[1], counts, indices) if texture else None
        target = target_triangles if _triangle_count(counts) > target_triangles else None
        colors = None
        if target is not None or (texture and uvs):
            points, counts, indices, colors = _decimate(points, counts, indices, target,
                texture[0] if texture and uvs else None, uvs)

        parent = prim.GetParent()
        proxy_path = parent.GetPath().AppendChild(_sibling_name(parent, prim.GetName()))
        proxy = UsdGeom.Mesh.Define(stage, proxy_path)
        proxy.CreatePointsAttr(points)
        proxy.CreateFaceVertexCountsAttr(counts)
        proxy.CreateFaceVertexIndicesAttr(indices)
        if colors:
            proxy.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(colors)
        UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
        UsdGeom.Imageable(prim).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
        proxied.append(str(prim.GetPath()))
    return {'proxied': proxied}


def generate_plain(source, destination, add_fn):
    """Open a plain USD file, run `add_fn(stage)`, and Export() the result to a new file.

    Shared with element_gen.py: `add_fn` does the actual editing and returns the same
    {'skipped': reason} or {...: [...]} shape that becomes the caller's result.
    """
    from pxr import Usd

    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError('USD file could not be opened')
    result = add_fn(stage)
    if 'skipped' in result:
        return result
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not stage.GetRootLayer().Export(str(destination)):
        raise RuntimeError('Could not write the layer')
    return result


def generate_usdz(source, destination, add_fn):
    """Extract a .usdz, run `add_fn(stage)` on its root layer, and repackage it.

    Shared with element_gen.py; see generate_plain.
    """
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
        result = add_fn(stage)
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
    add_fn = lambda stage: _add_proxies(stage, target_triangles)
    if Path(source).suffix.lower() == '.usdz':
        return generate_usdz(source, destination, add_fn)
    return generate_plain(source, destination, add_fn)


if __name__ == '__main__':
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    target_triangles = int(sys.argv[4]) if len(sys.argv) > 4 else TARGET_TRIANGLES
    result = generate(sys.argv[1], sys.argv[2], target_triangles)
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')
