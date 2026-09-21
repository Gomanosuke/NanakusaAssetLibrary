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

if __name__=='__main__':unittest.main()
