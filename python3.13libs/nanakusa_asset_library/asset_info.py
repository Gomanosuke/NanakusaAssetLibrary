"""Read asset statistics in a separate hython process, never in the live HIP."""
from pathlib import Path
import json
import sys


def inspect(source, kind):
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
    import hou
    from pxr import Usd, UsdGeom, UsdShade
    network = None
    try:
        if kind == 'usd':
            stage = Usd.Stage.Open(str(source))
        else:
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
        lod_levels = None
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            prims += 1
            if prim.IsA(UsdGeom.Mesh):
                mesh = UsdGeom.Mesh(prim)
                meshes += 1
                faces += len(mesh.GetFaceVertexCountsAttr().Get(time) or [])
                points += len(mesh.GetPointsAttr().Get(time) or [])
                if UsdGeom.Imageable(prim).ComputePurpose() == UsdGeom.Tokens.proxy:
                    has_proxy = True
                if lod_levels is None and prim.GetVariantSets().HasVariantSet('lod'):
                    lod_levels = len(prim.GetVariantSets().GetVariantSet('lod').GetVariantNames())
            elif prim.GetTypeName() == 'Volume':
                volumes += 1
            elif prim.IsA(UsdShade.Material):
                materials += 1
        result = {'Polygons': f'{faces:,}', 'Points': f'{points:,}', 'Meshes': meshes}
        if volumes:
            result['Volumes'] = volumes
        if kind == 'usd':
            result['USD prims'] = prims
            result['Proxy'] = 'Yes' if has_proxy else 'No'
            result['LOD'] = f'Yes ({lod_levels} levels)' if lod_levels else 'No'
            result['Up axis'] = UsdGeom.GetStageUpAxis(stage)
            if materials:
                result['Materials'] = materials
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
    try:
        result = {'info': inspect(sys.argv[1], sys.argv[2])}
    except Exception as exc:
        result = {'error': str(exc)}
    print('NAL_INFO:' + json.dumps(result))
