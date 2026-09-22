from pathlib import Path
import math
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, UsdShade, UsdUtils, Sdf
from nanakusa_asset_library import lod_gen, proxy_gen
from nanakusa_asset_library.lod_gen import generate


def grid_mesh(stage, path, n):
    mesh=UsdGeom.Mesh.Define(stage,path)
    points=[(i,j,math.sin(i*0.3)*math.cos(j*0.3)) for j in range(n) for i in range(n)]
    counts,indices=[],[]
    for j in range(n-1):
        for i in range(n-1):
            a=j*n+i;b=a+1;c=a+n+1;d=a+n
            counts.append(4);indices.extend([a,b,c,d])
    mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr(counts);mesh.CreateFaceVertexIndicesAttr(indices)
    return mesh,proxy_gen._triangle_count(counts)


class LodGenTests(unittest.TestCase):
    def test_levels_cascade_by_the_requested_reduction_and_lod0_is_the_original(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda';dest=Path(folder)/'out.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh,original=grid_mesh(stage,'/Asset/geo',30)
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            source_bytes=src.read_bytes()

            result=generate(src,dest,levels=4,reduction=50.0)
            self.assertEqual(result['lods'][0]['prim'],'/Asset/geo')
            self.assertEqual(src.read_bytes(),source_bytes)   # generate() never touches the source file

            check=Usd.Stage.Open(str(dest))
            prim=check.GetPrimAtPath('/Asset/geo')
            vset=prim.GetVariantSets().GetVariantSet('lod')
            self.assertEqual(vset.GetVariantNames(),['LOD0','LOD1','LOD2','LOD3'])
            self.assertEqual(vset.GetVariantSelection(),'LOD0')   # highest detail selected by default
            cmesh=UsdGeom.Mesh(prim)
            triangles=[]
            for name in vset.GetVariantNames():
                vset.SetVariantSelection(name)
                triangles.append(proxy_gen._triangle_count(cmesh.GetFaceVertexCountsAttr().Get()))
            self.assertEqual(triangles[0],original)
            for i in range(1,4):
                self.assertAlmostEqual(triangles[i],round(original*0.5**i),delta=8)
                self.assertLess(triangles[i],triangles[i-1])   # each level is strictly lighter

    def test_asset_that_already_has_lods_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            grid_mesh(stage,'/Asset/geo',5)
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            generate(src,Path(folder)/'first.usda',levels=2,reduction=50.0)
            result=generate(Path(folder)/'first.usda',Path(folder)/'second.usda',levels=2,reduction=50.0)
            self.assertEqual(result,{'skipped':'already has LODs'})

    def test_asset_with_no_mesh_geometry_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));UsdGeom.Xform.Define(stage,'/Asset');stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda',levels=3,reduction=50.0)
            self.assertEqual(result,{'skipped':'no mesh geometry'})


class LodGenUsdzTests(unittest.TestCase):
    def package(self,folder):
        src_dir=Path(folder)/'src';(src_dir/'textures').mkdir(parents=True)
        (src_dir/'textures'/'albedo.png').write_bytes(b'fakepngdata12345')
        layer=src_dir/'asset.usda'
        stage=Usd.Stage.CreateNew(str(layer));xform=UsdGeom.Xform.Define(stage,'/Asset')
        mesh,_=grid_mesh(stage,'/Asset/geo',20)
        shader=UsdShade.Shader.Define(stage,'/Asset/mtl/tex')
        shader.CreateInput('file',Sdf.ValueTypeNames.Asset).Set('./textures/albedo.png')
        stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
        usdz=Path(folder)/'asset.usdz'
        self.assertTrue(UsdUtils.CreateNewUsdzPackage(str(layer),str(usdz)))
        return usdz

    def test_lods_are_added_and_the_bundled_texture_reference_still_resolves(self):
        with tempfile.TemporaryDirectory() as folder:
            usdz=self.package(folder);before=usdz.read_bytes();dest=Path(folder)/'out.usdz'
            result=generate(usdz,dest,levels=3,reduction=50.0)
            self.assertEqual(result['lods'][0]['prim'],'/Asset/geo')
            self.assertEqual(usdz.read_bytes(),before)
            check=Usd.Stage.Open(str(dest))
            vset=check.GetPrimAtPath('/Asset/geo').GetVariantSets().GetVariantSet('lod')
            self.assertEqual(vset.GetVariantNames(),['LOD0','LOD1','LOD2'])
            texture=check.GetPrimAtPath('/Asset/mtl/tex').GetAttribute('inputs:file').Get()
            self.assertEqual(texture.resolvedPath,str(dest)+'[textures/albedo.png]')
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(sorted(z.namelist()),['asset.usda','textures/albedo.png'])

if __name__=='__main__':unittest.main()
