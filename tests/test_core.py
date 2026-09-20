import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from solaris_asset_library.core import Library, classify, inside, read_manifest

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'assets'; self.root.mkdir()
        self.lib=Library(self.base/'index'); self.rid=self.lib.add_root(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_scan_preserves_user_metadata_and_deduplicates(self):
        (self.root/'chair.obj').write_text('v 0 0 0')
        self.lib.scan(self.rid); asset=self.lib.assets()[0]
        self.lib.update(asset['id'],favorite=1,tags='wood outdoor',label='Chair',override_kind='usd')
        self.lib.scan(self.rid)
        rows=self.lib.assets(search='wood Chair',favorite=True,kind='usd')
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['id'],asset['id'])

    def test_missing_and_relink(self):
        (self.root/'a.usd').write_text('#usda 1.0')
        self.lib.scan(self.rid); old=self.lib.assets()[0]['id']
        new=self.base/'moved'; self.root.rename(new)
        with self.assertRaises(FileNotFoundError):self.lib.scan(self.rid)
        self.assertEqual(len(self.lib.assets()),1)
        self.lib.relink_root(self.rid,new); self.lib.scan(self.rid)
        self.assertEqual(self.lib.assets()[0]['id'],old)
        self.assertEqual(self.lib.resolve(self.lib.assets()[0]),new/'a.usd')
        (new/'a.usd').unlink(); self.lib.scan(self.rid)
        self.assertEqual(self.lib.assets(),[])

    def test_cancel_does_not_mark_missing(self):
        (self.root/'a.obj').write_text('v 0 0 0'); self.lib.scan(self.rid)
        result=self.lib.scan(self.rid,lambda:True)
        self.assertTrue(result['cancelled']); self.assertEqual(len(self.lib.assets()),1)

    def test_path_escape_and_overlapping_roots(self):
        with self.assertRaises(ValueError):inside(self.root,'../escape')
        with self.assertRaises(ValueError):self.lib.add_root(self.base)

    def test_forget_does_not_delete_source(self):
        source=self.root/'a.obj'; source.write_text('v 0 0 0')
        self.lib.scan(self.rid); backup=self.lib.backup_index()
        self.lib.remove_root(self.rid)
        self.assertTrue(source.exists()); self.assertTrue(backup.stat().st_size>0)

    def test_classification(self):
        self.assertEqual(classify('Textures/color.exr'),'texture')
        self.assertEqual(classify('HDRI/sky.exr'),'hdri')
        self.assertEqual(classify('Decals/sign.png'),'decal')
        self.assertEqual(classify('wall.pbr.json'),'pbr')
        self.assertEqual(classify('tree.bgeo.sc'),'model')

    def test_manifest(self):
        image=self.root/'color.png'; image.write_bytes(b'test')
        manifest=self.root/'wood.pbr.json'
        manifest.write_text(json.dumps({'schema':1,'maps':{'base_color':'color.png'}}))
        _,maps=read_manifest(manifest)
        self.assertEqual(maps['base_color'],image.as_posix())
        image.unlink()
        with self.assertRaises(FileNotFoundError):read_manifest(manifest)

    def test_search_literal_wildcards(self):
        (self.root/'a_wood.obj').write_text('')
        (self.root/'abwood.obj').write_text('')
        self.lib.scan(self.rid)
        self.assertEqual(len(self.lib.assets(search='a_')),1)

if __name__=='__main__':unittest.main()
