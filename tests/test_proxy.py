from pathlib import Path
import math
import os
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


def seam_split_grids(n=10, gap=0.0):
    """Two n x n grids side by side, sharing an edge but as separate, non-welded points - the
    way many FBX/Sketchfab exports split a surface at every UV or material seam."""
    def grid(x0):
        pts,idx={},{}
        for j in range(n+1):
            for i in range(n+1):
                idx[(i,j)]=len(pts);pts[idx[(i,j)]]=(x0+i,j,0.0)
        counts,indices=[],[]
        for j in range(n):
            for i in range(n):
                a,b,c,d=idx[(i,j)],idx[(i+1,j)],idx[(i+1,j+1)],idx[(i,j+1)]
                counts.append(4);indices.extend([a,b,c,d])
        return [pts[k] for k in range(len(pts))],counts,indices
    p1,c1,i1=grid(0);p2,c2,i2=grid(n+gap)
    offset=len(p1)
    return p1+p2,c1+c2,i1+[x+offset for x in i2]


class ProxyGenTests(unittest.TestCase):
    def test_seam_split_pieces_are_fused_before_reducing_and_keep_the_combined_shape(self):
        points,counts,indices=seam_split_grids()
        original_extent=max(p[0] for p in points)-min(p[0] for p in points)
        out_points,out_counts,out_indices,_=proxy_gen._decimate(points,counts,indices,40)
        reduced_extent=max(p[0] for p in out_points)-min(p[0] for p in out_points)
        # Without fusing the seam first, polyreduce shrinks each disconnected island on its own
        # and the combined shape collapses; fused, the full width of both grids is preserved.
        self.assertGreater(reduced_extent,original_extent*0.9)
        self.assertLess(len(out_points),len(points))   # still meaningfully reduced

    def test_target_is_triangles_not_primitives_for_an_all_quad_mesh(self):
        # polyreduce's own "Output Polygon Count" counts primitives; an all-quad mesh has half as
        # many primitives as triangles, so without triangulating first a target of, say, 500
        # triangles left the mesh at 500 quads (1000 triangles) - twice as heavy as requested.
        n=30
        pts=[(i,j,0.0) for j in range(n) for i in range(n)]
        counts,indices=[],[]
        for j in range(n-1):
            for i in range(n-1):
                a=j*n+i;b=a+1;c=a+n+1;d=a+n
                counts.append(4);indices.extend([a,b,c,d])
        original=proxy_gen._triangle_count(counts)
        target=500
        self.assertLess(target,original)
        out_points,out_counts,out_indices,_=proxy_gen._decimate(pts,counts,indices,target)
        self.assertAlmostEqual(proxy_gen._triangle_count(out_counts),target,delta=5)


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


def shown(stage, root='/Plant'):
    """(viewport, render): visible meshes whose purpose the viewport (default + proxy) and a
    render (default + render) draw."""
    viewport,render=[],[]
    for p in Usd.PrimRange(stage.GetPrimAtPath(root)):
        if not p.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(p).ComputeVisibility()=='invisible':continue
        purpose=UsdGeom.Imageable(p).ComputePurpose()
        if purpose in ('default','proxy'):viewport.append(p.GetName())
        if purpose in ('default','render'):render.append(p.GetName())
    return viewport,render


class ProxyGenVariantTests(unittest.TestCase):
    def native_variants(self,folder):
        """Like ThymusVulgaris_e95j6_*_OL.usd: every LOD variant defines its own var_01..var_03
        meshes, and the "variant" set deactivates the versions not chosen."""
        src=Path(folder)/'plant.usda';stage=Usd.Stage.CreateNew(str(src))
        top=UsdGeom.Xform.Define(stage,'/Plant').GetPrim();stage.DefinePrim('/Plant/geo','Scope')
        lod=top.GetVariantSets().AddVariantSet('LOD')
        for level,n in (('LOD_0',6),('LOD_1',4)):
            lod.AddVariant(level);lod.SetVariantSelection(level)
            with lod.GetVariantEditContext():
                for name in ('var_01','var_02','var_03'):grid_mesh(stage,f'/Plant/geo/{name}',n)
        lod.SetVariantSelection('LOD_0')
        choice=top.GetVariantSets().AddVariantSet('variant')
        for chosen in ('var_01','var_02','var_03'):
            choice.AddVariant(chosen);choice.SetVariantSelection(chosen)
            with choice.GetVariantEditContext():
                for other in ('var_01','var_02','var_03'):
                    if other!=chosen:stage.OverridePrim(f'/Plant/geo/{other}').SetActive(False)
        choice.SetVariantSelection('var_01')
        stage.SetDefaultPrim(top);stage.GetRootLayer().Save()
        return src

    def test_every_version_gets_a_proxy_that_is_switched_with_it(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.native_variants(folder);out=Path(folder)/'out.usda'
            before=Usd.Stage.Open(str(src));self.assertEqual(shown(before),(['var_01'],['var_01']))
            result=generate(src,out)
            self.assertEqual(result['proxied'],['/Plant/geo/var_01','/Plant/geo/var_02','/Plant/geo/var_03'])
            stage=Usd.Stage.Open(str(out));stage.SetEditTarget(stage.GetSessionLayer())
            sets=stage.GetPrimAtPath('/Plant').GetVariantSets()
            self.assertEqual(sets.GetVariantSet('variant').GetVariantSelection(),'var_01')   # the file's selections are untouched
            for chosen in ('var_01','var_02','var_03'):
                for level in ('LOD_0','LOD_1'):
                    sets.GetVariantSet('variant').SetVariantSelection(chosen);sets.GetVariantSet('LOD').SetVariantSelection(level)
                    # the viewport shows only the chosen version's proxy, a render only its mesh
                    self.assertEqual(shown(stage),([chosen+'_proxy'],[chosen]),(chosen,level))

    def test_a_proxy_keeps_standing_in_while_our_own_lod_hides_the_original_mesh(self):
        from nanakusa_asset_library import lod_gen
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda';stage=Usd.Stage.CreateNew(str(src))
            top=UsdGeom.Xform.Define(stage,'/Plant').GetPrim();grid_mesh(stage,'/Plant/body',12)
            stage.SetDefaultPrim(top);stage.GetRootLayer().Save()
            lod_gen.generate(src,Path(folder)/'lod.usda',2,50)
            generate(Path(folder)/'lod.usda',Path(folder)/'out.usda')
            out=Usd.Stage.Open(str(Path(folder)/'out.usda'));out.SetEditTarget(out.GetSessionLayer())
            self.assertEqual(shown(out),(['body_proxy'],['body']))
            out.GetPrimAtPath('/Plant').GetVariantSet('LOD').SetVariantSelection('LOD_2')
            self.assertEqual(shown(out),(['body_proxy'],['body_LOD_2']))   # the LOD copy is render-only

    def test_the_mesh_as_authored_still_gets_a_sibling_proxy_outside_any_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda';stage=Usd.Stage.CreateNew(str(src))
            top=UsdGeom.Xform.Define(stage,'/Plant').GetPrim();grid_mesh(stage,'/Plant/body',4)
            stage.SetDefaultPrim(top);stage.GetRootLayer().Save()
            generate(src,Path(folder)/'out.usda')
            layer=Sdf.Layer.FindOrOpen(str(Path(folder)/'out.usda'))
            self.assertEqual(layer.GetPrimAtPath('/Plant/body_proxy').specifier,Sdf.SpecifierDef)
            self.assertEqual(layer.GetPrimAtPath('/Plant/body').attributes['purpose'].default,'render')


def textured_quad(stage, path, tex_path):
    """A UsdPreviewSurface-bound quad whose left half samples red and right half samples blue."""
    import OpenImageIO as oiio
    spec=oiio.ImageSpec(8,8,3,oiio.UINT8);buf=oiio.ImageBuf(spec)
    for y in range(8):
        for x in range(8):buf.setpixel(x,y,[1.0,0.0,0.0] if x<4 else [0.0,0.0,1.0])
    Path(tex_path).parent.mkdir(parents=True,exist_ok=True);buf.write(str(tex_path))
    mesh=UsdGeom.Mesh.Define(stage,path)
    mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.faceVarying).Set([(0,0),(1,0),(1,1),(0,1)])
    mat=UsdShade.Material.Define(stage,path+'/mtl')
    surf=UsdShade.Shader.Define(stage,path+'/mtl/surface');surf.CreateIdAttr('UsdPreviewSurface')
    tex=UsdShade.Shader.Define(stage,path+'/mtl/tex');tex.CreateIdAttr('UsdUVTexture')
    tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(os.path.relpath(str(tex_path),Path(stage.GetRootLayer().realPath).parent).replace('\\','/'))
    reader=UsdShade.Shader.Define(stage,path+'/mtl/reader');reader.CreateIdAttr('UsdPrimvarReader_float2')
    reader.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
    tex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(),'result')
    surf.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(),'rgb')
    mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(),'surface')
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(mat)
    return mesh


class ProxyGenColorTests(unittest.TestCase):
    def test_base_color_texture_is_baked_onto_the_proxy_as_a_vertex_color(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            src=Path(folder)/'asset.usda';dest=Path(folder)/'out.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            textured_quad(stage,'/Asset/geo',Path(folder)/'textures'/'albedo.png')
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            result=generate(src,dest)
            self.assertEqual(result['proxied'],['/Asset/geo'])
            check=Usd.Stage.Open(str(dest))
            proxy=UsdGeom.Mesh(check.GetPrimAtPath('/Asset/geo_proxy'))
            cpv=proxy.GetDisplayColorPrimvar()
            self.assertTrue(cpv);self.assertEqual(cpv.GetInterpolation(),'vertex')
            colors=cpv.ComputeFlattened()
            self.assertEqual(len(colors),4)
            # points 0,3 are u=0 (red); points 1,2 are u=1 (blue)
            self.assertGreater(colors[0][0],colors[0][2]);self.assertGreater(colors[3][0],colors[3][2])
            self.assertGreater(colors[1][2],colors[1][0]);self.assertGreater(colors[2][2],colors[2][0])
            for c in colors:
                for channel in c:self.assertGreaterEqual(channel,0.0);self.assertLessEqual(channel,1.0)

    def test_baked_colors_survive_decimation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            src=Path(folder)/'asset.usda';dest=Path(folder)/'out.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            import OpenImageIO as oiio
            tex_path=Path(folder)/'textures'/'albedo.png';tex_path.parent.mkdir(parents=True)
            spec=oiio.ImageSpec(8,8,3,oiio.UINT8);buf=oiio.ImageBuf(spec)
            for y in range(8):
                for x in range(8):buf.setpixel(x,y,[1.0,0.0,0.0] if x<4 else [0.0,0.0,1.0])
            buf.write(str(tex_path))
            n=30
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            points=[(i,j,0.0) for j in range(n) for i in range(n)]
            counts,indices,uvs=[],[],[]
            for j in range(n-1):
                for i in range(n-1):
                    a=j*n+i;b=a+1;c=a+n+1;d=a+n
                    counts.append(4);indices.extend([a,b,c,d])
                    u0,u1=i/(n-1),(i+1)/(n-1);v0,v1=j/(n-1),(j+1)/(n-1)
                    uvs.extend([(u0,v0),(u1,v0),(u1,v1),(u0,v1)])
            mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr(counts);mesh.CreateFaceVertexIndicesAttr(indices)
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.faceVarying).Set(uvs)
            mat=UsdShade.Material.Define(stage,'/Asset/mtl')
            surf=UsdShade.Shader.Define(stage,'/Asset/mtl/surface');surf.CreateIdAttr('UsdPreviewSurface')
            tex=UsdShade.Shader.Define(stage,'/Asset/mtl/tex');tex.CreateIdAttr('UsdUVTexture')
            tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set('./textures/albedo.png')
            reader=UsdShade.Shader.Define(stage,'/Asset/mtl/reader');reader.CreateIdAttr('UsdPrimvarReader_float2')
            reader.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
            tex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(),'result')
            surf.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(),'rgb')
            mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(),'surface')
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim());UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(mat)
            self.assertGreater(proxy_gen._triangle_count(counts),proxy_gen.TARGET_TRIANGLES)
            stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/Asset').GetPrim());stage.GetRootLayer().Save()

            result=generate(src,dest)
            check=Usd.Stage.Open(str(dest))
            proxy=UsdGeom.Mesh(check.GetPrimAtPath('/Asset/geo_proxy'))
            colors=proxy.GetDisplayColorPrimvar().ComputeFlattened()
            self.assertLess(len(colors),len(points))   # decimated
            reds=sum(1 for c in colors if c[0]>c[2]);blues=sum(1 for c in colors if c[2]>c[0])
            self.assertGreater(reds,0);self.assertGreater(blues,0)   # both halves still represented
            for c in colors:
                for channel in c:self.assertGreaterEqual(channel,0.0);self.assertLessEqual(channel,1.0)

    def test_no_bound_material_leaves_the_proxy_without_display_color(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'asset.usda';dest=Path(folder)/'out.usda'
            stage=Usd.Stage.CreateNew(str(src));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
            mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()
            generate(src,dest)
            check=Usd.Stage.Open(str(dest))
            proxy=UsdGeom.Mesh(check.GetPrimAtPath('/Asset/geo_proxy'))
            self.assertFalse(proxy.GetDisplayColorPrimvar().HasValue())


def cutout_material(stage, path):
    """A leaf-like material: opacity from a texture, cut at 0.5 (AcerPseudoplatanus_abw4u_Leaves_OL)."""
    mat=UsdShade.Material.Define(stage,path)
    surf=UsdShade.Shader.Define(stage,path+'/surface');surf.CreateIdAttr('UsdPreviewSurface')
    surf.CreateInput('opacityThreshold',Sdf.ValueTypeNames.Float).Set(0.5)
    tex=UsdShade.Shader.Define(stage,path+'/opacity');tex.CreateIdAttr('UsdUVTexture')
    tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set('./textures/opacity.png')
    surf.CreateInput('opacity',Sdf.ValueTypeNames.Float).ConnectToSource(tex.ConnectableAPI(),'r')
    mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(),'surface')
    return mat


def bound(stage, path):
    material=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).ComputeBoundMaterial()[0]
    return str(material.GetPath()) if material else None


def binding_targets(stage, path):
    return [str(t) for t in UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).GetDirectBindingRel().GetTargets()]


class ProxyGenLookTests(unittest.TestCase):
    """A proxy must not draw with the material its render mesh inherits: without UVs it reads the
    opacity map at (0, 0), which is 0 on leaf cards, and the viewport cut the proxy away. Nor may
    it draw with an empty Material: Houdini's Scene View shows that plain grey, ignoring @Cd."""
    LOOK='/Plant/'+proxy_gen.PROXY_LOOK

    def assertNoMaterial(self,stage,path,msg=None):
        """Bound to the look Scope, so the binding resolves to no material at all."""
        self.assertEqual(binding_targets(stage,path),[self.LOOK],msg)
        self.assertIsNone(bound(stage,path),msg)
        self.assertEqual(stage.GetPrimAtPath(self.LOOK).GetTypeName(),'Scope',msg)

    def leaf(self,folder,with_proxy=False):
        src=Path(folder)/'plant.usda';stage=Usd.Stage.CreateNew(str(src))
        top=UsdGeom.Xform.Define(stage,'/Plant').GetPrim();geo=stage.DefinePrim('/Plant/geo','Scope')
        mat=cutout_material(stage,'/Plant/mtl/leaf')
        UsdShade.MaterialBindingAPI.Apply(geo).Bind(mat)   # bound above the mesh, as in the source files
        mesh,_=grid_mesh(stage,'/Plant/geo/leaf',4)
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set([(0,0)]*16)
        if with_proxy:   # a proxy made before the look existed
            proxy,_=grid_mesh(stage,'/Plant/geo/leaf_proxy',2)
            UsdGeom.Imageable(proxy.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
            UsdGeom.Imageable(mesh.GetPrim()).CreatePurposeAttr().Set(UsdGeom.Tokens.render)
        stage.SetDefaultPrim(top);stage.GetRootLayer().Save()
        return src

    def test_a_new_proxy_resolves_to_no_material_and_carries_only_points_and_display_color(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out.usda'
            generate(self.leaf(folder),out)
            stage=Usd.Stage.Open(str(out))
            self.assertNoMaterial(stage,'/Plant/geo/leaf_proxy')   # the viewport draws its displayColor
            self.assertEqual(bound(stage,'/Plant/geo/leaf'),'/Plant/mtl/leaf')   # the render mesh keeps its own
            proxy=stage.GetPrimAtPath('/Plant/geo/leaf_proxy')
            # no texture to bake here, so not even displayColor; never st, normals or opacity
            self.assertEqual(sorted(a.GetName() for a in proxy.GetAuthoredAttributes()),
                             ['faceVertexCounts','faceVertexIndices','points','purpose'])
            self.assertEqual(list(UsdGeom.PrimvarsAPI(proxy).FindInheritablePrimvars()),[])

    def test_proxies_made_before_the_look_get_it_and_a_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.leaf(folder,with_proxy=True);out=Path(folder)/'out.usda'
            self.assertEqual(generate(src,out),{'restyled':['/Plant/geo/leaf_proxy']})
            stage=Usd.Stage.Open(str(out))
            self.assertNoMaterial(stage,'/Plant/geo/leaf_proxy')
            self.assertEqual(bound(stage,'/Plant/geo/leaf'),'/Plant/mtl/leaf')
            self.assertEqual(generate(out,Path(folder)/'again.usda'),{'skipped':'already has a proxy'})

    def test_a_proxy_on_the_grey_empty_material_of_0_22_2_is_moved_to_the_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            src=self.leaf(folder,with_proxy=True)
            stage=Usd.Stage.Open(str(src))
            old=UsdShade.Material.Define(stage,self.LOOK)
            UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath('/Plant/geo/leaf_proxy')).Bind(old)
            stage.GetRootLayer().Save()
            out=Path(folder)/'out.usda'
            self.assertEqual(generate(src,out),{'restyled':['/Plant/geo/leaf_proxy']})
            stage=Usd.Stage.Open(str(out))
            self.assertNoMaterial(stage,'/Plant/geo/leaf_proxy')
            self.assertEqual(generate(out,Path(folder)/'again.usda'),{'skipped':'already has a proxy'})

    def test_proxies_inside_variants_are_bound_whichever_version_is_chosen(self):
        with tempfile.TemporaryDirectory() as folder:
            src=ProxyGenVariantTests.native_variants(None,folder)
            stage=Usd.Stage.Open(str(src))
            UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath('/Plant/geo')).Bind(cutout_material(stage,'/Plant/mtl/leaf'))
            stage.GetRootLayer().Save()
            out=Path(folder)/'out.usda';generate(src,out)
            stage=Usd.Stage.Open(str(out));stage.SetEditTarget(stage.GetSessionLayer())
            for chosen in ('var_01','var_02','var_03'):
                for level in ('LOD_0','LOD_1'):
                    sets=stage.GetPrimAtPath('/Plant').GetVariantSets()
                    sets.GetVariantSet('variant').SetVariantSelection(chosen);sets.GetVariantSet('LOD').SetVariantSelection(level)
                    self.assertNoMaterial(stage,f'/Plant/geo/{chosen}_proxy',(chosen,level))
                    self.assertEqual(bound(stage,f'/Plant/geo/{chosen}'),'/Plant/mtl/leaf',(chosen,level))


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
