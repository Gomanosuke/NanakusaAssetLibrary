from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library.core import Library,inside,visible_folders

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'assets';self.root.mkdir()
        for genre in ('USD','Texture','3DModel'):(self.root/genre).mkdir()
        self.lib=Library(self.base/'index');self.rid=self.lib.add_root(self.root)
    def tearDown(self):self.temp.cleanup()
    def write(self,relative,content=''):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content);return path
    def test_usd_package_is_opaque(self):
        self.write('USD/Plants/tree/tree.usd');self.write('USD/Plants/tree/geo.usdc')
        self.write('USD/Plants/tree/textures/color.tif');self.write('USD/loose.usd')
        self.lib.scan(self.rid);rows=self.lib.assets()
        self.assertEqual([r['relpath'] for r in rows],['USD/Plants/tree/tree.usd'])
        self.assertEqual(rows[0]['label'],'tree')
        self.assertNotIn('USD/Plants/tree/textures',visible_folders(self.root))
    def test_standalone_usdz_and_opaque_packages(self):
        for rel in ('USD/post.usdz','USD/Props/sign.USDZ','USD/tree/tree.usdz','USD/tree/hidden.usdz','USD/loose.usd'):
            self.write(rel)
        self.lib.scan(self.rid)
        rows={r['relpath']:r for r in self.lib.assets()}
        self.assertEqual(set(rows),{'USD/post.usdz','USD/Props/sign.USDZ','USD/tree/tree.usdz'})
        self.assertEqual(rows['USD/post.usdz']['label'],'post')
        self.assertEqual(rows['USD/Props/sign.USDZ']['label'],'sign')

    def test_fixed_genres_and_single_file_models(self):
        for path in ('3DModel/a.obj','3DModel/b.fbx','3DModel/smoke.vdb','Texture/a.tif','Texture/sky.hdr'):self.write(path)
        for path in ('3DModel/color.png','3DModel/multi.gltf','Texture/model.obj','Other/a.obj'):self.write(path)
        self.lib.scan(self.rid)
        self.assertEqual(len(self.lib.assets()),5);self.assertEqual(len(self.lib.assets(kind='texture')),2)
        self.assertNotIn('Other',visible_folders(self.root))
    def test_rescan_preserves_metadata(self):
        self.write('3DModel/chair.obj');self.lib.scan(self.rid)
        row=self.lib.assets()[0];self.lib.update(row['id'],tags='wood outdoor',favorite=1,label='Chair')
        self.lib.scan(self.rid);rows=self.lib.assets(search='wood Chair',favorite=True)
        self.assertEqual(len(rows),1);self.assertEqual(row['id'],rows[0]['id'])
    def test_old_flat_index_pruned(self):
        self.write('USD/tree/tree.usd');self.write('USD/tree/textures/color.tif')
        with self.lib.connect() as db:
            db.execute('INSERT INTO assets (id,root_id,relpath,label,kind) VALUES (?,?,?,?,?)',('old',self.rid,'USD/tree/textures/color.tif','color','texture'))
        self.lib.scan(self.rid);self.assertEqual(len(self.lib.assets()),1)
    def test_cancel_offline_and_relink(self):
        self.write('3DModel/a.obj');self.lib.scan(self.rid)
        self.assertTrue(self.lib.scan(self.rid,lambda:True)['cancelled'])
        new=self.base/'moved';self.root.rename(new)
        with self.assertRaises(FileNotFoundError):self.lib.scan(self.rid)
        self.assertEqual(len(self.lib.assets()),1)
        self.lib.relink_root(self.rid,new);self.lib.scan(self.rid)
        # Windows runners may expose TEMP through an 8.3 alias; compare identity.
        self.assertTrue(self.lib.resolve(self.lib.assets()[0]).samefile(new/'3DModel/a.obj'))
    def test_path_escape_and_overlap(self):
        with self.assertRaises(ValueError):inside(self.root,'../escape')
        with self.assertRaises(ValueError):self.lib.add_root(self.root/'USD')
    def test_forget_keeps_files_and_backup(self):
        path=self.write('3DModel/a.obj');self.lib.scan(self.rid)
        backup=self.lib.backup_index();self.lib.remove_root(self.rid)
        self.assertTrue(path.exists());self.assertTrue(backup.stat().st_size>0)
if __name__=='__main__':unittest.main()
