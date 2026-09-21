from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
import hou
from unittest.mock import patch
from pxr import Usd,UsdGeom
from nanakusa_asset_library import dragdrop as dd

class DropTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.obj=self.root/'mesh.obj';self.obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        self.net=hou.node('/obj').createNode('subnet','drop_test');self.lop=self.net.createNode('lopnet')
    def tearDown(self):self.net.destroy();self.temp.cleanup()
    def test_mime_preserves_plain_path(self):
        mime=dd.mime_data(self.obj,'model','mesh')
        self.assertEqual(mime.text(),self.obj.as_posix());self.assertEqual(dd.parse(mime)['path'],self.obj.as_posix())
        self.assertEqual(mime.urls()[0].toLocalFile(),self.obj.as_posix())

    def test_batch_mime_all_kinds_and_spaces(self):
        entries=[{'path':str(self.root/name),'kind':kind,'label':kind} for name,kind in [('mesh file.obj','model'),('asset.usd','usd'),('color.png','texture')]]
        mime=dd.mime_data_many(entries)
        self.assertEqual(len(dd.parse_items(mime)),3)
        self.assertEqual(len(mime.urls()),3)
        self.assertIn('"'+(self.root/'mesh file.obj').as_posix()+'"',mime.text())

    def test_batch_merges_geometry_and_rolls_back_failure(self):
        data={'kind':'model','path':str(self.obj),'label':'mesh'}
        nodes=dd.import_payloads([data,dict(data,label='other')],self.lop)
        merge=self.lop.displayNode()
        self.assertEqual(merge.type().name(),'merge');self.assertEqual(list(merge.inputs()),nodes)
        self.assertEqual(sum(p.IsA(UsdGeom.Mesh) for p in merge.stage().Traverse()),2)
        before=set(self.lop.children())
        original=dd.ops.import_into_context
        calls=[]
        def fail_second(*args):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('synthetic failure')
            return original(*args)
        with patch.object(dd.ops,'import_into_context',side_effect=fail_second):
            with self.assertRaises(RuntimeError):dd.import_payloads([data,data],self.lop)
        self.assertEqual(set(self.lop.children()),before)
        self.assertEqual(self.lop.displayNode(),merge)

    def test_pbr_batch_shared_uv_and_output_preservation(self):
        mat=self.lop.createNode('materiallibrary')
        payloads=[]
        for channel in ('albedo','normal','roughness','metallic','height','ao','opacity','emission'):
            path=self.root/('stone_1K_'+channel+'.tif');path.write_bytes(b'placeholder')
            payloads.append({'path':str(path),'kind':'texture','label':path.stem})
        builder,=dd.import_payloads(payloads,mat)
        images=[n for n in builder.children() if n.type().name()=='mtlximage']
        self.assertEqual(len(images),8)
        self.assertEqual(len({n.input(3) for n in images}),1)
        shader=next(n for n in builder.children() if n.type().name()=='mtlxstandard_surface')
        self.assertEqual(shader.input(shader.inputIndex('normal')).type().name().split('::')[0],'mtlxnormalmap')
        self.assertEqual(shader.input(shader.inputIndex('base_color')).type().name(),'mtlxmultiply')
        self.assertEqual(shader.input(shader.inputIndex('specular_roughness')).outputDataTypes(),('float',))
        self.assertEqual(builder.node('normal_image').parm('filecolorspace').eval(),'Raw')
        original=builder.node('surface_output').input(0)
        positions={n:n.position() for n in builder.children()}
        dd.import_payloads(payloads,builder)
        self.assertEqual(builder.node('surface_output').input(0),original)
        self.assertTrue(all(n.position()==pos for n,pos in positions.items()))
    def pbr_payloads(self,prefix='stone_1K_',channels=('albedo','normal','roughness','metallic','height','ao','opacity','emission')):
        payloads=[]
        for channel in channels:
            path=self.root/(prefix+channel+'.tif');path.write_bytes(b'placeholder')
            payloads.append({'path':str(path),'kind':'texture','label':path.stem})
        return payloads

    def test_material_layout_aligns_image_nodes_in_columns(self):
        mat=self.lop.createNode('materiallibrary')
        builder,=dd.import_payloads(self.pbr_payloads(),mat)
        images=[n for n in builder.children() if n.type().name()=='mtlximage']
        self.assertEqual(len(images),8);self.assertEqual(len({round(n.position()[0],4) for n in images}),1)
        ys=sorted((n.position()[1] for n in images),reverse=True)
        self.assertTrue(all(a-b>=images[0].size()[1] for a,b in zip(ys,ys[1:])),'images must not overlap')
        self.assertEqual(len({round(a-b,4) for a,b in zip(ys,ys[1:])}),1)   # evenly spaced
        # Same top-to-bottom order as the inputs of mtlxstandard_surface; displacement comes last.
        order=[n.name() for n in sorted(images,key=lambda n:-n.position()[1])]
        self.assertEqual(order,['base_color_image','ao_image','metalness_image','roughness_image','emission_image','opacity_image','normal_image','displacement_image'])
        shader=next(n for n in builder.children() if n.type().name()=='mtlxstandard_surface')
        slots=[shader.inputNames().index(x) for x in ('base_color','metalness','specular_roughness','emission_color','opacity','normal')]
        self.assertEqual(slots,sorted(slots))
        uv=next(n for n in builder.children() if n.type().name()=='mtlxtexcoord')
        decode=builder.node('normal_decode');multiply=builder.node('base_color_ao');out=builder.node('surface_output')
        x=lambda n:n.position()[0]
        self.assertLess(x(uv),x(images[0]));self.assertLess(x(images[0]),x(decode));self.assertEqual(round(x(decode),4),round(x(multiply),4))
        self.assertLess(x(decode),x(shader));self.assertLess(x(shader),x(out))
        self.assertEqual(round(decode.position()[1],4),round(builder.node('normal_image').position()[1],4))   # beside its image

    def test_material_layout_in_existing_builder_goes_below_and_keeps_nodes(self):
        mat=self.lop.createNode('materiallibrary')
        builder,=dd.import_payloads(self.pbr_payloads(),mat)
        before={n:tuple(n.position()) for n in builder.children()}
        dd.import_payloads(self.pbr_payloads('brick_2K_',('albedo','roughness','normal')),builder)
        self.assertTrue(all(tuple(n.position())==pos for n,pos in before.items()))
        added=[n for n in builder.children() if n not in before]
        self.assertTrue(all(n.position()[1]<min(p[1] for p in before.values()) for n in added))
        images=[n for n in added if n.type().name()=='mtlximage']
        self.assertEqual(len({round(n.position()[0],4) for n in images}),1)
        known=set(builder.children())
        dd.import_payloads(self.pbr_payloads('wood_2K_',('albedo','roughness')),builder,hou.Vector2(20,-30))
        uv=next(n for n in builder.children() if n not in known and n.type().name()=='mtlxtexcoord')
        self.assertEqual(round(uv.position()[0],3),20.0)   # dropped at the cursor, still one aligned group

    def test_obj_stage_and_sop_drops(self):
        data={'kind':'model','path':str(self.obj),'label':'mesh'}
        geo=dd.import_payload(data,self.net);self.assertEqual(geo.type().name(),'geo')
        file=next(n for n in geo.children() if n.type().name()=='file')
        self.assertEqual(file.parm('file').eval(),self.obj.as_posix())
        node=dd.import_payload(data,self.lop);self.assertTrue(any(p.IsA(UsdGeom.Mesh) for p in node.stage().Traverse()))
        file2=dd.import_payload(data,geo);self.assertEqual(file2.type().name(),'file')
    def test_usd_obj_drop(self):
        path=self.root/'asset.usda';s=Usd.Stage.CreateNew(str(path));g=UsdGeom.Cube.Define(s,'/asset');s.SetDefaultPrim(g.GetPrim());s.GetRootLayer().Save()
        geo=dd.import_payload({'kind':'usd','path':str(path),'label':'asset'},self.net)
        reader=next(n for n in geo.children() if n.type().name()=='usdimport')
        self.assertEqual(reader.parm('filepath1').eval(),path.as_posix());self.assertTrue(reader.geometry().prims())
    def test_vdb_file_in_obj_and_stage(self):
        geo=self.net.createNode('geo');box=geo.createNode('box')
        vdb=geo.createNode('vdbfrompolygons');vdb.setInput(0,box)
        path=self.root/'volume.vdb';vdb.geometry().saveToFile(str(path))
        payload={'kind':'model','path':str(path),'label':'volume'}
        result=dd.import_payload(payload,self.net)
        reader=next(n for n in result.children() if n.type().name()=='file')
        self.assertEqual(reader.parm('file').eval(),path.as_posix())
        self.assertTrue(reader.geometry().prims())
        stage=dd.import_payload(payload,self.lop).stage()
        self.assertTrue(any(p.GetTypeName()=='Volume' for p in stage.Traverse()))
    def test_texture_only_in_material_context(self):
        mat=self.lop.createNode('materiallibrary')
        self.assertFalse(dd.can_import(self.lop,'texture'));self.assertFalse(dd.can_import(self.net,'texture'))
        self.assertTrue(dd.can_import(mat,'texture'))
        path=self.root/'image.png';path.write_bytes(b'placeholder')
        payload={'kind':'texture','path':str(path),'label':'surface'}
        builder=dd.import_payload(payload,mat);self.assertTrue(dd.can_import(builder,'texture'))
        img=next(n for n in builder.children() if n.type().name()=='mtlximage')
        self.assertEqual(img.input(3).type().name(),'mtlxtexcoord')
        output=builder.node('surface_output');original=output.input(0)
        surface=dd.import_payload(payload,builder)
        self.assertEqual(surface.type().name(),'mtlxstandard_surface');self.assertEqual(output.input(0),original)
if __name__=='__main__':unittest.main()
