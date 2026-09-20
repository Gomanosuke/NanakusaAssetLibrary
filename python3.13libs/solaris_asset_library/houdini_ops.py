"""Houdini 22 adapters. All calls must run on Houdini's main thread."""
from pathlib import Path
import re
from datetime import datetime
from . import core

def safe_name(value):
    name = re.sub(r'[^A-Za-z0-9_]', '_', value).strip('_') or 'asset'
    return ('asset_' if name[0].isdigit() else '') + name[:70]

def _set(node, name, value):
    parm = node.parm(name)
    if parm is None:
        raise RuntimeError('Unsupported Houdini node parameter: ' + node.type().name() + '/' + name)
    parm.set(value)

def _encoded_parm(node, token, value):
    # USD properties use encoded parameter names, changed between H22 builds.
    matches = [p for p in node.parms() if token in p.name() and '_control_' not in p.name()]
    if len(matches) != 1:
        raise RuntimeError('Could not identify ' + token + ' on ' + node.path())
    matches[0].set(value)

def _material(parent, name, maps, settings, upstream=None):
    import voptoolutils
    lib = parent.createNode('materiallibrary', name + '_materials')
    if upstream:
        lib.setInput(0, upstream)
    builder = voptoolutils._setupMtlXBuilderSubnet(destination_node=lib, name=name)
    shader = next(n for n in builder.children() if n.type().name() == 'mtlxstandard_surface')
    matpath = '/materials/' + lib.name() + '/' + name
    lib.parm('matnode1').set(builder.name())
    lib.parm('matpath1').set(matpath)
    for channel, source in maps.items():
        img = builder.createNode('mtlximage', channel)
        sig = 'color3' if channel in ('base_color', 'emission', 'opacity') else ('vector3' if channel == 'normal' else 'float')
        img.parm('signature').set(sig)
        img.parm('file').set(source)
        space = settings.get('color_space', 'srgb_texture') if channel in ('base_color', 'emission') else 'Raw'
        img.parm('filecolorspace').set(space)
        if channel == 'normal':
            normal = builder.createNode('mtlxnormalmap', 'normal_decode')
            normal.setNamedInput('in', img, 'out')
            shader.setNamedInput('normal', normal, 'out')
        elif channel == 'displacement':
            disp = next(n for n in builder.children() if n.type().name() == 'mtlxdisplacement')
            disp.setNamedInput('displacement', img, 'out')
            if disp.parm('scale'):
                disp.parm('scale').set(float(settings.get('displacement_scale', 0.01)))
        else:
            target = {'roughness': 'specular_roughness', 'emission': 'emission_color'}.get(channel, channel)
            shader.setNamedInput(target, img, 'out')
            if channel == 'emission':
                shader.parm('emission').set(1)
    builder.layoutChildren()
    lib.layoutChildren()
    return lib, matpath

def _source_prim(path):
    from pxr import Usd
    stage = Usd.Stage.Open(str(path), load=Usd.Stage.LoadNone)
    if not stage:
        raise ValueError('USD/MaterialX file could not be opened')
    default = stage.GetDefaultPrim()
    if default:
        return str(default.GetPath())
    roots = [p for p in stage.GetPseudoRoot().GetChildren() if not p.IsAbstract()]
    if len(roots) == 1:
        return str(roots[0].GetPath())
    raise ValueError('Multiple root prims and no defaultPrim. Use USD Sublayer mode, or author a defaultPrim first.')

def import_asset(path, kind, label, target='/stage', upstream=None, usd_mode='reference', assign_pattern=''):
    import hou
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    parent = hou.node(target)
    if parent is None or parent.childTypeCategory() != hou.lopNodeTypeCategory():
        raise ValueError('Destination must be a Solaris LOP network')
    if upstream and upstream.parent() != parent:
        raise ValueError('The input node belongs to a different network')
    before = set(parent.children())
    name = safe_name(label)
    matpath = None
    try:
        with hou.undos.group('Asset Library: ' + label):
            if kind in ('usd', 'material'):
                if usd_mode == 'sublayer':
                    node = parent.createNode('sublayer', name)
                    _set(node, 'filepath1', p.as_posix())
                else:
                    source = _source_prim(p)
                    node = parent.createNode('reference', name)
                    _set(node, 'primpath', '/assets/' + node.name())
                    _set(node, 'primpath1', '/assets/' + node.name())
                    _set(node, 'filepath1', p.as_posix())
                    _set(node, 'filerefprimpath1', source)
                    # Explicit source path overrides automatic selection.
                    if node.parm('filerefprim1'):
                        node.parm('filerefprim1').set('')
                if upstream:
                    node.setInput(0, upstream)
                if kind == 'material' and assign_pattern:
                    from pxr import UsdShade
                    material_stage = node.stage()
                    root_path = '/assets/' + node.name()
                    materials = [prim for prim in material_stage.Traverse()
                                 if prim.IsA(UsdShade.Material) and (usd_mode == 'sublayer' or str(prim.GetPath()).startswith(root_path + '/'))]
                    if len(materials) != 1:
                        raise ValueError('Automatic assignment requires a single material. Import without an assignment pattern, then assign the desired material explicitly.')
                    assign = parent.createNode('assignmaterial', name + '_assign')
                    assign.setInput(0, node)
                    _set(assign, 'primpattern1', assign_pattern)
                    _set(assign, 'matspecpath1', str(materials[0].GetPath()))
                    node = assign
            elif kind == 'hdri':
                node = parent.createNode('domelight', name)
                _encoded_parm(node, 'texturefile', p.as_posix())
                if upstream:
                    node.setInput(0, upstream)
            elif kind == 'model':
                node = parent.createNode('sopcreate', name)
                node.parm('primpath').set('/assets/' + node.name())
                node.parm('pathprefix').set('/assets/' + node.name())
                subnet = node.node('sopnet/create')
                ext = p.suffix.lower()
                typ, parm = {
                    '.abc': ('alembic', 'fileName'), '.fbx': ('kinefx::fbxskinimport', 'fbxfile'),
                    '.gltf': ('gltf', 'gltffile'), '.glb': ('gltf', 'gltffile'),
                }.get(ext, ('file', 'file'))
                reader = subnet.createNode(typ, 'source_geometry')
                _set(reader, parm, p.as_posix())
                reader.setDisplayFlag(True); reader.setRenderFlag(True)
                subnet.layoutChildren()
                if upstream:
                    node.setInput(0, upstream)
            elif kind in ('pbr', 'texture', 'decal'):
                if p.name.lower().endswith('.json'):
                    settings, maps = core.read_manifest(p)
                else:
                    settings, maps = {}, {'base_color': p.as_posix()}
                if kind == 'decal':
                    # An explicit UV card: no automatic projection or surface modification.
                    card = parent.createNode('sopcreate', name + '_card')
                    prim = '/assets/' + card.name()
                    card.parm('primpath').set(prim)
                    card.parm('pathprefix').set(prim)
                    subnet = card.node('sopnet/create')
                    grid = subnet.createNode('grid', 'decal_card')
                    grid.parmTuple('size').set((1, 1))
                    grid.parm('rows').set(2); grid.parm('cols').set(2)
                    uv = subnet.createNode('texture', 'uv')
                    uv.setInput(0, grid)
                    uv.setDisplayFlag(True); uv.setRenderFlag(True)
                    subnet.layoutChildren()
                    if upstream:
                        card.setInput(0, upstream)
                    node, matpath = _material(parent, name, maps, settings, card)
                    if 'opacity' not in maps and 'base_color' in maps:
                        builder = next(n for n in node.children() if n.type().name() == 'subnet')
                        alpha = builder.createNode('mtlximage', 'rgba_for_alpha')
                        alpha.parm('signature').set('color4')
                        alpha.parm('file').set(maps['base_color'])
                        alpha.parm('filecolorspace').set('Raw')
                        extract = builder.createNode('mtlxextract', 'alpha')
                        extract.parm('signature').set('color4')
                        extract.parm('index').set(3)
                        extract.setNamedInput('in', alpha, 'out')
                        shader = next(n for n in builder.children() if n.type().name() == 'mtlxstandard_surface')
                        shader.setNamedInput('opacity', extract, 'out')
                        builder.layoutChildren()
                    assign_pattern = prim + '/**'
                else:
                    node, matpath = _material(parent, name, maps, settings, upstream)
                if assign_pattern:
                    assign = parent.createNode('assignmaterial', name + '_assign')
                    assign.setInput(0, node)
                    _set(assign, 'primpattern1', assign_pattern)
                    _set(assign, 'matspecpath1', matpath)
                    node = assign
            else:
                raise ValueError('Unsupported asset kind: ' + kind)
            for created in set(parent.children()) - before:
                created.setUserData('sal_source', p.as_posix())
                created.moveToGoodPosition()
            # Cook now: report actual import errors, do not leave a misleading success.
            stage = node.stage()
            if stage is None or node.errors():
                raise RuntimeError('\n'.join(node.errors()) or 'No USD stage produced')
            return node
    except Exception:
        for created in set(parent.children()) - before:
            created.destroy()
        raise

def export_stage(node, destination):
    """Export a standalone composed stage; preserve external image dependencies.

    Source USD layers resolve paths before flattening. Images are not copied.
    Destination must not exist. A new version is always explicit.
    """
    from pxr import Usd
    p = Path(destination)
    if p.suffix.lower() not in {'.usd', '.usda', '.usdc'}:
        raise ValueError('Choose .usd, .usda or .usdc')
    if p.exists():
        raise FileExistsError('Choose a new version; existing files are never overwritten: ' + str(p))
    stage = node.stage()
    if not stage or node.errors():
        raise RuntimeError('Cannot publish an invalid stage')
    layer = stage.Flatten()
    flat = Usd.Stage.Open(layer)
    # Solaris bookkeeping is not an asset and can contain transient op: paths.
    flat.RemovePrim('/HoudiniLayerInfo')
    roots = [x for x in flat.GetPseudoRoot().GetChildren() if not x.IsAbstract()]
    if len(roots) > 1:
        from pxr import UsdGeom
        wrapper_path = '/LibraryAsset'
        while flat.GetPrimAtPath(wrapper_path):
            wrapper_path += '_'
        UsdGeom.Xform.Define(flat, wrapper_path)
        for root in roots:
            editor = Usd.NamespaceEditor(flat)
            editor.MovePrimAtPath(root.GetPath(), wrapper_path + '/' + root.GetName())
            if not editor.CanApplyEdits() or not editor.ApplyEdits():
                raise RuntimeError('Could not package asset roots safely')
        roots = [flat.GetPrimAtPath(wrapper_path)]
    if len(roots) == 1:
        flat.SetDefaultPrim(roots[0])
    p.parent.mkdir(parents=True, exist_ok=True)
    # Reserve filename first to prevent accidental overwrite across sessions.
    with p.open('xb'):
        pass
    try:
        if not layer.Export(p.as_posix()):
            raise RuntimeError('USD export failed')
        check = Usd.Stage.Open(p.as_posix())
        if not check or not list(check.Traverse()):
            raise RuntimeError('Published USD has no prims')
    except Exception:
        p.unlink(missing_ok=True)
        raise
    return p

def register_catalog(usd_path, catalog_path, label, tags='', thumbnail=''):
    import hou
    p = Path(usd_path)
    if p.suffix.lower() not in core.USD:
        raise ValueError('Only USD files can be registered in the standard Asset Catalog')
    _source_prim(p)  # A catalog asset must be referenceable, not an ambiguous scene.
    catalog = Path(catalog_path)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    source = hou.AssetGalleryDataSource(catalog.as_posix())
    if not source.isValid() or source.isReadOnly():
        raise RuntimeError('Asset Catalog is not writable: ' + str(catalog))
    key = core.path_key(p)
    for item in source.itemIds():
        existing = source.filePath(item)
        if existing and core.path_key(existing) == key:
            return item, False
    if catalog.exists():
        # Back up through SQLite to include committed WAL state.
        import sqlite3
        backup = catalog.with_name(catalog.stem + '_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + catalog.suffix)
        src = sqlite3.connect(str(catalog)); dst = sqlite3.connect(str(backup))
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
    thumb = Path(thumbnail).read_bytes() if thumbnail and Path(thumbnail).is_file() else b''
    source.startTransaction()
    try:
        item = source.addItem(label, p.as_posix(), thumbnail=thumb)
        if not item:
            raise RuntimeError('Asset Catalog rejected the item')
        source.setOwnsFile(item, False)
        for tag in tags.replace(',', ' ').split():
            source.addTag(item, tag)
        source.endTransaction(True)
    except Exception:
        source.endTransaction(False)
        raise
    return item, True
