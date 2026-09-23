"""Read asset statistics in a separate process, never in the live HIP.

Textures and USD are read first by Houdini's plain Python ("plain" mode, about 0.2 s instead of
hython's 1.7 s start). That interpreter lacks Houdini's USD plugins, so a stage that references
Houdini formats (.bgeo, ...) reports composition errors there; the panel then asks hython.
3DModel files always need hython (they are imported through a LOP network).
"""
from pathlib import Path
import json
import os
import sys


class NeedsHython(Exception):
    """Plain Python cannot read this asset faithfully; retry in hython."""


def plain_setup():
    """Make pxr / OpenImageIO importable from Houdini's plain Python (HFS and PATH come from the panel)."""
    home = Path(os.environ['HFS'])
    sys.path.insert(0, str(home / 'houdini' / ('python%d.%dlibs' % sys.version_info[:2])))
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(str(home / 'bin'))


def inspect(source, kind, plain=False):
    if kind == 'texture':
        import OpenImageIO as oiio
        image = oiio.ImageInput.open(str(source))
        if image is None:
            raise ValueError('Could not read image header')
        try:
            spec = image.spec()
            return {'Resolution': f'{spec.width:,} x {spec.height:,}',
                    'Channels': spec.nchannels, 'Pixel type': str(spec.format)}
        finally:
            image.close()
    if plain and kind != 'usd':
        raise NeedsHython(kind)
    from pxr import Usd, UsdGeom, UsdShade
    network = None
    try:
        if kind == 'usd':
            stage = Usd.Stage.Open(str(source))
            if plain and (stage is None or stage.GetCompositionErrors()):
                raise NeedsHython('composition errors without Houdini plugins')
        else:
            import hou
            from nanakusa_asset_library import houdini_ops
            network = hou.node('/obj').createNode('lopnet', 'asset_info')
            node = houdini_ops.import_asset(source, 'model', 'asset', network.path())
            node.cook(force=True)
            if node.errors():
                raise RuntimeError('; '.join(node.errors()))
            stage = node.stage()
        if stage is None:
            raise ValueError('Could not open asset stage')
        frame = stage.GetStartTimeCode() if stage.HasAuthoredTimeCodeRange() else 1
        time = Usd.TimeCode(frame)
        meshes = faces = points = volumes = prims = materials = 0
        has_proxy = False
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            prims += 1
            if prim.IsA(UsdGeom.Mesh):
                mesh = UsdGeom.Mesh(prim)
                meshes += 1
                faces += len(mesh.GetFaceVertexCountsAttr().Get(time) or [])
                points += len(mesh.GetPointsAttr().Get(time) or [])
                if UsdGeom.Imageable(prim).ComputePurpose() == UsdGeom.Tokens.proxy:
                    has_proxy = True
            elif prim.GetTypeName() == 'Volume':
                volumes += 1
            elif prim.IsA(UsdShade.Material) and prim.GetName() != 'NAL_proxy_look':   # proxy_gen.PROXY_LOOK: not one of the asset's own
                materials += 1
        result = {'Polygons': f'{faces:,}', 'Points': f'{points:,}', 'Meshes': meshes}
        if volumes:
            result['Volumes'] = volumes
        if kind == 'usd':
            result['USD prims'] = prims
            result['Proxy'] = 'Yes' if has_proxy else 'No'
            result['Up axis'] = UsdGeom.GetStageUpAxis(stage)
            if materials:
                result['Materials'] = materials
            # Variant sets a placement can switch (on the prim a Reference / Stage Manager places):
            # "LOD" from lod_gen.py, "element" from element_gen.py, or the asset's own.
            top = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()), None)
            if top:
                sets = top.GetVariantSets()
                names = ['%s (%d)' % (n, len(sets.GetVariantSet(n).GetVariantNames())) for n in sets.GetNames()]
                if names:
                    result['Variant sets'] = ', '.join(names)
            bounds = UsdGeom.BBoxCache(time, ['default', 'render'], useExtentsHint=True)
            box = bounds.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            if not box.IsEmpty():
                size = box.GetSize()
                result['Size'] = f'{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f}'
        result['Sample frame'] = f'{frame:g}'
        return result
    finally:
        if network is not None:
            network.destroy()


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    plain = sys.argv[3:4] == ['plain']
    try:
        if plain:
            plain_setup()
        result = {'info': inspect(sys.argv[1], sys.argv[2], plain)}
    except Exception as exc:
        # In plain mode any failure (a missing plugin, a DLL that did not load) is retried in hython.
        result = {'retry': str(exc)} if plain else {'error': str(exc)}
    print('NAL_INFO:' + json.dumps(result))
