from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from pxr import Usd, UsdGeom, UsdShade, Sdf, UsdUtils
from nanakusa_asset_library import lod_gen, element_gen


def layer_text(path):
    stage=Usd.Stage.Open(str(path))   # keep the stage alive while its layer is read
    return stage.GetRootLayer().ExportToString()


def grid(stage, path, n=20, proxy=True):
    """An n x n quad grid with per-point UVs (st0) and normals, a bound material and (like every
    library asset) a purpose=proxy sibling."""
    points=[(x/n,0.0,z/n) for z in range(n+1) for x in range(n+1)]
    counts=[4]*(n*n);indices=[]
    for z in range(n):
        for x in range(n):
            a=z*(n+1)+x;indices+=[a,a+n+1,a+n+2,a+1]
    mesh=UsdGeom.Mesh.Define(stage,path)
    mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr(counts);mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateNormalsAttr([(0,1,0)]*len(points));mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st0',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set([(p[0],p[2]) for p in points])
    UsdGeom.Imageable(mesh).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
    material=UsdShade.Material.Define(stage,'/Top/Looks/mat')
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    if proxy:
        p=UsdGeom.Mesh.Define(stage,path+'_proxy');p.CreatePointsAttr(points[:3]);p.CreateFaceVertexCountsAttr([3]);p.CreateFaceVertexIndicesAttr([0,1,2])
        UsdGeom.Imageable(p).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
    return mesh


def shown(stage, root='/Top'):
    """{mesh name: triangles} of the render meshes currently visible under `root`."""
    out={}
    for p in Usd.PrimRange(stage.GetPrimAtPath(root)):
        if p.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(p).ComputeVisibility()!='invisible' and UsdGeom.Imageable(p).ComputePurpose()!='proxy':
            out[p.GetName()]=sum(c-2 for c in UsdGeom.Mesh(p).GetFaceVertexCountsAttr().Get())
    return out


class LodTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.dir=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def asset(self,name='asset.usda'):
        src=self.dir/name;stage=Usd.Stage.CreateNew(str(src))
        top=UsdGeom.Xform.Define(stage,'/Top');UsdGeom.Xform.Define(stage,'/Top/Geo')
        grid(stage,'/Top/Geo/body')
        stage.SetDefaultPrim(top.GetPrim());stage.GetRootLayer().Save()
        return src

    def test_lod_variants_sit_on_the_top_prim_and_show_reduced_copies_with_uvs_normals_and_material(self):
        src=self.asset();out=self.dir/'lod.usda'
        result=lod_gen.generate(src,out,3,50)
        self.assertEqual(result['lods']['prim'],'/Top');self.assertEqual(result['lods']['variants'],['LOD_1','LOD_2','LOD_3'])
        self.assertEqual(result['lods']['triangles'][0],800)
        stage=Usd.Stage.Open(str(out));stage.SetEditTarget(stage.GetSessionLayer())   # selections stay out of the file
        vs=stage.GetPrimAtPath('/Top').GetVariantSet('LOD')
        self.assertEqual(vs.GetVariantSelection(),'LOD_1')   # full detail unless something picks another
        self.assertEqual(shown(stage),{'body':800})
        previous=800
        for level in ('LOD_2','LOD_3'):
            vs.SetVariantSelection(level)
            visible=shown(stage);self.assertEqual(list(visible),['body_'+level])
            self.assertLess(visible['body_'+level],previous);previous=visible['body_'+level]
            lod=UsdGeom.Mesh(stage.GetPrimAtPath('/Top/Geo/body_'+level))
            count=len(lod.GetPointsAttr().Get())
            st=UsdGeom.PrimvarsAPI(lod).GetPrimvar('st0')
            self.assertEqual(st.GetInterpolation(),UsdGeom.Tokens.vertex);self.assertEqual(len(st.Get()),count)
            self.assertEqual(st.GetTypeName(),Sdf.ValueTypeNames.TexCoord2fArray)
            self.assertEqual(lod.GetNormalsInterpolation(),UsdGeom.Tokens.vertex);self.assertEqual(len(lod.GetNormalsAttr().Get()),count)
            # recomputed on the reduced surface, facing the same way as the original (+Y for this grid)
            self.assertTrue(all(abs(n[1]-1.0)<1e-4 for n in lod.GetNormalsAttr().Get()))
            self.assertLessEqual(max(i for i in lod.GetFaceVertexIndicesAttr().Get()),count-1)
            bound,_=UsdShade.MaterialBindingAPI(lod.GetPrim()).ComputeBoundMaterial()
            self.assertEqual(str(bound.GetPath()),'/Top/Looks/mat')
            self.assertTrue(lod.GetExtentAttr().HasAuthoredValue())
            self.assertTrue(stage.GetPrimAtPath('/Top/Geo/body_proxy').IsValid())   # the viewport proxy stays
        # Houdini's own LOD tools read the set on the placed prim: a reference carries it there.
        placed=Usd.Stage.CreateInMemory();placed.SetEditTarget(placed.GetSessionLayer());ref=placed.DefinePrim('/placed');ref.GetReferences().AddReference(str(out))
        self.assertIn('LOD',ref.GetVariantSets().GetNames())
        ref.GetVariantSet('LOD').SetVariantSelection('LOD_3')
        self.assertEqual(list(shown(placed,'/placed')),['body_LOD_3'])

    def test_delete_restores_the_original_layer_exactly(self):
        src=self.asset();original=layer_text(src)
        lod_gen.generate(src,self.dir/'lod.usda',4,50)
        self.assertEqual(lod_gen.remove(self.dir/'lod.usda',self.dir/'back.usda'),{'removed_lods':['/Top']})
        self.assertEqual(layer_text(self.dir/'back.usda'),original)
        self.assertEqual(lod_gen.remove(self.dir/'back.usda',self.dir/'again.usda'),{'skipped':'no LODs found in this file'})

    def test_refusals_leave_the_asset_alone(self):
        src=self.asset();lod_gen.generate(src,self.dir/'lod.usda',3,50)
        self.assertIn('already has LODs',lod_gen.generate(self.dir/'lod.usda',self.dir/'x.usda')['skipped'])
        with self.assertRaises(ValueError):lod_gen.generate(src,self.dir/'x.usda',1,50)
        with self.assertRaises(ValueError):lod_gen.generate(src,self.dir/'x.usda',lod_gen.MAX_LEVELS+1,50)
        # a mesh whose visibility is already authored could never be hidden by a variant
        def edited_copy(name,edit):   # edit a copy: the source layer stays exactly as saved
            Sdf.Layer.FindOrOpen(str(src)).Export(str(self.dir/name));stage=Usd.Stage.Open(str(self.dir/name))
            edit(stage.GetPrimAtPath('/Top/Geo/body'));stage.GetRootLayer().Save()
        edited_copy('visible.usda',lambda p:UsdGeom.Imageable(p).CreateVisibilityAttr().Set('inherited'))
        self.assertIn('visibility is already authored',lod_gen.generate(self.dir/'visible.usda',self.dir/'x.usda')['skipped'])
        # data that cannot be carried faithfully: refuse instead of writing a broken LOD
        edited_copy('ints.usda',lambda p:UsdGeom.PrimvarsAPI(p).CreatePrimvar('ids',Sdf.ValueTypeNames.IntArray,UsdGeom.Tokens.vertex).Set([1]*441))
        self.assertIn('unsupported vertex primvar',lod_gen.generate(self.dir/'ints.usda',self.dir/'x.usda')['skipped'])
        self.assertFalse((self.dir/'x.usda').exists())

    def test_lods_and_an_element_switch_share_the_top_prim_and_delete_independently(self):
        src=self.dir/'pack.usda';stage=Usd.Stage.CreateNew(str(src))
        top=UsdGeom.Xform.Define(stage,'/Top');UsdGeom.Xform.Define(stage,'/Top/Root')
        for name in ('a','b'):UsdGeom.Xform.Define(stage,f'/Top/Root/{name}');grid(stage,f'/Top/Root/{name}/geo',10)
        stage.SetDefaultPrim(top.GetPrim());stage.GetRootLayer().Save()
        element_gen.generate(src,self.dir/'switched.usda');before_lod=layer_text(self.dir/'switched.usda')
        lod_gen.generate(self.dir/'switched.usda',self.dir/'both.usda',3,50)
        both=Usd.Stage.Open(str(self.dir/'both.usda'));both.SetEditTarget(both.GetSessionLayer());prim=both.GetPrimAtPath('/Top')
        self.assertEqual(sorted(prim.GetVariantSets().GetNames()),['LOD','element'])
        for element,name in (('Element0','a'),('Element1','b')):
            prim.GetVariantSet('element').SetVariantSelection(element)
            for level in ('LOD_1','LOD_2','LOD_3'):
                prim.GetVariantSet('LOD').SetVariantSelection(level)
                self.assertEqual(list(shown(both)),['geo' if level=='LOD_1' else 'geo_'+level])   # one element, one level
                visible=[p.GetPath() for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(p).ComputeVisibility()!='invisible' and UsdGeom.Imageable(p).ComputePurpose()!='proxy']
                self.assertTrue(all(str(v).startswith(f'/Top/Root/{name}/') for v in visible))
        lod_gen.remove(self.dir/'both.usda',self.dir/'no_lod.usda')
        self.assertEqual(layer_text(self.dir/'no_lod.usda'),before_lod)   # element switch untouched
        self.assertEqual(element_gen.remove(self.dir/'both.usda',self.dir/'no_element.usda'),{'removed_element_switch':['/Top']})
        no_element=Usd.Stage.Open(str(self.dir/'no_element.usda'))
        self.assertEqual(no_element.GetPrimAtPath('/Top').GetVariantSets().GetNames(),['LOD'])   # the LODs stay

    def test_usdz_round_trip(self):
        src=self.asset('asset.usda');usdz=self.dir/'asset.usdz'
        self.assertTrue(UsdUtils.CreateNewUsdzPackage(str(src),str(usdz)));before=usdz.read_bytes()
        result=lod_gen.generate(usdz,self.dir/'lod.usdz',2,25)
        self.assertEqual(usdz.read_bytes(),before)   # the source is never written
        self.assertEqual(result['lods']['variants'],['LOD_1','LOD_2'])
        self.assertLessEqual(result['lods']['triangles'][1],result['lods']['triangles'][0]*0.3)
        lod_gen.remove(self.dir/'lod.usdz',self.dir/'back.usdz')
        self.assertEqual(layer_text(self.dir/'back.usdz'),layer_text(usdz))


if __name__=='__main__':unittest.main()
