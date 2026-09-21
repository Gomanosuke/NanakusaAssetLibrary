from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, Gf
from unittest.mock import patch
from nanakusa_asset_library import thumbnail_scene
from nanakusa_asset_library.thumbnail_scene import prepare


class ThumbnailTests(unittest.TestCase):
    def test_camera_frames_translated_z_up_geometry_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(thumbnail_scene,'ENVIRONMENT',Path(folder)/'missing.exr'):
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
            dome=result.GetPrimAtPath(str(camera.GetPath().GetParentPath())+'/domelight')
            self.assertEqual(dome.GetTypeName(),'DomeLight')
            self.assertEqual(dome.GetAttribute('inputs:intensity').Get(),1)
            self.assertEqual(dome.GetAttribute('inputs:exposure').Get(),0)
            self.assertEqual(tuple(dome.GetAttribute('inputs:color').Get()),(1,1,1))
            self.assertEqual(source.read_bytes(),before)   # no HDR available: Houdini's default white dome

    def lights(self,up,environment):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'asset.usda';dest=Path(folder)/'preview.usda'
            stage=Usd.Stage.CreateNew(str(source));UsdGeom.SetStageUpAxis(stage,up);UsdGeom.Cube.Define(stage,'/asset');stage.GetRootLayer().Save()
            with patch.object(thumbnail_scene,'ENVIRONMENT',environment(folder)):
                info=prepare(source,'usd',dest)
            result=Usd.Stage.Open(str(dest));base=str(UsdGeom.Camera(result.GetPrimAtPath(info['camera'])).GetPath().GetParentPath())
            # Read the values inside the temporary folder while the stage is alive.
            dome,key=result.GetPrimAtPath(base+'/domelight'),result.GetPrimAtPath(base+'/key')
            read=lambda prim,name:prim.GetAttribute(name).Get()
            return info,{n:read(dome,n) for n in ('inputs:exposure','inputs:intensity','inputs:texture:file','inputs:texture:format','xformOp:rotateXYZ')},{n:read(key,n) for n in ('inputs:exposure',)},(dome.GetTypeName(),key.GetTypeName())

    def test_bundled_hdr_lights_the_scene_and_is_rotated_around_the_up_axis(self):
        def fake(folder):
            path=Path(folder)/'meadow_2_8k.exr';path.write_bytes(b'x');return path
        for up,expected in (('Y',(0,-30,0)),('Z',(0,0,-30))):
            info,dome,key,types=self.lights(up,fake)
            self.assertTrue(info['environment'].endswith('meadow_2_8k.exr'))
            self.assertTrue(dome['inputs:texture:file'].path.endswith('meadow_2_8k.exr'))
            self.assertEqual(dome['inputs:texture:format'],'latlong')
            self.assertEqual((dome['inputs:exposure'],dome['inputs:intensity']),(-0.5,1))
            self.assertEqual(tuple(dome['xformOp:rotateXYZ']),expected)
            self.assertEqual(types,('DomeLight','DistantLight'));self.assertEqual(key['inputs:exposure'],1)

    def test_missing_hdr_falls_back_to_the_default_dome_light(self):
        info,dome,key,types=self.lights('Y',lambda folder:Path(folder)/'absent.exr')
        self.assertEqual(info['environment'],'');self.assertEqual(dome['inputs:exposure'],0);self.assertIsNone(dome['inputs:texture:file'])

    def test_environment_is_bundled_next_to_the_module_not_in_the_library(self):
        self.assertEqual(thumbnail_scene.ENVIRONMENT.parent,Path(thumbnail_scene.__file__).parent/'resources')
        self.assertEqual(thumbnail_scene.ENVIRONMENT.name,'meadow_2_8k.exr')

    def test_empty_stage_fails_instead_of_saving_blank_thumbnail(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'empty.usda';stage=Usd.Stage.CreateNew(str(source));stage.GetRootLayer().Save()
            with self.assertRaisesRegex(ValueError,'No visible geometry'):
                prepare(source,'usd',Path(folder)/'preview.usda')

if __name__=='__main__':unittest.main()
