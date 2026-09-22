from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, UsdUtils
from nanakusa_asset_library.element_gen import generate, remove


def triangle(stage, path):
    mesh=UsdGeom.Mesh.Define(stage,path)
    mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
    return mesh


class ElementGenTests(unittest.TestCase):
    def test_a_pack_of_alternate_objects_gets_an_element_switch_showing_only_the_first(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'pack.usda'
            stage=Usd.Stage.CreateNew(str(src));root=UsdGeom.Xform.Define(stage,'/Root')
            for name in ('mushroom_a','mushroom_b','mushroom_c'):
                group=UsdGeom.Xform.Define(stage,f'/Root/{name}')
                triangle(stage,f'/Root/{name}/geo')
            stage.SetDefaultPrim(root.GetPrim());stage.GetRootLayer().Save()

            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result['element_switch']['prim'],'/Root')
            self.assertEqual(result['element_switch']['variants'],['Element0','Element1','Element2'])

            out=Usd.Stage.Open(str(Path(folder)/'out.usda'))
            root_out=out.GetPrimAtPath('/Root')
            vs=root_out.GetVariantSet('element')
            self.assertEqual(vs.GetVariantSelection(),'Element0')   # first element shown by default
            names=['mushroom_a','mushroom_b','mushroom_c']
            for index,selected in enumerate(names):
                vs.SetVariantSelection(f'Element{index}')
                visible=[n for n in names if UsdGeom.Imageable(out.GetPrimAtPath(f'/Root/{n}')).ComputeVisibility()!='invisible']
                self.assertEqual(visible,[selected])

    def test_a_render_mesh_and_its_own_proxy_sibling_are_not_mistaken_for_a_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=triangle(stage,'/Asset/geo')
            UsdGeom.Imageable(mesh.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
            proxy=triangle(stage,'/Asset/geo_proxy')
            UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'no multi-object pack found (need 2+ sibling objects with geometry)'})

    def test_single_object_asset_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            triangle(stage,'/Asset/geo')
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'no multi-object pack found (need 2+ sibling objects with geometry)'})

    def test_asset_with_an_existing_element_switch_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'pack.usda'
            stage=Usd.Stage.CreateNew(str(src));root=UsdGeom.Xform.Define(stage,'/Root')
            for name in ('a','b'):
                triangle(stage,f'/Root/{name}')
            vset=root.GetPrim().GetVariantSets().AddVariantSet('element')
            vset.AddVariant('Element0');vset.SetVariantSelection('Element0')
            stage.SetDefaultPrim(root.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'already has an element switch'})

    def test_the_shallowest_branch_point_is_used_not_a_deeper_one(self):
        # A pack root that itself has a single child before branching (a common wrapper Xform)
        # must not be mistaken for the branch point; the branch is one level deeper.
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'pack.usda'
            stage=Usd.Stage.CreateNew(str(src))
            wrapper=UsdGeom.Xform.Define(stage,'/Wrapper')
            branch=UsdGeom.Xform.Define(stage,'/Wrapper/Branch')
            for name in ('a','b'):
                triangle(stage,f'/Wrapper/Branch/{name}')
            stage.SetDefaultPrim(wrapper.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,Path(folder)/'out.usda')
            self.assertEqual(result['element_switch']['prim'],'/Wrapper/Branch')

    def test_remove_undoes_the_switch_completely_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'pack.usda'
            stage=Usd.Stage.CreateNew(str(src));root=UsdGeom.Xform.Define(stage,'/Root')
            for name in ('a','b','c'):
                triangle(stage,f'/Root/{name}')
            stage.SetDefaultPrim(root.GetPrim());stage.GetRootLayer().Save()
            switched=Path(folder)/'switched.usda'
            generate(src,switched)

            unswitched=Path(folder)/'unswitched.usda'
            result=remove(switched,unswitched)
            self.assertEqual(result,{'removed_element_switch':['/Root']})

            out=Usd.Stage.Open(str(unswitched))
            root_out=out.GetPrimAtPath('/Root')
            self.assertFalse(root_out.GetVariantSets().HasVariantSet('element'))
            for name in ('a','b','c'):   # no leftover visibility overrides either
                self.assertFalse(UsdGeom.Imageable(out.GetPrimAtPath(f'/Root/{name}')).GetVisibilityAttr().HasAuthoredValue())

            again=remove(unswitched,Path(folder)/'again.usda')
            self.assertEqual(again,{'skipped':'no element switch found in this file'})

    def test_remove_on_an_asset_without_a_switch_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            triangle(stage,'/Asset/geo')
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=remove(src,Path(folder)/'out.usda')
            self.assertEqual(result,{'skipped':'no element switch found in this file'})


class ElementGenUsdzTests(unittest.TestCase):
    def test_a_usdz_pack_is_switched_and_repackaged(self):
        with tempfile.TemporaryDirectory() as folder:
            src_dir=Path(folder)/'src';src_dir.mkdir()
            layer=src_dir/'pack.usda'
            stage=Usd.Stage.CreateNew(str(layer));root=UsdGeom.Xform.Define(stage,'/Root')
            for name in ('a','b','c'):
                triangle(stage,f'/Root/{name}')
            stage.SetDefaultPrim(root.GetPrim());stage.GetRootLayer().Save()
            usdz=Path(folder)/'pack.usdz'
            self.assertTrue(UsdUtils.CreateNewUsdzPackage(str(layer),str(usdz)))
            before=usdz.read_bytes()

            dest=Path(folder)/'out.usdz'
            result=generate(usdz,dest)
            self.assertEqual(result['element_switch']['variants'],['Element0','Element1','Element2'])
            self.assertEqual(usdz.read_bytes(),before)   # generate() never touches the source .usdz

            check=Usd.Stage.Open(str(dest))
            vs=check.GetPrimAtPath('/Root').GetVariantSet('element')
            self.assertEqual(vs.GetVariantSelection(),'Element0')
            with zipfile.ZipFile(dest) as z:
                self.assertEqual(z.namelist(),['pack.usda'])

            unswitched=Path(folder)/'unswitched.usdz'
            result=remove(dest,unswitched)
            self.assertEqual(result,{'removed_element_switch':['/Root']})
            check2=Usd.Stage.Open(str(unswitched))
            self.assertFalse(check2.GetPrimAtPath('/Root').GetVariantSets().HasVariantSet('element'))


if __name__=='__main__':unittest.main()
