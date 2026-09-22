from pathlib import Path
import math
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, UsdShade, UsdUtils, Sdf
from nanakusa_asset_library import proxy_gen
from nanakusa_asset_library.proxy_gen import generate


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


class ProxyGenTests(unittest.TestCase):
    def test_small_mesh_is_copied_and_heavy_mesh_is_decimated_to_the_target(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda';dest=Path(folder)/'out.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            small=UsdGeom.Mesh.Define(stage,'/Asset/small')
            small.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
            small.CreateFaceVertexCountsAttr([4]);small.CreateFaceVertexIndicesAttr([0,1,2,3])
            big,before=grid_mesh(stage,'/Asset/big',40)
            self.assertGreater(before,proxy_gen.TARGET_TRIANGLES)
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            source_bytes=src.read_bytes()

            result=generate(src,dest)
            self.assertEqual(sorted(result['proxied']),['/Asset/big','/Asset/small'])
            self.assertEqual(src.read_bytes(),source_bytes)   # generate() never touches the source file

            check=Usd.Stage.Open(str(dest))
            small_o=check.GetPrimAtPath('/Asset/small');small_p=check.GetPrimAtPath('/Asset/small_proxy')
            big_o=check.GetPrimAtPath('/Asset/big');big_p=check.GetPrimAtPath('/Asset/big_proxy')
            self.assertEqual(UsdGeom.Imageable(small_o).ComputePurpose(),'render')
            self.assertEqual(UsdGeom.Imageable(small_p).ComputePurpose(),'proxy')
            self.assertEqual(len(UsdGeom.Mesh(small_p).GetPointsAttr().Get()),4)   # too small to decimate: copied as-is
            self.assertEqual(UsdGeom.Imageable(big_o).ComputePurpose(),'render')
            self.assertEqual(UsdGeom.Imageable(big_p).ComputePurpose(),'proxy')
            after=proxy_gen._triangle_count(UsdGeom.Mesh(big_p).GetFaceVertexCountsAttr().Get())
            self.assertLess(after,before)
            self.assertLessEqual(after,proxy_gen.TARGET_TRIANGLES*1.5)
            self.assertGreater(len(UsdGeom.Mesh(big_p).GetPointsAttr().Get()),0)

    def test_asset_with_an_existing_proxy_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
            proxy=UsdGeom.Mesh.Define(stage,'/Asset/geo_proxy')
            proxy.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);proxy.CreateFaceVertexCountsAttr([3]);proxy.CreateFaceVertexIndicesAttr([0,1,2])
            UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'already has a proxy'})

    def test_asset_with_no_mesh_geometry_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));UsdGeom.Xform.Define(stage,'/Asset');stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'no mesh geometry'})

    def test_sibling_proxy_name_never_collides(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
            UsdGeom.Xform.Define(stage,'/Asset/geo_proxy')   # a name that would otherwise collide
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result['proxied'],['/Asset/geo'])
            check=Usd.Stage.Open(str(Path(folder)/'out.usda'))
            self.assertEqual(check.GetPrimAtPath('/Asset/geo_proxy').GetTypeName(),'Xform')
            self.assertEqual(check.GetPrimAtPath('/Asset/geo_proxy_').GetTypeName(),'Mesh')

class ProxyGenUsdzTests(unittest.TestCase):
    def package(self, folder, extra=lambda stage,xform:None):
        src_dir=Path(folder)/'src';(src_dir/'textures').mkdir(parents=True)
        (src_dir/'textures'/'albedo.png').write_bytes(b'fakepngdata12345')
        layer=src_dir/'asset.usda'
        stage=Usd.Stage.CreateNew(str(layer));xform=UsdGeom.Xform.Define(stage,'/Asset')
        mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
        mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)]);mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
        shader=UsdShade.Shader.Define(stage,'/Asset/mtl/tex')
        shader.CreateInput('file',Sdf.ValueTypeNames.Asset).Set('./textures/albedo.png')
        extra(stage,xform)
        stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
        usdz=Path(folder)/'asset.usdz'
        self.assertTrue(UsdUtils.CreateNewUsdzPackage(str(layer),str(usdz)))
        return usdz

    def test_proxy_is_added_and_the_bundled_texture_reference_still_resolves(self):
        with tempfile.TemporaryDirectory() as folder:
            usdz=self.package(folder)
            before=usdz.read_bytes()
            dest=Path(folder)/'out.usdz'
            result=generate(usdz,dest)
            self.assertEqual(result['proxied'],['/Asset/geo'])
            self.assertEqual(usdz.read_bytes(),before)   # generate() never touches the source .usdz

            check=Usd.Stage.Open(str(dest))
            orig=check.GetPrimAtPath('/Asset/geo');proxy=check.GetPrimAtPath('/Asset/geo_proxy')
            self.assertEqual(UsdGeom.Imageable(orig).ComputePurpose(),'render')
            self.assertEqual(UsdGeom.Imageable(proxy).ComputePurpose(),'proxy')
            texture=check.GetPrimAtPath('/Asset/mtl/tex').GetAttribute('inputs:file').Get()
            self.assertEqual(texture.resolvedPath, str(dest)+'[textures/albedo.png]')
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(sorted(z.namelist()),['asset.usda','textures/albedo.png'])

    def test_usdz_with_an_existing_proxy_is_skipped_without_repackaging(self):
        with tempfile.TemporaryDirectory() as folder:
            def add_proxy(stage,xform):
                proxy=UsdGeom.Mesh.Define(stage,'/Asset/geo_proxy')
                proxy.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)]);proxy.CreateFaceVertexCountsAttr([4]);proxy.CreateFaceVertexIndicesAttr([0,1,2,3])
                UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
            usdz=self.package(folder,add_proxy)
            dest=Path(folder)/'out.usdz'
            result=generate(usdz,dest)
            self.assertEqual(result,{'skipped':'already has a proxy'})
            self.assertFalse(dest.exists())

if __name__=='__main__':unittest.main()
