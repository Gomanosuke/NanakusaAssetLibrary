from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
import hou
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
