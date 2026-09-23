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


def layer_text(path):
    stage=Usd.Stage.Open(str(path))   # keep the stage alive while its layer is read
    return stage.GetRootLayer().ExportToString()


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
            self.assertEqual(result['element_switch']['branch'],'/Wrapper/Branch')
            # the variant set sits on the top (default) prim, so a placement of the asset (Reference,
            # Stage Manager) carries it on the placed prim itself; it still switches the branch's children
            self.assertEqual(result['element_switch']['prim'],'/Wrapper')
            out=Usd.Stage.Open(str(Path(folder)/'out.usda'))
            self.assertFalse(out.GetPrimAtPath('/Wrapper/Branch').GetVariantSets().HasVariantSet('element'))
            vs=out.GetPrimAtPath('/Wrapper').GetVariantSet('element')
            for index,name in enumerate(('a','b')):
                vs.SetVariantSelection(f'Element{index}')
                visible=[n for n in ('a','b') if UsdGeom.Imageable(out.GetPrimAtPath(f'/Wrapper/Branch/{n}')).ComputeVisibility()!='invisible']
                self.assertEqual(visible,[name])

    def _wrapped_pack(self,folder):
        src=Path(folder)/'pack.usda'
        stage=Usd.Stage.CreateNew(str(src));top=UsdGeom.Xform.Define(stage,'/Top')
        UsdGeom.Xform.Define(stage,'/Top/Meshes/Root')
        for name in ('a','b','c'):triangle(stage,f'/Top/Meshes/Root/{name}')
        stage.SetDefaultPrim(top.GetPrim());stage.GetRootLayer().Save()
        return src

    def test_delete_restores_the_original_layer_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self._wrapped_pack(folder);original=layer_text(str(src))
            generate(src,Path(folder)/'switched.usda')
            self.assertEqual(remove(Path(folder)/'switched.usda',Path(folder)/'back.usda'),{'removed_element_switch':['/Top']})
            self.assertEqual(layer_text(Path(folder)/'back.usda'),original)

    def test_an_old_switch_on_the_branch_prim_is_moved_to_the_top_prim_and_still_deletes_cleanly(self):
        from pxr import Sdf
        with tempfile.TemporaryDirectory() as folder:
            src=self._wrapped_pack(folder);original=layer_text(str(src))
            # what versions before 0.19.0 wrote: the variant set on the branch prim itself
            old=Path(folder)/'old.usda';stage=Usd.Stage.Open(str(src))
            vset=stage.GetPrimAtPath('/Top/Meshes/Root').GetVariantSets().AddVariantSet('element')
            for i,chosen in enumerate(('a','b','c')):
                vset.AddVariant(f'Element{i}');vset.SetVariantSelection(f'Element{i}')
                with vset.GetVariantEditContext():
                    for other in ('a','b','c'):
                        UsdGeom.Imageable(stage.GetPrimAtPath(f'/Top/Meshes/Root/{other}')).CreateVisibilityAttr().Set('inherited' if other==chosen else 'invisible')
            vset.SetVariantSelection('Element0');stage.GetRootLayer().Export(str(old))

            moved=Path(folder)/'moved.usda'
            result=generate(old,moved)
            self.assertEqual(result['element_switch']['prim'],'/Top')
            self.assertEqual(result['element_switch']['moved_from'],['/Top/Meshes/Root'])
            out=Usd.Stage.Open(str(moved))
            self.assertEqual([p.GetPath() for p in out.Traverse() if p.GetVariantSets().HasVariantSet('element')],[Sdf.Path('/Top')])
            self.assertEqual(generate(moved,Path(folder)/'again.usda'),{'skipped':'already has an element switch'})
            # identical to a switch added to the untouched asset, and Delete returns the original
            generate(src,Path(folder)/'fresh.usda')
            self.assertEqual(out.GetRootLayer().ExportToString(),layer_text(Path(folder)/'fresh.usda'))
            remove(moved,Path(folder)/'back.usda')
            self.assertEqual(layer_text(Path(folder)/'back.usda'),original)
            # Delete on the old format still works too
            remove(old,Path(folder)/'old_back.usda')
            self.assertEqual(layer_text(Path(folder)/'old_back.usda'),original)

    def test_a_switch_over_a_prim_defined_in_another_layer_leaves_no_empty_over_after_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            base=self._wrapped_pack(folder)
            src=Path(folder)/'wrapper.usda';stage=Usd.Stage.CreateNew(str(src))
            stage.GetRootLayer().subLayerPaths.append(base.name)
            stage.SetDefaultPrim(stage.GetPrimAtPath('/Top'));stage.GetRootLayer().Save()
            original=layer_text(str(src))
            generate(src,Path(folder)/'switched.usda')
            switched=Usd.Stage.Open(str(Path(folder)/'switched.usda'))
            self.assertIsNotNone(switched.GetRootLayer().GetPrimAtPath('/Top'))   # an over held the switch
            remove(Path(folder)/'switched.usda',Path(folder)/'back.usda')
            back=Usd.Stage.Open(str(Path(folder)/'back.usda'))
            self.assertIsNone(back.GetRootLayer().GetPrimAtPath('/Top'));self.assertEqual(back.GetRootLayer().ExportToString(),original)

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
