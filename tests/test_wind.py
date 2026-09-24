from pathlib import Path
import hashlib
import math
import os
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
import numpy as np
from pxr import Usd, UsdGeom, UsdSkel, Sdf, Gf
from nanakusa_asset_library import wind_gen, variant_scan


def strip(base, lean, height, segments, width=0.01):
    """A thin blade from `base`, leaning by `lean` (x, z) at its tip: (points, counts, indices)."""
    points=[]
    for i in range(segments+1):
        s=i/segments
        c=(base[0]+lean[0]*s*s, base[1]+height*s, base[2]+lean[1]*s*s)
        points+=[(c[0]-width/2,c[1],c[2]),(c[0]+width/2,c[1],c[2])]
    indices=[]
    for i in range(segments):indices+=[2*i,2*i+1,2*i+3,2*i+2]
    return points,[4]*segments,indices


def merge(parts):
    points,counts,indices=[],[],[]
    for p,c,i in parts:
        offset=len(points);points+=p;counts+=c;indices+=[k+offset for k in i]
    return points,counts,indices


def plant(detail):
    """Five blades from the base, a leaf starting on a blade's side, loose florets at a blade's tip,
    and a root lying on the ground - the kinds of piece a scanned grass tuft is made of."""
    parts=[]
    for k in range(5):
        a=2*math.pi*k/5
        parts.append(strip((0.01*math.cos(a),0.0,0.01*math.sin(a)),(0.08*math.cos(a),0.08*math.sin(a)),0.5-0.04*k,detail))
    parts.append(strip((0.012,0.2,0.0),(0.1,0.02),0.15,detail//2))                  # leaf on blade 0's side
    for k in range(4):                                                                 # florets near blade 0's tip
        y=0.43+0.015*k
        parts.append(([(0.085,y,0.0),(0.095,y,0.0),(0.095,y+0.01,0.0),(0.085,y+0.01,0.0)],[4],[0,1,2,3]))
    parts.append(([(0.0,0.002,0.0),(0.12,0.002,0.03),(0.12,0.002,0.04),(0.0,0.002,0.01)],[4],[0,1,2,3]))   # root on the ground
    return merge(parts)


def define(stage, path, data, proxy=False):
    mesh=UsdGeom.Mesh.Define(stage,path)
    mesh.CreatePointsAttr(data[0]);mesh.CreateFaceVertexCountsAttr(data[1]);mesh.CreateFaceVertexIndicesAttr(data[2])
    UsdGeom.Imageable(mesh).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy if proxy else UsdGeom.Tokens.render)
    return mesh


def skinned(stage, frame):
    """{mesh path: skinned points} as UsdSkel computes them (what Karma and the viewport draw)."""
    cache=UsdSkel.Cache();out={}
    for prim in stage.Traverse():
        if not prim.IsA(UsdSkel.Root):continue
        root=UsdSkel.Root(prim);cache.Populate(root,Usd.PrimDefaultPredicate)
        for binding in cache.ComputeSkelBindings(root,Usd.PrimDefaultPredicate):
            xforms=binding.GetSkeleton() and cache.GetSkelQuery(binding.GetSkeleton()).ComputeSkinningTransforms(frame)
            for target in binding.GetSkinningTargets():
                points=target.GetPrim().GetAttribute('points').Get()
                target.ComputeSkinnedPoints(xforms,points,frame)
                out[str(target.GetPrim().GetPath())]=np.array(points)
    return out


class WindTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.dir=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def asset(self):
        """Like the library's scanned plants: LOD_0 / LOD_1 define the mesh, a proxy outside them."""
        src=self.dir/'Grass.usda';stage=Usd.Stage.CreateNew(str(src))
        UsdGeom.SetStageUpAxis(stage,'Y');UsdGeom.SetStageMetersPerUnit(stage,1)
        top=UsdGeom.Xform.Define(stage,'/Grass');UsdGeom.Scope.Define(stage,'/Grass/geo')
        stage.SetDefaultPrim(top.GetPrim())
        lod=top.GetPrim().GetVariantSets().AddVariantSet('LOD')
        for name,detail in (('LOD_0',16),('LOD_1',6)):
            lod.AddVariant(name);lod.SetVariantSelection(name)
            with lod.GetVariantEditContext():define(stage,'/Grass/geo/plant',plant(detail))
        lod.SetVariantSelection('LOD_0')
        define(stage,'/Grass/geo/plant_proxy',plant(3),proxy=True)
        stage.GetRootLayer().Save()
        return src

    def generate(self, src, **kw):
        out=wind_gen.output_for(src)
        result=wind_gen.generate(src,out,loop_seconds=4,**kw)
        # What ui.WindJob does after the worker succeeds.
        entry_temp,clip_temp=wind_gen.temp_paths(out)
        os.replace(clip_temp,result['clip']);os.replace(entry_temp,out)
        return out,result

    def test_output_goes_next_to_a_shared_layer_and_into_a_new_package_otherwise(self):
        self.assertEqual(wind_gen.output_for(Path('/lib/USD/Grass_OL/Big.usd')),Path('/lib/USD/Grass_OL/Big_Anim.usd'))
        self.assertEqual(wind_gen.output_for(Path('/lib/USD/Tree/Tree.usd')),Path('/lib/USD/Tree_Anim/Tree_Anim.usd'))
        self.assertEqual(wind_gen.output_for(Path('/lib/USD/Rocks/Stone.usdz')),Path('/lib/USD/Rocks/Stone_Anim/Stone_Anim.usd'))
        self.assertEqual(wind_gen.clip_path(Path('/x/Big_Anim.usd')),Path('/x/anim/Big_Anim_clip.usd'))

    def test_animation_references_the_untouched_source_loops_for_any_frame_and_skins_every_mesh(self):
        src=self.asset();before=hashlib.sha1(src.read_bytes()).hexdigest()
        out,result=self.generate(src)
        self.assertEqual(hashlib.sha1(src.read_bytes()).hexdigest(),before)
        self.assertTrue(wind_gen.is_output(out));self.assertFalse(wind_gen.is_output(src))
        stage=Usd.Stage.Open(str(out))
        self.assertEqual(stage.GetCompositionErrors(),[])
        self.assertEqual(stage.GetDefaultPrim().GetPath(),Sdf.Path('/Grass_Anim'))
        self.assertTrue(stage.GetPrimAtPath('/Grass_Anim/geo').IsA(UsdSkel.Root))   # below the placed prim
        frames=result['wind']['frames']
        rotations=UsdSkel.Animation(stage.GetPrimAtPath('/Grass_Anim/geo/NAL_wind/plant_anim')).GetRotationsAttr()
        self.assertTrue(rotations.ValueMightBeTimeVarying())
        for t in (1,1001,-37):
            self.assertEqual(list(rotations.Get(t)),list(rotations.Get(t+frames)))   # seamless loop anywhere
        self.assertNotEqual(list(rotations.Get(1)),list(rotations.Get(1+frames//3)))
        rest=np.array(stage.GetPrimAtPath('/Grass_Anim/geo/plant').GetAttribute('points').Get())
        moved=skinned(stage,1+frames//4)
        self.assertEqual(set(moved),{'/Grass_Anim/geo/plant','/Grass_Anim/geo/plant_proxy'})
        shift=np.linalg.norm(moved['/Grass_Anim/geo/plant']-rest,axis=1)
        self.assertGreater(shift.max(),0.005)          # blade tips sway
        self.assertLess(shift.max(),0.25)
        self.assertLess(shift[rest[:,1]<0.003].max(),1e-4)   # the root on the ground stays put

    def test_loose_florets_follow_their_blade_and_edges_keep_their_length(self):
        src=self.asset();out,result=self.generate(src,strength=2.0)
        stage=Usd.Stage.Open(str(out))
        rest=np.array(stage.GetPrimAtPath('/Grass_Anim/geo/plant').GetAttribute('points').Get())
        frames=result['wind']['frames']
        start=5*(16+1)*2+(16//2+1)*2   # after the five blades and the leaf (plant(16))
        floret=np.arange(start,start+16)
        self.assertTrue(np.allclose(rest[floret][:,0].min(),0.085))
        for f in range(1,frames,frames//6):
            p=skinned(stage,f)['/Grass_Anim/geo/plant']
            # Rigid: the florets keep their shape relative to each other.
            d0=np.linalg.norm(rest[floret][:,None]-rest[floret][None],axis=2)
            d1=np.linalg.norm(p[floret][:,None]-p[floret][None],axis=2)
            self.assertLess(np.abs(d1-d0).max(),2e-3)
        self.assertLess(max(c['stretch'] for c in result['wind']['checks'].values()),0.02)

    def test_each_lod_is_bound_inside_its_variant_and_the_selection_still_switches(self):
        src=self.asset();out,_=self.generate(src)
        stage=Usd.Stage.Open(str(out))
        top=stage.GetDefaultPrim()
        self.assertEqual(top.GetVariantSets().GetVariantSet('LOD').GetVariantSelection(),'LOD_0')   # the source's choice
        for name,detail in (('LOD_0',16),('LOD_1',6)):
            top.GetVariantSets().GetVariantSet('LOD').SetVariantSelection(name)
            mesh=stage.GetPrimAtPath('/Grass_Anim/geo/plant')
            count=len(mesh.GetAttribute('points').Get())
            self.assertEqual(count,len(plant(detail)[0]))
            binding=UsdSkel.BindingAPI(mesh)
            self.assertEqual(len(binding.GetJointIndicesPrimvar().Get()),count*wind_gen.INFLUENCES)
            weights=np.array(binding.GetJointWeightsPrimvar().Get()).reshape(-1,wind_gen.INFLUENCES)
            self.assertTrue(np.allclose(weights.sum(1),1,atol=1e-5))
            self.assertIn('/Grass_Anim/geo/plant',skinned(stage,5))

    def test_the_tag_check_sees_the_animation(self):
        src=self.asset();out,_=self.generate(src)
        names,has_proxy,has_anim=variant_scan.top_variant_sets(out)
        self.assertEqual((sorted(names),has_proxy,has_anim),(['LOD','wind_phase'],True,True))
        self.assertEqual(variant_scan.top_variant_sets(src),(['LOD'],True,False))

    def test_phases_play_the_same_loop_a_quarter_apart(self):
        src=self.asset();out,result=self.generate(src)
        frames=result['wind']['frames']
        stage=Usd.Stage.Open(str(out))
        phase=stage.GetDefaultPrim().GetVariantSets().GetVariantSet('wind_phase')
        self.assertEqual(phase.GetVariantNames(),['phase_0','phase_1','phase_2','phase_3'])
        self.assertEqual(phase.GetVariantSelection(),'phase_0')
        rotations=lambda:UsdSkel.Animation(stage.GetPrimAtPath('/Grass_Anim/geo/NAL_wind/plant_anim')).GetRotationsAttr()
        base=[list(rotations().Get(t)) for t in (5,1003)]
        phase.SetVariantSelection('phase_1')
        shifted=[list(rotations().Get(t+frames//4)) for t in (5,1003)]
        self.assertEqual(base,shifted)
        self.assertNotEqual(list(rotations().Get(5)),base[0])

    def test_an_animated_file_is_not_animated_again(self):
        src=self.asset();out,_=self.generate(src)
        with self.assertRaises(ValueError):
            wind_gen.generate(out,self.dir/'again.usd')


if __name__=='__main__':
    unittest.main()
