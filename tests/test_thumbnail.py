from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, Gf
from nanakusa_asset_library.thumbnail_scene import prepare


class ThumbnailTests(unittest.TestCase):
    def test_camera_frames_translated_z_up_geometry_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'asset.usda';dest=Path(folder)/'preview.usda'
            stage=Usd.Stage.CreateNew(str(source));UsdGeom.SetStageUpAxis(stage,'Z')
            cube=UsdGeom.Cube.Define(stage,'/asset');cube.AddTranslateOp().Set((50,20,100))
            stage.GetRootLayer().Save();before=source.read_bytes()
            info=prepare(source,'usd',dest)
            result=Usd.Stage.Open(str(dest));camera=UsdGeom.Camera(result.GetPrimAtPath(info['camera']))
            view=camera.GetCamera(1).transform.GetInverse()
            point=view.Transform(Gf.Vec3d(50,20,100))
            self.assertAlmostEqual(point[0],0,places=5);self.assertAlmostEqual(point[1],0,places=5)
            self.assertLess(point[2],0)
            self.assertEqual(source.read_bytes(),before)

    def test_empty_stage_fails_instead_of_saving_blank_thumbnail(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'empty.usda';stage=Usd.Stage.CreateNew(str(source));stage.GetRootLayer().Save()
            with self.assertRaisesRegex(ValueError,'No visible geometry'):
                prepare(source,'usd',Path(folder)/'preview.usda')

if __name__=='__main__':unittest.main()
