from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library.asset_info import inspect


class AssetInfoTests(unittest.TestCase):
    def test_image_headers_include_tiff_and_hdr(self):
        import OpenImageIO as oiio
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('tif','hdr'):
                path=Path(folder)/('image.'+suffix)
                image=oiio.ImageBuf(oiio.ImageSpec(17,9,3,oiio.FLOAT))
                self.assertTrue(image.write(str(path)))
                info=inspect(path,'texture')
                self.assertEqual(info['Resolution'],'17 x 9')
                self.assertEqual(info['Channels'],3)

    def test_model_and_usd_face_counts(self):
        from pxr import Usd,UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'mesh.obj'
            model.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            info=inspect(model,'model')
            self.assertEqual(info['Polygons'],'1');self.assertEqual(info['Points'],'3')
            path=Path(folder)/'mesh.usda';stage=Usd.Stage.CreateNew(str(path))
            mesh=UsdGeom.Mesh.Define(stage,'/mesh')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)])
            mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
            stage.GetRootLayer().Save()
            self.assertEqual(inspect(path,'usd')['Polygons'],'1')

    def test_usd_reports_proxy_presence_up_axis_materials_and_size(self):
        from pxr import Usd,UsdGeom,UsdShade
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'asset.usda';stage=Usd.Stage.CreateNew(str(path))
            UsdGeom.SetStageUpAxis(stage,'Z')
            xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            mesh.CreatePointsAttr([(0,0,0),(2,0,0),(2,4,0),(0,4,0)])
            mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
            UsdGeom.Imageable(mesh.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
            UsdShade.Material.Define(stage,'/Asset/mtl')
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            info=inspect(path,'usd')
            self.assertEqual(info['Proxy'],'No')
            self.assertEqual(info['Up axis'],'Z')
            self.assertEqual(info['Materials'],1)
            self.assertEqual(info['Size'],'2.00 x 4.00 x 0.00')

            proxy=UsdGeom.Mesh.Define(stage,'/Asset/geo_proxy')
            proxy.CreatePointsAttr([(0,0,0),(2,0,0),(2,4,0),(0,4,0)])
            proxy.CreateFaceVertexCountsAttr([4]);proxy.CreateFaceVertexIndicesAttr([0,1,2,3])
            UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
            stage.GetRootLayer().Save()
            self.assertEqual(inspect(path,'usd')['Proxy'],'Yes')

    def test_empty_usd_reports_no_size(self):
        from pxr import Usd,UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.usda';stage=Usd.Stage.CreateNew(str(path))
            UsdGeom.Xform.Define(stage,'/Asset');stage.GetRootLayer().Save()
            info=inspect(path,'usd')
            self.assertNotIn('Size',info);self.assertNotIn('Materials',info);self.assertEqual(info['Proxy'],'No')

if __name__=='__main__':unittest.main()
