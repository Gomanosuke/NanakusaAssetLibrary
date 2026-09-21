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



class IndexScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'assets';self.root.mkdir()
        self.lib=Library(self.base/'index');self.rid=self.lib.add_root(self.root)
    def tearDown(self):self.temp.cleanup()
    def write(self,relative):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('x');return path

    def test_scan_records_folders_and_packages_stay_leaves(self):
        for rel in ('3DModel/Props/Deep/a.obj','USD/Plants/tree/tree.usd','USD/Plants/tree/textures/c.tif','Texture/PBR/x.tif'):self.write(rel)
        (self.root/'3DModel'/'Empty').mkdir();(self.root/'.hidden').mkdir()
        self.lib.scan(self.rid)
        self.assertEqual(sorted(self.lib.folders(self.rid)),sorted(['3DModel','3DModel/Props','3DModel/Props/Deep','3DModel/Empty','USD','USD/Plants','USD/Plants/tree','Texture','Texture/PBR']))
        self.assertEqual(set(visible_folders(self.root)),set(self.lib.folders(self.rid))|{'USD','Texture','3DModel'})   # same folders as a disk walk

    def test_failed_scan_keeps_the_previous_folder_list(self):
        self.write('3DModel/A/a.obj');self.lib.scan(self.rid);before=self.lib.folders(self.rid)
        self.assertTrue(self.lib.scan(self.rid,lambda:True)['cancelled']);self.assertEqual(self.lib.folders(self.rid),before)

    def test_created_and_moved_folders_are_tracked_without_a_scan(self):
        self.write('3DModel/A/a.obj');self.lib.scan(self.rid)
        self.lib.add_folder(self.rid,'3DModel/New/Nested');self.assertIn('3DModel/New',self.lib.folders(self.rid));self.assertIn('3DModel/New/Nested',self.lib.folders(self.rid))
        row=self.lib.assets()[0]
        self.lib.relocate(self.rid,[dict(id=row['id'],relpath='3DModel/New/Nested/A/a.obj',thumbnail='')],[('3DModel/A','3DModel/New/Nested/A')])
        folders=set(self.lib.folders(self.rid))
        self.assertIn('3DModel/New/Nested/A',folders);self.assertNotIn('3DModel/A',folders)
        self.lib.set_folders(self.rid,['3DModel/x']);self.assertEqual(self.lib.folders(self.rid),['3DModel/x'])
        self.lib.remove_root(self.rid);self.assertEqual(self.lib.folders(self.rid),[])

    def test_folder_query_is_an_exact_prefix_range(self):
        for rel in ('3DModel/Foo/a.obj','3DModel/Foo/Sub/b.obj','3DModel/Foo2/c.obj','3DModel/Foo-bar/d.obj','3DModel/Foo.x/e.obj','3DModel/Foo0/f.obj'):self.write(rel)
        self.lib.scan(self.rid)
        self.assertEqual(sorted(r['relpath'] for r in self.lib.assets(folder='3DModel/Foo')),['3DModel/Foo/Sub/b.obj','3DModel/Foo/a.obj'])
        self.assertEqual(len(self.lib.assets(folder='3DModel/Foo/')),2)
        self.assertEqual(len(self.lib.assets(folder='3DModel')),6)
        self.assertEqual(self.lib.assets(folder='3DModel/Nothing'),[])

    def test_assets_by_ids_in_chunks_and_missing_ids(self):
        for i in range(620):self.write(f'3DModel/m{i:04d}.obj')
        self.lib.scan(self.rid);rows=self.lib.assets()
        found=self.lib.assets_by_ids([r['id'] for r in rows]+['nope'])
        self.assertEqual({r['id'] for r in found},{r['id'] for r in rows});self.assertIn('root_path',found[0])
        self.assertEqual(self.lib.assets_by_ids([]),[])

    def test_scan_rows_match_plain_paths(self):
        self.write('3DModel/dir/mesh.bgeo.sc');self.write('3DModel/dir/Upper.OBJ');self.write('Texture/a.b.tif');self.write('USD/One.USDZ');self.write('USD/pack/pack.usda')
        self.lib.scan(self.rid);rows={r['relpath']:r for r in self.lib.assets()}
        self.assertEqual(rows['3DModel/dir/mesh.bgeo.sc']['label'],'mesh.bgeo');self.assertEqual(rows['3DModel/dir/Upper.OBJ']['label'],'Upper')
        self.assertEqual(rows['Texture/a.b.tif']['label'],'a.b');self.assertEqual(rows['USD/One.USDZ']['label'],'One');self.assertEqual(rows['USD/pack/pack.usda']['label'],'pack')
        self.assertEqual(rows['Texture/a.b.tif']['size'],1)

if __name__=='__main__':unittest.main()
