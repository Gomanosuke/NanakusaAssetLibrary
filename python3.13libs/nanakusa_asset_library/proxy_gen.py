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


# Scanned plants carry thousands of tiny separate pieces (florets of a seed head, seeds, specks).
# polyreduce cannot merge pieces, so each keeps a few triangles and they alone made a grass proxy
# 16k triangles against a 300 target. Pieces smaller than SMALL_PIECE of the mesh's diagonal are
# instead gathered into groups (pieces within CARD_GAP of each other: one seed head) and each group
# becomes one flat card - a quad on the group's best-fitting plane, in the group's average color.
SMALL_PIECE = 0.03
CARD_GAP = 0.005
CARD_MIN_WIDTH = 0.15   # of the card's length: a straight row of florets still reads as a card


def _pieces(points, face_vertex_counts, face_vertex_indices):
    """Connected-piece label per face, after welding points at the same position (a UV or
    material seam splits one surface into separate points but not into separate pieces)."""
    import numpy
    points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
    counts = numpy.asarray(face_vertex_counts, dtype=numpy.int64)
    indices = numpy.asarray(face_vertex_indices, dtype=numpy.int64)
    diagonal = float(numpy.linalg.norm(points.max(0) - points.min(0))) if len(points) else 0.0
    tolerance = max(diagonal * 0.0002, 1e-9)
    _, weld = numpy.unique(numpy.round(points / tolerance).astype(numpy.int64), axis=0, return_inverse=True)
    weld = weld.ravel()
    starts = numpy.r_[0, numpy.cumsum(counts)[:-1]]
    corner = weld[indices]
    # Every corner joins its face's first corner: union-find by repeated minimum labels.
    first = numpy.repeat(corner[starts], counts)
    label = numpy.arange(weld.max() + 1 if len(weld) else 0)
    while True:
        low = numpy.minimum(label[corner], label[first])
        new = label.copy()
        numpy.minimum.at(new, corner, low)
        numpy.minimum.at(new, first, low)
        new = new[new[new]]
        if numpy.array_equal(new, label):
            break
        label = new
    return numpy.unique(label[corner[starts]], return_inverse=True)[1].ravel()


def _split_small_pieces(points, face_vertex_counts, face_vertex_indices, uvs=None):
    """Split a mesh into its large part and groups of small pieces.

    Returns (large, groups) or None when there is nothing to split off: `large` is (points,
    counts, indices, uvs) of the faces kept as a mesh (may have no faces), `groups` a list of
    (points, counts, indices, uvs) sub-meshes, one per future card."""
    import numpy
    points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
    counts = numpy.asarray(face_vertex_counts, dtype=numpy.int64)
    indices = numpy.asarray(face_vertex_indices, dtype=numpy.int64)
    if len(counts) < 2:
        return None
    diagonal = float(numpy.linalg.norm(points.max(0) - points.min(0)))
    piece = _pieces(points, counts, indices)
    pieces = piece.max() + 1
    if pieces < 2:
        return None
    face = numpy.repeat(numpy.arange(len(counts)), counts)
    corner_piece = piece[face]
    low = numpy.full((pieces, 3), numpy.inf)
    high = numpy.full((pieces, 3), -numpy.inf)
    numpy.minimum.at(low, corner_piece, points[indices])
    numpy.maximum.at(high, corner_piece, points[indices])
    small = numpy.linalg.norm(high - low, axis=1) < SMALL_PIECE * diagonal
    if small.sum() < 2:
        return None
    # Groups: small pieces with corners in the same or neighbouring cells of a CARD_GAP grid.
    gap = CARD_GAP * diagonal
    cell = numpy.floor((points[indices] - points.min(0)) / max(gap, 1e-12)).astype(numpy.int64) + 1
    size = cell.max(0) + 2
    key = (cell[:, 0] * size[1] + cell[:, 1]) * size[2] + cell[:, 2]
    in_small = small[corner_piece]
    pairs = numpy.unique(numpy.stack([key[in_small], corner_piece[in_small]], 1), axis=0)
    first_in_cell = {}
    for k, p in pairs.tolist():
        first_in_cell.setdefault(k, p)
    group = list(range(pieces))

    def find(p):
        while group[p] != p:
            group[p] = group[group[p]]
            p = group[p]
        return p

    for k, p in pairs.tolist():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    q = first_in_cell.get(k + (dx * size[1] + dy) * size[2] + dz)
                    if q is not None:
                        a, b = find(p), find(q)
                        if a != b:
                            group[max(a, b)] = min(a, b)
    face_group = numpy.array([find(p) for p in piece.tolist()])
    face_small = small[piece]
    starts = numpy.r_[0, numpy.cumsum(counts)[:-1]]

    def sub(face_mask):
        chosen = numpy.nonzero(face_mask)[0]
        if not len(chosen):
            return [], [], [], None
        corners = numpy.concatenate([numpy.arange(starts[f], starts[f] + counts[f]) for f in chosen])
        used, local = numpy.unique(indices[corners], return_inverse=True)
        return (points[used].tolist(), counts[chosen].tolist(), local.ravel().tolist(),
                [uvs[c] for c in corners.tolist()] if uvs else None)

    large = sub(~face_small)
    groups = [sub(face_small & (face_group == g)) for g in numpy.unique(face_group[face_small])]
    return large, groups


def _card(points, colors=None):
    """A quad covering `points` on their best-fitting plane: (4 corners, color or None)."""
    import numpy
    points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
    centre = points.mean(0)
    axes = numpy.linalg.svd(points - centre, full_matrices=True)[2] if len(points) > 1 else numpy.eye(3)
    u, v = axes[0], axes[1]
    pu, pv = (points - centre) @ u, (points - centre) @ v
    # Full length (the card must still meet its stem), but 5-95 % across: a few florets sticking
    # out must not make the card much wider than the head reads.
    (u0, u1), (v0, v1) = (pu.min(), pu.max()), numpy.percentile(pv, (5, 95))
    length = max(u1 - u0, 1e-9)
    if v1 - v0 < CARD_MIN_WIDTH * length:
        middle = (v0 + v1) / 2
        v0, v1 = middle - CARD_MIN_WIDTH * length / 2, middle + CARD_MIN_WIDTH * length / 2
    corners = [centre + a * u + b * v for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    color = numpy.asarray(colors, dtype=numpy.float64).reshape(-1, 3).mean(0).tolist() if colors else None
    return [c.tolist() for c in corners], color


def _proxy_geometry(points, counts, indices, target, texture=None, uvs=None):
    """(points, counts, indices, colors or None) of a proxy. A mesh over the target has its small
    pieces made into cards and the rest reduced to about `target` triangles; a lighter one is kept
    as it is. Colors are baked from `texture` when given."""
    split = _split_small_pieces(points, counts, indices, uvs) if target is not None else None
    if split is None:
        if target is not None or texture:
            return _decimate(points, counts, indices, target, texture, uvs)
        return list(points), list(counts), list(indices), None
    (lp, lc, li, luv), groups = split
    out_points, out_counts, out_indices, out_colors = [], [], [], []
    if lc:
        keep = target if _triangle_count(lc) > target else None
        colors = None
        if keep is not None or texture:
            lp, lc, li, colors = _decimate(lp, lc, li, keep, texture, luv)
        out_points, out_counts, out_indices = list(lp), list(lc), list(li)
        out_colors = list(colors) if colors else [None] * len(lp)
    for gp, gc, gi, guv in groups:
        colors = _decimate(gp, gc, gi, None, texture, guv)[3] if texture and guv else None
        corners, color = _card(gp, colors)
        base = len(out_points)
        out_points += corners
        out_counts.append(4)
        out_indices += [base, base + 1, base + 2, base + 3]
        out_colors += [color] * 4
    if all(c is None for c in out_colors):
        return out_points, out_counts, out_indices, None
    return out_points, out_counts, out_indices, [c if c is not None else [0.5, 0.5, 0.5] for c in out_colors]


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
    entirely when there is neither a target nor a texture, since even a no-op call builds a node
    network.

    The mesh goes in as in-memory geometry (a Stash SOP) and comes back through bulk attribute
    reads: meshes of a million triangles (SalixCaprea_1x94n_Big_OL.usd), whose thousands of
    separate leaves polyreduce cannot bring below ~250k triangles, took minutes per mesh through
    an OBJ file and a per-vertex Python loop and ran into the job's time limit.
    """
    import hou
    import numpy
    points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
    scratch = hou.node('/obj').createNode('geo', 'nanakusa_proxy_scratch')
    try:
        geo = hou.Geometry()
        geo.createPoints(points.tolist())
        polys, offset = [], 0
        for count in face_vertex_counts:
            polys.append(tuple(face_vertex_indices[offset:offset + count]))
            offset += count
        geo.createPolygons(polys)
        if uvs:
            # one (u, v) per face-vertex, in the same face-vertex order the polygons were made in
            geo.addAttrib(hou.attribType.Vertex, 'uv', (0.0, 0.0, 0.0))
            flat = numpy.zeros((len(uvs), 3)); flat[:, :2] = numpy.asarray(uvs, dtype=numpy.float64).reshape(-1, 2)
            geo.setVertexFloatAttribValues('uv', flat.ravel().tolist())
        source = scratch.createNode('stash')
        source.parm('stash').set(geo)
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
            diagonal = float(numpy.linalg.norm(points.max(axis=0) - points.min(axis=0))) if len(points) else 0.0
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
        out = node.geometry().freeze()
        colors = None
        if out.findPointAttrib('Cd') is not None:
            # polyreduce's attribute blending can overshoot slightly at sharp color edges.
            colors = numpy.clip(numpy.asarray(out.pointFloatAttribValues('Cd')).reshape(-1, 3), 0.0, 1.0).tolist()
        if target is None:
            # Only a color bake: the topology is the one that went in.
            return points.tolist(), list(face_vertex_counts), list(face_vertex_indices), colors
        out_points = numpy.asarray(out.pointFloatAttribValues('P')).reshape(-1, 3).tolist()
        if out.vertexCount() != 3 * len(out.prims()):
            raise RuntimeError('reduction left non-triangles')   # never expected after divide + polyreduce
        # Each face corner's point number, read in bulk: number the points, then promote that
        # attribute to the corners (hou has no bulk read of polygon connectivity).
        out.addAttrib(hou.attribType.Point, 'nal_point', 0)
        out.setPointIntAttribValues('nal_point', list(range(len(out.points()))))
        promote = hou.sopNodeTypeCategory().nodeVerb('attribpromote')
        promote.setParms({'inname': 'nal_point', 'inclass': 2, 'outclass': 3, 'method': 8, 'deletein': 1})
        corners = hou.Geometry()
        promote.execute(corners, [out])
        out_indices = list(corners.vertexIntAttribValues('nal_point'))
        return out_points, [3] * (len(out_indices) // 3), out_indices, colors
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


def _is_lod_set(name):
    # Same rule as core.is_lod_set (this worker script does not import the package's core).
    return name.lower().startswith('lod')


def _collect_meshes(stage):
    """{path: mesh data} of every render mesh the asset can show, not only the ones the authored
    variant selections compose: after the stage as authored, each other variant of every variant
    set is selected in turn (one set at a time, in the session layer, never in the file). A
    level-of-detail set is left at its selection - its levels swap a mesh's geometry, and one
    proxy stands in for all of them. The first look at a mesh wins (the authored selections for
    the rest), and its data are read while it is composed."""
    from pxr import Sdf, Usd, UsdGeom
    found = {}

    def look():
        for prim in stage.Traverse():
            path = prim.GetPath()
            if (path in found or not prim.IsA(UsdGeom.Mesh) or prim.IsInstanceProxy()
                    or UsdGeom.Imageable(prim).ComputePurpose() == UsdGeom.Tokens.proxy):
                continue
            mesh = UsdGeom.Mesh(prim)
            points = mesh.GetPointsAttr().Get()
            if not points:
                continue
            counts = mesh.GetFaceVertexCountsAttr().Get()
            indices = mesh.GetFaceVertexIndicesAttr().Get()
            texture = _find_base_color_texture(prim)
            uvs = _face_vertex_uvs(prim, texture[1], counts, indices) if texture else None
            found[path] = {'points': points, 'counts': counts, 'indices': indices,
                           'texture': texture[0] if texture and uvs else None, 'uvs': uvs if texture else None,
                           'defined_outside_variants': any(spec.specifier == Sdf.SpecifierDef and not spec.path.ContainsPrimVariantSelection()
                                                           for spec in prim.GetPrimStack())}

    _sweep_variants(stage, look)
    return found


def _sweep_variants(stage, look):
    """Call `look()` with the stage as authored, then with each other variant of every variant set
    that is not a level-of-detail set selected in turn (one set at a time, in the session layer,
    never in the file)."""
    from pxr import Usd
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        look()
        sets = [(prim.GetPath(), name) for prim in stage.Traverse()
                for name in prim.GetVariantSets().GetNames() if not _is_lod_set(name)]
        for path, name in sets:
            variant_set = stage.GetPrimAtPath(path).GetVariantSet(name)
            authored = variant_set.GetVariantSelection()
            for variant in variant_set.GetVariantNames():
                if variant != authored:
                    variant_set.SetVariantSelection(variant)
                    look()
            variant_set.ClearVariantSelection()   # the session opinion only; the file's selection stays


# A proxy is a sibling of its render mesh, so it inherits whatever material is bound above them
# (e.g. on the asset's "geo" scope). Having no UVs, it samples that material's textures at
# (0, 0): leaf and grass opacity maps are 0 there, so with the material's opacity threshold the
# viewport cut the whole proxy away (AcerPseudoplatanus_abw4u_Leaves_OL). An empty binding
# does not stop the inheritance (UsdShade keeps looking further up), but a binding whose target
# is not a Material does: ComputeBoundMaterial stops at the proxy's own binding and returns no
# material. So a proxy is bound to this plain Scope, and a viewport draws it the way it draws any
# mesh with no material - in the proxy's own displayColor (@Cd).
# 0.22.2 made it an empty Material instead: Houdini's Scene View drew that as a plain grey
# material and ignored the colors (AcerPseudoplatanus_853se_Big_OL); `_proxy_look` turns such a
# Material into the Scope. (A UsdPreviewSurface reading displayColor came out black in husk's Storm.)
PROXY_LOOK = 'NAL_proxy_look'


def _proxy_look(stage, proxy_path):
    """Path of the binding stop under the proxy's top prim: a Scope, defined there if missing (or
    turned from the Material 0.22.2 wrote into one)."""
    from pxr import Sdf, UsdShade
    prefixes = proxy_path.GetPrefixes()
    top = prefixes[0] if len(prefixes) > 1 else Sdf.Path.absoluteRootPath   # never under a mesh
    path = top.AppendChild(PROXY_LOOK)
    existing = stage.GetPrimAtPath(path)
    if not existing or existing.GetTypeName() != 'Scope':
        stage.DefinePrim(path, 'Scope')
    return path


def _on_old_look(prim):
    """True for a proxy bound to the empty Material that 0.22.2 made (shown grey in Houdini)."""
    from pxr import UsdShade
    for target in UsdShade.MaterialBindingAPI(prim).GetDirectBindingRel().GetTargets():
        look = prim.GetStage().GetPrimAtPath(target)
        if target.name == PROXY_LOOK and look and look.IsA(UsdShade.Material):
            return True
    return False


def _needs_look(prim):
    """True for a proxy mesh that would draw with a material bound above it: nothing bound on the
    proxy itself and no UVs of its own to read that material's textures with."""
    from pxr import Sdf, UsdGeom, UsdShade
    binding = UsdShade.MaterialBindingAPI(prim)
    if binding.GetDirectBindingRel().GetTargets():
        return False
    uv_types = (Sdf.ValueTypeNames.TexCoord2fArray, Sdf.ValueTypeNames.TexCoord2dArray,
                Sdf.ValueTypeNames.TexCoord2hArray, Sdf.ValueTypeNames.Float2Array)
    if any(p.GetTypeName() in uv_types for p in UsdGeom.PrimvarsAPI(prim).GetPrimvarsWithValues()):
        return False
    return bool(binding.ComputeBoundMaterial()[0])


def _bind_look(layer, path, look):
    """Bind the prim at `path` to `look`, authored with Sdf in `layer` (the prim may be defined
    inside a variant that is not selected now; an "over" at its plain path reaches it there)."""
    from pxr import Sdf
    spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
    schemas = spec.GetInfo('apiSchemas')
    if 'MaterialBindingAPI' not in list(schemas.prependedItems) + list(schemas.explicitItems):
        schemas.prependedItems = list(schemas.prependedItems) + ['MaterialBindingAPI']
        spec.SetInfo('apiSchemas', schemas)
    rel = spec.relationships.get('material:binding') or Sdf.RelationshipSpec(spec, 'material:binding', custom=False)
    rel.targetPathList.explicitItems = [look]


def _restyle_proxies(stage):
    """Bind every existing proxy that `_needs_look` to the proxy look (proxies made before it
    existed, and a source's own proxies with the same problem), and move proxies off the 0.22.2
    Material form of it. Returns the paths bound."""
    from pxr import UsdGeom
    found = set()

    def look():
        for prim in stage.Traverse():
            if (prim.GetPath() not in found and prim.IsA(UsdGeom.Mesh) and not prim.IsInstanceProxy()
                    and UsdGeom.Imageable(prim).ComputePurpose() == UsdGeom.Tokens.proxy
                    and (_needs_look(prim) or _on_old_look(prim))):
                found.add(prim.GetPath())
    _sweep_variants(stage, look)
    layer = stage.GetRootLayer()
    for path in found:
        _bind_look(layer, path, _proxy_look(stage, path))
    return sorted(str(p) for p in found)


def _variant_specs(layer):
    """(variant set name, variant prim spec) for every variant in `layer`, nested ones included."""
    out = []

    def walk(spec):
        for child in spec.nameChildren:
            for set_name, variant_set in child.variantSets.items():
                for variant in variant_set.variants.values():
                    out.append((set_name, variant.primSpec))
                    walk(variant.primSpec)
            walk(child)
    walk(layer.pseudoRoot)
    return out


def _variant_definitions(layer, mesh_path, variant_specs):
    """Paths of the specs inside variants of `layer` that define the prim at `mesh_path` ("def",
    not "over"), whichever variants are selected now."""
    from pxr import Sdf
    found = []
    for _set_name, variant in variant_specs:
        owner = variant.path.StripAllVariantSelections()
        if mesh_path.HasPrefix(owner):
            spec = layer.GetPrimAtPath(mesh_path.ReplacePrefix(owner, variant.path))
            if spec is not None and spec.specifier == Sdf.SpecifierDef:
                found.append(spec.path)
    return found


def _copy_switching(layer, mesh_path, proxy_path, variant_specs):
    """Give the proxy the same "active" / "visibility" opinions its render mesh gets - authored
    directly, and inside every variant of a variant set that is not a level-of-detail set - so
    whatever switches a mesh off (a source's own `variant` set deactivating the other versions)
    switches its proxy off with it. A level-of-detail set is skipped on purpose: when one hides
    the original mesh for a reduced copy (lod_gen.py), its proxy still stands in for the asset."""
    from pxr import Sdf
    places = [(Sdf.Path.absoluteRootPath, Sdf.Path.absoluteRootPath)]
    for set_name, variant in variant_specs:
        owner = variant.path.StripAllVariantSelections()
        if not _is_lod_set(set_name) and mesh_path.HasPrefix(owner):
            places.append((owner, variant.path))
    for owner, base in places:
        mesh_spec = layer.GetPrimAtPath(mesh_path.ReplacePrefix(owner, base))
        if mesh_spec is None:
            continue
        visibility = mesh_spec.attributes.get('visibility')
        has_active = mesh_spec.HasInfo('active')
        if not has_active and (visibility is None or not visibility.HasDefaultValue()):
            continue
        target = proxy_path.ReplacePrefix(owner, base)
        spec = layer.GetPrimAtPath(target) or Sdf.CreatePrimInLayer(layer, target)
        if has_active:
            spec.active = mesh_spec.active
        if visibility is not None and visibility.HasDefaultValue():
            attr = spec.attributes.get('visibility') or Sdf.AttributeSpec(spec, 'visibility', Sdf.ValueTypeNames.Token)
            attr.default = visibility.default


def _add_proxies(stage, target_triangles):
    """Author a proxy for every render mesh directly onto an already-open, editable stage.

    Returns {'skipped': reason} or {'proxied': [...prim paths...]} - the same shape `generate()`
    returns, so both the plain-file and the .usdz path (which edits an extracted copy of the
    package's root layer) can share this.

    Each proxy is a purpose=proxy sibling of its mesh, defined where the mesh is defined (inside
    the same variants when the mesh only exists inside variants), switched on and off exactly
    like its mesh (_copy_switching), and the mesh itself becomes purpose=render. Assets whose
    meshes only some variant selections show (a source's own "variant" set) get a proxy for every
    one of them (_collect_meshes), so the viewport shows the selected version's proxy only.

    A proxy carries only points and displayColor (Houdini's @P and @Cd), and one that would
    otherwise draw with a material bound above it is bound to the proxy look (PROXY_LOOK). An
    asset that already has proxies only gets that binding where they lack it ({'restyled': [...]}).
    """
    from pxr import Sdf, Tf, UsdGeom

    if any(UsdGeom.Imageable(p).GetPurposeAttr().HasAuthoredValue()
           and UsdGeom.Imageable(p).GetPurposeAttr().Get() == UsdGeom.Tokens.proxy
           for p in stage.Traverse()):
        restyled = _restyle_proxies(stage)
        return {'restyled': restyled} if restyled else {'skipped': 'already has a proxy'}

    meshes = _collect_meshes(stage)
    if not meshes:
        return {'skipped': 'no mesh geometry'}

    layer = stage.GetRootLayer()
    variant_specs = _variant_specs(layer)
    taken = set(meshes)
    proxied = []
    for path, data in meshes.items():
        points, counts, indices = data['points'], data['counts'], data['indices']
        # Give the proxy a sense of the render mesh's look: bake its base color texture down to
        # one color per point, the way an Attribute from Map SOP would, so a plain grey stand-in
        # is not the only option in the viewport.
        target = target_triangles if _triangle_count(counts) > target_triangles else None
        points, counts, indices, colors = _proxy_geometry(points, counts, indices, target, data['texture'], data['uvs'])

        name = Tf.MakeValidIdentifier(path.name + '_proxy')
        while path.GetParentPath().AppendChild(name) in taken or stage.GetPrimAtPath(path.GetParentPath().AppendChild(name)):
            name += '_'
        proxy_path = path.GetParentPath().AppendChild(name)
        taken.add(proxy_path)
        proxy = UsdGeom.Mesh.Define(stage, proxy_path)
        proxy.CreatePointsAttr(points)
        proxy.CreateFaceVertexCountsAttr(counts)
        proxy.CreateFaceVertexIndicesAttr(indices)
        if colors:
            proxy.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(colors)
        UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
        if _needs_look(proxy.GetPrim()):
            # bound before it is copied into variants, so every copy carries the binding
            _bind_look(layer, proxy_path, _proxy_look(stage, proxy_path))
        defs =_variant_definitions(layer, path, variant_specs)
        if not data['defined_outside_variants'] and defs:
            # The mesh only exists inside variants (e.g. every LOD variant defines its own
            # version): define the proxy in each of them too, so it exists exactly where its mesh does.
            for mesh_def in defs:
                Sdf.CopySpec(layer, proxy_path, layer, mesh_def.GetParentPath().AppendChild(name))
            _remove_spec(layer, proxy_path)
        _copy_switching(layer, path, proxy_path, variant_specs)
        # The mesh may not be composed right now (another variant is selected), so author with Sdf.
        mesh_spec = layer.GetPrimAtPath(path) or Sdf.CreatePrimInLayer(layer, path)
        purpose = mesh_spec.attributes.get('purpose') or Sdf.AttributeSpec(
            mesh_spec, 'purpose', Sdf.ValueTypeNames.Token, Sdf.VariabilityUniform)
        purpose.default = UsdGeom.Tokens.render
        _mark_lod_copies_render(layer, path, variant_specs)
        proxied.append(str(path))
    return {'proxied': sorted(proxied)}


def _mark_lod_copies_render(layer, mesh_path, variant_specs):
    """lod_gen.py's reduced copies of a mesh ("<mesh>_LOD_k", defined in its LOD variants) copy
    the mesh's purpose when they are made. Made before this proxy, they are still default and the
    viewport would draw one next to the proxy, so they become render-only like their mesh."""
    from pxr import Sdf, UsdGeom
    for set_name, variant in variant_specs:
        owner = variant.path.StripAllVariantSelections()
        if not _is_lod_set(set_name) or not mesh_path.HasPrefix(owner):
            continue
        parent = layer.GetPrimAtPath(mesh_path.GetParentPath().ReplacePrefix(owner, variant.path))
        if parent is None:
            continue
        for child in parent.nameChildren:
            if child.name.startswith(mesh_path.name + '_LOD_') and child.specifier == Sdf.SpecifierDef and child.typeName == 'Mesh':
                purpose = child.attributes.get('purpose') or Sdf.AttributeSpec(
                    child, 'purpose', Sdf.ValueTypeNames.Token, Sdf.VariabilityUniform)
                purpose.default = UsdGeom.Tokens.render


def _remove_spec(layer, path):
    """Delete a prim spec, then any parent "over" left with nothing in it."""
    from pxr import Sdf
    spec = layer.GetPrimAtPath(path)
    while spec is not None and spec.path != Sdf.Path.absoluteRootPath:
        parent = spec.nameParent
        del parent.nameChildren[spec.name]
        spec = parent if parent.path != Sdf.Path.absoluteRootPath and parent.specifier == Sdf.SpecifierOver and parent.IsInert() else None


def generate_plain(source, destination, add_fn):
    """Open a plain USD file, run `add_fn(stage)`, and Export() the result to a new file.

    Shared with element_gen.py: `add_fn` does the actual editing and returns the same
    {'skipped': reason} or {...: [...]} shape that becomes the caller's result.
    """
    from pxr import Usd

    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError('USD file could not be opened')
    layer = stage.GetRootLayer()
    was_dirty = layer.dirty
    try:
        result = add_fn(stage)
        if 'skipped' in result:
            return result
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not layer.Export(str(destination)):
            raise RuntimeError('Could not write the layer')
        return result
    finally:
        # The edits were for the new file only: leave the source layer as saved for anything else
        # in this process that opens it (the layer registry would otherwise hand out the edited copy).
        if not was_dirty and layer.dirty:
            layer.Reload()


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
