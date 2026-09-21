"""Standalone hython worker: prepare a framed stage without touching the open HIP."""
from pathlib import Path
import math
import sys


def prepare(source, kind, destination):
    import hou
    from pxr import Usd, UsdGeom, UsdLux, Gf
    from nanakusa_asset_library import houdini_ops

    destination = Path(destination)
    if kind == 'usd':
        stage = Usd.Stage.CreateNew(str(destination))
        stage.GetRootLayer().subLayerPaths = [Path(source).resolve().as_posix()]
    else:
        network = hou.node('/obj').createNode('lopnet', 'thumbnail')
        node = houdini_ops.import_asset(source, 'model', 'asset', network.path())
        node.cook(force=True)
        if node.errors():
            raise RuntimeError('; '.join(node.errors()))
        node.stage().Flatten().Export(str(destination))
        stage = Usd.Stage.Open(str(destination))
    # Evaluate the first authored frame, including animated extents.
    frame = stage.GetStartTimeCode() if stage.HasAuthoredTimeCodeRange() else 1
    bounds = UsdGeom.BBoxCache(Usd.TimeCode(frame), ['default', 'render', 'proxy'], useExtentsHint=True)
    box = bounds.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    if box.IsEmpty():
        raise ValueError('No visible geometry bounds. Check payloads and dependencies.')
    center = box.GetMidpoint()
    radius = box.GetSize().GetLength() * .5
    if not math.isfinite(radius) or radius <= 1e-8:
        raise ValueError('Geometry has empty or invalid bounds')
    up = Gf.Vec3d(0, 0, 1) if UsdGeom.GetStageUpAxis(stage) == 'Z' else Gf.Vec3d(0, 1, 0)
    direction = Gf.Vec3d(1, -1.6, .8) if up[2] else Gf.Vec3d(1, .65, 1.6)
    # Fit a bounding sphere inside the narrower vertical field of view.
    distance = radius / math.sin(math.atan(12 / 50)) * 1.12
    eye = center + direction.GetNormalized() * distance
    prefix = '/NanakusaPreview'
    while stage.GetPrimAtPath(prefix):
        prefix += '_'
    camera = UsdGeom.Camera.Define(stage, prefix + '/camera')
    camera.CreateFocalLengthAttr(50)
    camera.CreateHorizontalApertureAttr(24)
    camera.CreateVerticalApertureAttr(24)
    camera.CreateClippingRangeAttr(Gf.Vec2f(max(radius*.0001, 1e-5), distance + radius*10))
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, center, up).GetInverse())
    dome = UsdLux.DomeLight.Define(stage, prefix + '/domelight')
    # Match Houdini's default Dome Light: white, intensity 1, exposure 0.
    dome.CreateIntensityAttr(1)
    dome.CreateExposureAttr(0)
    dome.CreateColorAttr(Gf.Vec3f(1))
    key = UsdLux.DistantLight.Define(stage, prefix + '/key')
    key.CreateIntensityAttr(2)
    key.CreateAngleAttr(15)
    UsdGeom.Xformable(key).AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, center, up).GetInverse())
    stage.GetRootLayer().Save()
    return {'camera': str(camera.GetPath()), 'frame': frame}


if __name__ == '__main__':
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    result = prepare(sys.argv[1], sys.argv[2], sys.argv[3])
    Path(sys.argv[4]).write_text(json.dumps(result), encoding='utf-8')
