"""Run using Houdini 22 hython in a fresh process, never in a production HIP."""
from pathlib import Path
import sys
import tempfile
import unittest
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
import hou
from pxr import Usd, UsdGeom, UsdShade
from hutil.PySide import QtGui
from solaris_asset_library import houdini_ops as ops

class HoudiniTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.net=hou.node('/obj').createNode('lopnet','sal_test')
        self.image=self.root/'color.png'
        img=QtGui.QImage(16,16,QtGui.QImage.Format.Format_RGBA8888); img.fill(QtGui.QColor(128,100,60,128)); img.save(str(self.image))
        self.obj=self.root/'triangle.obj'
        self.obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')

    def tearDown(self):
        self.net.destroy(); self.temp.cleanup()

    def load(self,path,kind,**kw):return ops.import_asset(path,kind,'test_asset',self.net.path(),**kw)

    def test_model_and_publish_reference(self):
        node=self.load(self.obj,'model')
        self.assertTrue(any(p.IsA(UsdGeom.Mesh) for p in node.stage().Traverse()))
        dest=self.root/'triangle.usd'; ops.export_stage(node,dest)
        published=Usd.Stage.Open(str(dest))
        self.assertTrue(published.GetDefaultPrim())
        ref=self.load(dest,'usd')
        self.assertTrue(any(p.IsA(UsdGeom.Mesh) for p in ref.stage().Traverse()))
        with self.assertRaises(FileExistsError):ops.export_stage(node,dest)

    def test_hdri(self):
        node=self.load(self.image,'hdri')
        domes=[p for p in node.stage().Traverse() if p.GetTypeName().startswith('DomeLight')]
        self.assertEqual(len(domes),1)
        self.assertEqual(domes[0].GetAttribute('inputs:texture:file').Get().path,self.image.as_posix())

    def test_pbr_material_and_binding(self):
        maps={key:self.image.name for key in ('base_color','roughness','metalness','normal','opacity','displacement','emission')}
        manifest=self.root/'wood.pbr.json'; manifest.write_text(json.dumps({'schema':1,'maps':maps}))
        geo=self.load(self.obj,'model')
        node=self.load(manifest,'pbr',upstream=geo,assign_pattern='/assets/**')
        stage=node.stage()
        self.assertTrue(any(p.IsA(UsdShade.Material) for p in stage.Traverse()))
        mesh=next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
        self.assertTrue(UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0])
        self.assertEqual(node.errors(),())

    def test_decal_card(self):
        node=self.load(self.image,'decal')
        mesh=next(p for p in node.stage().Traverse() if p.IsA(UsdGeom.Mesh))
        self.assertTrue(mesh.GetAttribute('primvars:st'))
        self.assertTrue(UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0])
        dest=self.root/'decal.usd'; ops.export_stage(node,dest)
        ref=self.load(dest,'usd')
        mesh=next(p for p in ref.stage().Traverse() if p.IsA(UsdGeom.Mesh))
        self.assertTrue(UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0])

    def test_catalog_no_duplicates(self):
        usd=self.root/'cube.usda'; stage=Usd.Stage.CreateNew(str(usd)); prim=UsdGeom.Cube.Define(stage,'/cube'); stage.SetDefaultPrim(prim.GetPrim()); stage.GetRootLayer().Save()
        catalog=self.root/'catalog.db'
        aid,created=ops.register_catalog(usd,catalog,'Cube','test',str(self.image)); self.assertTrue(created)
        bid,created=ops.register_catalog(usd,catalog,'Cube'); self.assertFalse(created); self.assertEqual(aid,bid)
        source=hou.AssetGalleryDataSource(str(catalog)); self.assertFalse(source.ownsFile(aid))
        self.assertEqual(len(source.itemIds()),1)

    def test_invalid_import_rolls_back(self):
        count=len(self.net.children())
        bad=self.root/'bad.pbr.json'; bad.write_text('{}')
        with self.assertRaises(ValueError):self.load(bad,'pbr')
        self.assertEqual(len(self.net.children()),count)

    def test_ambiguous_usd_requires_sublayer(self):
        p=self.root/'two.usda'; s=Usd.Stage.CreateNew(str(p)); UsdGeom.Cube.Define(s,'/a'); UsdGeom.Cube.Define(s,'/b'); s.GetRootLayer().Save()
        with self.assertRaises(ValueError):self.load(p,'usd')
        node=self.load(p,'usd',usd_mode='sublayer')
        self.assertTrue(node.stage().GetPrimAtPath('/a')); self.assertTrue(node.stage().GetPrimAtPath('/b'))

    def test_materialx_document(self):
        import MaterialX as mx
        doc=mx.createDocument()
        shader=doc.addNode('standard_surface','surface','surfaceshader')
        shader.addInput('base_color','color3').setValue(mx.Color3(.2,.4,.6))
        doc.addMaterialNode('material',shader)
        path=self.root/'surface.mtlx'; mx.writeToXmlFile(doc,str(path))
        geo=self.load(self.obj,'model')
        node=self.load(path,'material',upstream=geo,assign_pattern='/assets/**')
        self.assertTrue(any(p.IsA(UsdShade.Material) for p in node.stage().Traverse()))
        mesh=next(p for p in node.stage().Traverse() if p.IsA(UsdGeom.Mesh))
        self.assertTrue(UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()[0])

if __name__=='__main__':unittest.main()
