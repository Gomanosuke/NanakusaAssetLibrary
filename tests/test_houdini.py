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
from nanakusa_asset_library import houdini_ops as ops

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

    def _variant_pack(self):
        # Mirrors real Sketchfab-style packs: the defaultPrim (Top) is a wrapper above the actual
        # branch point (Top/Root), so the reference remap preserves that sub-path - unlike a fixture
        # where the branch point IS the defaultPrim, which would remap onto /assets/test_asset with
        # no extra segment and wouldn't catch a path bug like that.
        p=self.root/'pack.usda'; stage=Usd.Stage.CreateNew(str(p))
        top=UsdGeom.Xform.Define(stage,'/Top'); root=UsdGeom.Xform.Define(stage,'/Top/Root')
        for name in ('a','b','c'):
            UsdGeom.Mesh.Define(stage,f'/Top/Root/{name}')
        vset=root.GetPrim().GetVariantSets().AddVariantSet('element')
        for i,name in enumerate(('a','b','c')):
            vset.AddVariant(f'Element{i}');vset.SetVariantSelection(f'Element{i}')
            with vset.GetVariantEditContext():
                for other in ('a','b','c'):
                    UsdGeom.Imageable(stage.GetPrimAtPath(f'/Top/Root/{other}')).CreateVisibilityAttr().Set('inherited' if other==name else 'invisible')
        vset.SetVariantSelection('Element0')
        stage.SetDefaultPrim(top.GetPrim());stage.GetRootLayer().Save()
        return p

    def test_variant_switch_node_is_added_when_requested_and_the_asset_has_one(self):
        node=self.load(self._variant_pack(),'usd',add_variant_switch=True)
        self.assertEqual(node.type().name(),'setvariant')
        self.assertEqual(node.inputs()[0].type().name(),'reference::2.0')
        self.assertTrue(node.parm('enable1').eval())
        # /Top is the defaultPrim (remapped to /assets/test_asset); /Root is one level deeper.
        self.assertEqual(node.parm('primpattern1').eval(),'/assets/test_asset/Root')
        self.assertEqual(node.parm('variantset1').eval(),'element')
        self.assertTrue(node.parm('variantnameuseindex1').eval())
        self.assertEqual(node.parm('variantnameindex1').eval(),0)
        # the switch is the new terminal node: still exactly one mesh visible downstream of it
        self.assertEqual(UsdGeom.Imageable(node.stage().GetPrimAtPath('/assets/test_asset/Root/a')).ComputeVisibility(),'inherited')
        # laid out straight below its Reference, not beside it
        reference=node.inputs()[0]
        self.assertAlmostEqual(node.position()[0],reference.position()[0])
        self.assertAlmostEqual(node.position()[1],reference.position()[1]-ops.CHAIN_STEP)

    def test_a_generated_switch_is_on_the_referenced_prim_so_set_variant_and_stage_manager_target_it(self):
        from nanakusa_asset_library import element_gen
        switched=self.root/'switched.usda';element_gen.generate(self._variant_pack_without_switch(),switched)
        node=self.load(switched,'usd',add_variant_switch=True)
        self.assertEqual(node.parm('primpattern1').eval(),'/assets/test_asset')   # the Reference's own prim
        # Stage Manager writes variant selections only on the prims it places: that is now enough.
        manager=self.net.createNode('stagemanager');manager.parm('num_changes').set(1)
        manager.parm('change1').set('create');manager.parm('primpath1').set('/placed');manager.parm('reffilepath1').set(str(switched))
        manager.parm('num_variants1').set(1);manager.parm('variantset1_1').set('element');manager.parm('variantname1_1').set('Element2')
        shown=[n for n in ('a','b','c') if UsdGeom.Imageable(manager.stage().GetPrimAtPath(f'/placed/Root/{n}')).ComputeVisibility()!='invisible']
        self.assertEqual(shown,['c'])

    def test_an_asset_with_lods_gets_an_auto_select_lod_that_switches_by_camera_distance(self):
        from nanakusa_asset_library import lod_gen
        src=self._variant_pack_without_switch();lods=self.root/'lods.usda';lod_gen.generate(src,lods,3,50)
        camera=self.net.createNode('camera');camera.parm('primpath').set('/cameras/cam')
        node=ops.import_asset(lods,'usd','test_asset',self.net.path(),upstream=camera,add_lod_select=True)
        self.assertEqual(node.type().name(),'autoselectlod')
        self.assertEqual(node.parm('primpattern').eval(),'/assets/test_asset')
        self.assertEqual(node.parm('variantset1').eval(),'LOD');self.assertEqual(node.parm('numoflods').eval(),3)
        self.assertEqual(node.parm('camera').eval(),'/cameras/cam')   # the camera found upstream
        distances=[node.parm(f'thresh_dist{i}').eval() for i in (1,2,3)]
        self.assertEqual(distances[0],0.0);self.assertLess(distances[1],distances[2])
        self.assertEqual(node.inputs()[0].type().name(),'reference::2.0')
        self.assertAlmostEqual(node.position()[1],node.inputs()[0].position()[1]-ops.CHAIN_STEP)   # straight below
        def picked(z):
            camera.parm('tz').set(z)
            return node.stage().GetPrimAtPath('/assets/test_asset').GetVariantSet('LOD').GetVariantSelection()
        self.assertEqual(picked(0.0),'LOD_1')
        self.assertEqual(picked((distances[1]+distances[2])/2),'LOD_2')
        self.assertEqual(picked(distances[2]*2),'LOD_3')
        # without the flag (no "lod" tag) nothing is added
        self.assertEqual(ops.import_asset(lods,'usd','plain',self.net.path()).type().name(),'reference::2.0')

    def _variant_pack_without_switch(self):
        p=self.root/'plain_pack.usda';stage=Usd.Stage.CreateNew(str(p))
        top=UsdGeom.Xform.Define(stage,'/Top');UsdGeom.Xform.Define(stage,'/Top/Root')
        for name in ('a','b','c'):
            mesh=UsdGeom.Mesh.Define(stage,f'/Top/Root/{name}')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
        stage.SetDefaultPrim(top.GetPrim());stage.GetRootLayer().Save()
        return p

    def test_variant_switch_node_is_not_added_without_a_variant_or_without_the_flag(self):
        node=self.load(self.obj,'model')   # unrelated kind: never eligible
        plain=self.root/'plain.usda'; s=Usd.Stage.CreateNew(str(plain)); x=UsdGeom.Xform.Define(s,'/x'); s.SetDefaultPrim(x.GetPrim()); s.GetRootLayer().Save()
        node=self.load(plain,'usd',add_variant_switch=True)   # asked for, but nothing to switch
        self.assertEqual(node.type().name(),'reference::2.0')
        node=self.load(self._variant_pack(),'usd')   # has a variant, but not opted in
        self.assertEqual(node.type().name(),'reference::2.0')

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
