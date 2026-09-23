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

    def test_several_usd_files_sharing_a_folder_are_each_an_asset(self):
        # e.g. ErodiumCiconium_6j98t_OL: Big and Small versions, one textures folder, no entry file named after the folder
        for rel in ('USD/Misc/Plant_OL/Plant_Big_OL.usd','USD/Misc/Plant_OL/Plant_Small_OL.usda','USD/Misc/Plant_OL/extra.usdz',
                    'USD/Misc/Plant_OL/textures/leaf/a.png','USD/Misc/Plant_OL/textures/leaf/a.png.rat',
                    'USD/Misc/Plant_OL/Plant_Big_OL.nanakusa_generate_tmp.usd',   # a running job's output: never listed
                    'USD/loose.usd'):   # loose layers right under USD/ stay unlisted
            self.write(rel)
        self.lib.scan(self.rid)
        rows={r['relpath']:r for r in self.lib.assets()}
        self.assertEqual(set(rows),{'USD/Misc/Plant_OL/Plant_Big_OL.usd','USD/Misc/Plant_OL/Plant_Small_OL.usda','USD/Misc/Plant_OL/extra.usdz'})
        self.assertEqual(rows['USD/Misc/Plant_OL/Plant_Big_OL.usd']['label'],'Plant_Big_OL')
        # listed in their own folder, whose subfolders are its files rather than library folders
        listed=self.lib.stream(stacked=False,folder='USD/Misc/Plant_OL',recursive=False).next(50)
        self.assertEqual({e['rep']['relpath'] for e in listed},set(rows))
        self.assertIn('USD/Misc/Plant_OL',self.lib.folders(self.rid))
        self.assertFalse(any(f.startswith('USD/Misc/Plant_OL/') for f in self.lib.folders(self.rid)))
        self.assertNotIn('USD/Misc/Plant_OL/textures',visible_folders(self.root))
        from nanakusa_asset_library import core,storage
        big=rows['USD/Misc/Plant_OL/Plant_Big_OL.usd']
        self.assertFalse(core.is_usd_package(big));self.assertTrue(core.is_shared_usd_layer(big))
        self.assertFalse(core.is_shared_usd_layer(rows['USD/Misc/Plant_OL/extra.usdz']))   # a .usdz carries its own files
        # each has its own thumbnail next to it (a package's folder thumbnail would be shared by both)
        self.assertEqual(storage.thumbnail_destination(big,self.base).name,'Plant_Big_OL_thumbnail.png')
        # a folder with an entry file named after it is still one package, whatever else it holds
        self.write('USD/Misc/Tree/Tree.usd');self.write('USD/Misc/Tree/geo.usdc');self.lib.scan(self.rid)
        tree=[r for r in self.lib.assets() if r['relpath'].startswith('USD/Misc/Tree/')]
        self.assertEqual([r['relpath'] for r in tree],['USD/Misc/Tree/Tree.usd']);self.assertTrue(core.is_usd_package(tree[0]))

    def test_fixed_genres_and_single_file_models(self):
        for path in ('3DModel/a.obj','3DModel/b.fbx','3DModel/smoke.vdb','Texture/a.tif','Texture/sky.hdr'):self.write(path)
        for path in ('3DModel/color.png','3DModel/multi.gltf','Texture/model.obj','Other/a.obj'):self.write(path)
        self.lib.scan(self.rid)
        self.assertEqual(len(self.lib.assets()),5);self.assertEqual(len(self.lib.assets(kind='texture')),2)
        self.assertNotIn('Other',visible_folders(self.root))
    def test_houdini_texture_caches_are_not_assets(self):
        # photo.hdr.rat / photo.exr.tx are converted copies Houdini writes next to the source image;
        # a .rat or .tx saved on purpose (one extension) is still an asset.
        for path in ('Texture/HDR/sky_8k.hdr','Texture/HDR/sky_8k.hdr.rat','Texture/HDR/meadow.exr.rat',
                     'Texture/wood.png.tx','Texture/own.rat','Texture/own.tx','Texture/wood.v2.rat'):self.write(path)
        self.lib.scan(self.rid)
        self.assertEqual(sorted(r['relpath'] for r in self.lib.assets()),
                         ['Texture/HDR/sky_8k.hdr','Texture/own.rat','Texture/own.tx','Texture/wood.v2.rat'])
        from nanakusa_asset_library import core
        self.assertIsNone(core.classify('a/sky.HDR.RAT'));self.assertEqual(core.classify('a/own.rat'),'texture')

    def test_index_upgrade_drops_indexed_texture_caches_after_a_backup(self):
        import sqlite3
        self.write('Texture/HDR/sky.hdr');self.write('Texture/HDR/sky.hdr.rat')
        with self.lib.connect() as db:
            db.execute("INSERT INTO assets (id,root_id,relpath,label,kind) VALUES ('cache',?,'Texture/HDR/sky.hdr.rat','sky.hdr','texture')",(self.rid,))
            db.execute("INSERT INTO info VALUES ('cache','s','{}')")
            db.execute('PRAGMA user_version=4')   # an index written before 0.18.1
        again=Library(self.base/'index')
        self.assertEqual([r['id'] for r in again.assets()],[])
        with again.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM info WHERE id='cache'").fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],Library.VERSION)
        backups=list((self.base/'index'/'backups').glob('*_before_v5.sqlite3'));self.assertEqual(len(backups),1)
        saved=sqlite3.connect(str(backups[0]))
        try:self.assertEqual(saved.execute("SELECT relpath FROM assets WHERE id='cache'").fetchone()[0],'Texture/HDR/sky.hdr.rat')
        finally:saved.close()
        self.assertEqual(Library(self.base/'index').assets(),[])   # already upgraded: no second backup
        self.assertEqual(len(list((self.base/'index'/'backups').glob('*_before_v5.sqlite3'))),1)

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

    def test_stacks_are_decided_at_scan_time_per_folder_and_resolution(self):
        for rel in ('a/stone_1K_albedo.tif','a/stone_1K_normal.tif','a/stone_2K_albedo.tif','a/stone_2K_normal.tif','b/stone_1K_albedo.tif','b/stone_1K_albedo.png','b/stone_1K_roughness.tif','b/lone_albedo.tif','b/sky.hdr'):
            self.write('Texture/'+rel)
        self.lib.scan(self.rid)
        with self.lib.connect() as db:
            got={r['relpath'].split('/',1)[1]:(r['gkey'] or '').split('|',1)[-1] for r in db.execute('SELECT relpath,gkey FROM assets')}
        self.assertEqual(got['a/stone_1K_albedo.tif'],got['a/stone_1K_normal.tif']);self.assertNotEqual(got['a/stone_1K_albedo.tif'],got['a/stone_2K_albedo.tif'])
        self.assertEqual(got['a/stone_2K_albedo.tif'],got['a/stone_2K_normal.tif']);self.assertTrue(got['a/stone_1K_albedo.tif'])
        for single in ('b/stone_1K_albedo.tif','b/stone_1K_albedo.png','b/stone_1K_roughness.tif','b/lone_albedo.tif','b/sky.hdr'):self.assertEqual(got[single],'')   # ambiguous or alone

    def test_stream_orders_stacks_and_pages_exactly(self):
        for c in ('albedo','ao','normal'):self.write(f'Texture/PBR/Brick_1K_{c}.tif')
        for name in ('a_first','z_last'):self.write(f'3DModel/{name}.obj')
        self.write('Texture/PBR/sky.hdr')
        self.lib.scan(self.rid)
        every=self.lib.stream(stacked=False);self.assertEqual([e['label'] for e in every.next(50)],['a_first','Brick_1K_albedo','Brick_1K_ao','Brick_1K_normal','sky','z_last'])
        stream=self.lib.stream(stacked=True)
        first=stream.next(2);self.assertEqual([e['label'] for e in first],['a_first','Brick_1K'])
        self.assertEqual(len(first[1]['rows']),3);self.assertEqual(first[1]['rep']['label'],'Brick_1K_albedo')
        self.assertFalse(stream.finished);self.assertEqual([e['label'] for e in stream.next(10)],['sky','z_last']);self.assertTrue(stream.finished)
        self.assertEqual(stream.next(5),[])
        self.assertEqual(self.lib.count(),6);self.assertEqual(self.lib.count(kind='model'),2)
        # A search keeps only the matching images: two left still stack, one left is single.
        two=self.lib.stream(stacked=True,search='brick_1k_a').next(10);self.assertEqual([len(e['rows']) for e in two],[2])
        one=self.lib.stream(stacked=True,search='normal').next(10);self.assertEqual([(len(e['rows']),e['label']) for e in one],[(1,'Brick_1K_normal')])
        every.close();stream.close()

    def test_stream_follows_folders_favorites_and_moves(self):
        self.write('3DModel/A/one.obj');self.write('3DModel/A/Sub/two.obj');self.write('USD/pack/pack.usd');self.write('3DModel/B/three.obj')
        self.lib.scan(self.rid);rid=self.rid
        labels=lambda **f:[e['label'] for e in self.lib.stream(**f).next(50)]
        self.assertEqual(labels(root_id=rid,folder='3DModel/A'),['one','two']);self.assertEqual(labels(root_id=rid,folder='3DModel/A',recursive=False),['one'])
        self.assertEqual(labels(folder='USD',recursive=False),['pack']);self.assertEqual(labels(folder='USD/pack',recursive=False),['pack'])   # a package lists itself
        row=next(r for r in self.lib.assets() if r['label']=='three');self.lib.update(row['id'],favorite=1)
        self.assertEqual(labels(favorite=True),['three'])
        self.lib.relocate(rid,[dict(id=row['id'],relpath='3DModel/A/Sub/three.obj',thumbnail='')])
        self.assertEqual(labels(folder='3DModel/A/Sub',recursive=False),['three','two'])

    def test_older_index_is_upgraded_once(self):
        import sqlite3
        self.write('3DModel/a.obj');self.write('Texture/x_albedo.tif');self.write('Texture/x_normal.tif');self.lib.scan(self.rid)
        with self.lib.connect() as db:
            db.execute('UPDATE assets SET folder=NULL,pkg=NULL,stack=NULL,channel=NULL,gkey=NULL');db.execute('PRAGMA user_version=0')
        again=Library(self.base/'index')
        self.assertEqual([len(e['rows']) for e in again.stream(stacked=True).next(10)],[1,2])
        with again.connect() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],Library.VERSION)

    def test_asset_statistics_are_remembered_until_the_file_changes(self):
        self.write('3DModel/a.obj');self.lib.scan(self.rid);row=self.lib.assets()[0]
        self.assertIsNone(self.lib.info(row['id'],'1:1'))
        self.lib.save_info(row['id'],'1:1',{'info':{'Polygons':'3'}})
        self.assertEqual(self.lib.info(row['id'],'1:1'),{'info':{'Polygons':'3'}});self.assertIsNone(self.lib.info(row['id'],'2:1'))
        self.lib.save_info(row['id'],'2:1',{'info':{'Polygons':'4'}});self.assertIsNone(self.lib.info(row['id'],'1:1'))
        self.lib.remove_root(self.rid)
        with self.lib.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM info').fetchone()[0],0)

if __name__=='__main__':unittest.main()
