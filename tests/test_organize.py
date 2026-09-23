from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library.core import Library
from nanakusa_asset_library import organize
from nanakusa_asset_library.organize import MoveError


class OrganizeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'assets';self.root.mkdir()
        for genre in ('USD','Texture','3DModel'):(self.root/genre).mkdir()
        self.data=self.base/'data';self.lib=Library(self.data);self.rid=self.lib.add_root(self.root)
    def tearDown(self):self.temp.cleanup()
    def write(self,relative,content='x'):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content);return path
    def scan(self):self.lib.scan(self.rid)
    def row(self,relpath):return next(r for r in self.lib.assets(missing=True) if r['relpath']==relpath)
    def move(self,relpaths,dest):
        return organize.move_assets(self.lib,[self.row(r) for r in relpaths],dest,self.data)

    def test_model_moves_with_thumbnails_and_keeps_metadata(self):
        self.write('3DModel/Misc/chair.fbx');self.write('3DModel/Misc/chair_thumbnail.png');self.write('3DModel/Misc/chair.preview.jpg')
        (self.root/'3DModel/Props').mkdir();self.scan()
        old=self.row('3DModel/Misc/chair.fbx');self.lib.update(old['id'],tags='wood red',favorite=1,notes='keep')
        result=self.move(['3DModel/Misc/chair.fbx'],'3DModel/Props')
        self.assertEqual(result['moved'],1);self.assertTrue(result['backup'].is_file())
        for name in ('chair.fbx','chair_thumbnail.png','chair.preview.jpg'):
            self.assertTrue((self.root/'3DModel/Props'/name).is_file(),name)
            self.assertFalse((self.root/'3DModel/Misc'/name).exists(),name)
        new=self.row('3DModel/Props/chair.fbx')
        self.assertEqual((new['id'],new['tags'],new['favorite'],new['notes']),(old['id'],'wood red',1,'keep'))
        self.scan()   # a rescan must keep the same asset instead of creating a new one
        rows=self.lib.assets(missing=True);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],old['id']);self.assertEqual(rows[0]['tags'],'wood red')

    def test_usd_files_sharing_a_folder_move_only_with_the_folder(self):
        for rel in ('USD/Misc/Plant_OL/Plant_Big.usd','USD/Misc/Plant_OL/Plant_Small.usd','USD/Misc/Plant_OL/textures/a.png'):self.write(rel)
        (self.root/'USD/Props').mkdir();self.scan()
        big=self.row('USD/Misc/Plant_OL/Plant_Big.usd');self.lib.update(big['id'],tags='plant')
        with self.assertRaises(MoveError) as caught:self.move(['USD/Misc/Plant_OL/Plant_Big.usd'],'USD/Props')
        self.assertIn('Move the folder "Plant_OL" instead',str(caught.exception))
        self.assertTrue((self.root/'USD/Misc/Plant_OL/Plant_Big.usd').is_file())   # nothing moved
        # its hidden subfolders are not a place to put assets either
        self.write('USD/Misc/loose.usdz');self.scan()
        with self.assertRaises(MoveError):self.move(['USD/Misc/loose.usdz'],'USD/Misc/Plant_OL/textures')
        self.move(['USD/Misc/loose.usdz'],'USD/Misc/Plant_OL')   # the folder itself is fine
        # the folder moves with all of its files and both assets keep their ids and tags
        organize.move_folders(self.lib,self.rid,['USD/Misc/Plant_OL'],'USD/Props',self.data)
        self.assertTrue((self.root/'USD/Props/Plant_OL/textures/a.png').is_file())
        moved=self.row('USD/Props/Plant_OL/Plant_Big.usd');self.assertEqual((moved['id'],moved['tags']),(big['id'],'plant'))

    def test_usd_package_moves_as_folder_with_thumbnail_and_dependencies(self):
        self.write('USD/Misc/Chair/Chair.usd');self.write('USD/Misc/Chair/thumbnail.png');self.write('USD/Misc/Chair/textures/a.png')
        (self.root/'USD/Props').mkdir();self.scan()
        old=self.row('USD/Misc/Chair/Chair.usd');self.lib.update(old['id'],tags='seat')
        result=self.move(['USD/Misc/Chair/Chair.usd'],'USD/Props')
        for name in ('Chair.usd','thumbnail.png','textures/a.png'):self.assertTrue((self.root/'USD/Props/Chair'/name).is_file())
        self.assertFalse((self.root/'USD/Misc/Chair').exists())
        new=self.row('USD/Props/Chair/Chair.usd');self.assertEqual((new['id'],new['tags']),(old['id'],'seat'))
        self.assertEqual([(Path(o).name,Path(n).parent.name) for o,n in result['catalog']],[('Chair.usd','Chair')])
        self.assertTrue(Path(result['catalog'][0][1]).samefile(self.root/'USD/Props/Chair/Chair.usd'))   # Windows may spell temp folders in short form

    def test_standalone_usdz_and_custom_thumbnail_follow(self):
        self.write('USD/post.usdz');self.write('USD/post_thumbnail.png');(self.root/'USD/Props').mkdir()
        custom=self.write('USD/post_thumbnail.jpg');self.scan()
        row=self.row('USD/post.usdz');self.lib.update(row['id'],thumbnail=str(custom))
        self.move(['USD/post.usdz'],'USD/Props')
        self.assertTrue((self.root/'USD/Props/post_thumbnail.png').is_file());self.assertTrue((self.root/'USD/Props/post_thumbnail.jpg').is_file())
        self.assertTrue(Path(self.row('USD/Props/post.usdz')['thumbnail']).samefile(self.root/'USD/Props/post_thumbnail.jpg'))

    def test_texture_thumbnail_cache_needs_no_move(self):
        self.write('Texture/PBR/stone.tif');(self.root/'Texture/Misc').mkdir();self.scan()
        old=self.row('Texture/PBR/stone.tif')
        self.move(['Texture/PBR/stone.tif'],'Texture/Misc')
        self.assertEqual(self.row('Texture/Misc/stone.tif')['id'],old['id'])

    def test_shared_thumbnail_is_copied_when_other_asset_stays(self):
        self.write('3DModel/a/chair.fbx');self.write('3DModel/a/chair.obj');self.write('3DModel/a/chair_thumbnail.png');(self.root/'3DModel/b').mkdir()
        self.scan();self.move(['3DModel/a/chair.fbx'],'3DModel/b')
        self.assertTrue((self.root/'3DModel/a/chair_thumbnail.png').is_file());self.assertTrue((self.root/'3DModel/b/chair_thumbnail.png').is_file())

    def test_shared_thumbnail_moves_once_when_both_move(self):
        self.write('3DModel/a/chair.fbx');self.write('3DModel/a/chair.obj');self.write('3DModel/a/chair_thumbnail.png');(self.root/'3DModel/b').mkdir()
        self.scan();self.move(['3DModel/a/chair.fbx','3DModel/a/chair.obj'],'3DModel/b')
        self.assertFalse((self.root/'3DModel/a/chair_thumbnail.png').exists());self.assertTrue((self.root/'3DModel/b/chair_thumbnail.png').is_file())

    def test_refusals_leave_everything_untouched(self):
        self.write('3DModel/a/chair.fbx');self.write('3DModel/b/chair.fbx');self.write('USD/Chair/Chair.usd');self.write('Texture/t.png')
        self.scan();before=self.lib.assets(missing=True)
        for rows,dest in ((['3DModel/a/chair.fbx'],'3DModel/b'),(['3DModel/a/chair.fbx'],'USD'),
                          (['Texture/t.png'],'3DModel/a'),(['3DModel/a/chair.fbx'],'USD/Chair'),(['3DModel/a/chair.fbx'],'Other'),
                          (['3DModel/a/chair.fbx'],''),(['3DModel/a/chair.fbx'],'3DModel/missing')):
            with self.assertRaises(MoveError,msg=str(dest)):self.move(rows,dest)
        self.assertEqual(self.lib.assets(missing=True),before)
        self.assertTrue((self.root/'3DModel/a/chair.fbx').is_file())

    def test_same_folder_is_reported(self):
        self.write('3DModel/a/chair.fbx');self.scan()
        with self.assertRaises(MoveError):self.move(['3DModel/a/chair.fbx'],'3DModel/a')

    def test_failure_rolls_back_files_and_index(self):
        self.write('3DModel/a/chair.fbx');self.write('3DModel/a/chair_thumbnail.png');(self.root/'3DModel/b').mkdir();self.scan()
        original=Path.rename;calls=[]
        def flaky(path,target):
            calls.append(path)
            if len(calls)==2:raise PermissionError('locked')
            return original(path,target)
        with patch.object(Path,'rename',flaky):
            with self.assertRaises(MoveError):self.move(['3DModel/a/chair.fbx'],'3DModel/b')
        self.assertTrue((self.root/'3DModel/a/chair.fbx').is_file());self.assertTrue((self.root/'3DModel/a/chair_thumbnail.png').is_file())
        self.assertFalse((self.root/'3DModel/b/chair.fbx').exists());self.assertEqual(self.row('3DModel/a/chair.fbx')['present'],1)

    def test_index_failure_rolls_back_files(self):
        self.write('3DModel/a/chair.fbx');(self.root/'3DModel/b').mkdir();self.scan()
        with patch.object(Library,'relocate',side_effect=RuntimeError('db')):
            with self.assertRaises(RuntimeError):self.move(['3DModel/a/chair.fbx'],'3DModel/b')
        self.assertTrue((self.root/'3DModel/a/chair.fbx').is_file());self.assertFalse((self.root/'3DModel/b/chair.fbx').exists())

    def test_folder_move_updates_every_asset_and_keeps_tags(self):
        self.write('3DModel/Misc/Rocks/big.fbx');self.write('3DModel/Misc/Rocks/big_thumbnail.png');self.write('3DModel/Misc/Rocks/Deep/small.obj')
        self.write('USD/Misc/Rocks/Rocks.usd');(self.root/'3DModel/Environment').mkdir();self.scan()
        ids={r['relpath']:r for r in self.lib.assets()}
        self.lib.update(ids['3DModel/Misc/Rocks/big.fbx']['id'],tags='stone')
        custom=self.write('3DModel/Misc/Rocks/pick.png');self.lib.update(ids['3DModel/Misc/Rocks/Deep/small.obj']['id'],thumbnail=str(custom))
        result=organize.move_folder(self.lib,self.rid,'3DModel/Misc/Rocks','3DModel/Environment',self.data)
        self.assertEqual(result['moved'],2);self.assertEqual(result['new_rel'],'3DModel/Environment/Rocks')
        self.assertFalse((self.root/'3DModel/Misc/Rocks').exists())
        self.assertTrue((self.root/'3DModel/Environment/Rocks/big_thumbnail.png').is_file())
        moved=self.row('3DModel/Environment/Rocks/big.fbx');self.assertEqual((moved['id'],moved['tags']),(ids['3DModel/Misc/Rocks/big.fbx']['id'],'stone'))
        small=self.row('3DModel/Environment/Rocks/Deep/small.obj')
        self.assertTrue(Path(small['thumbnail']).samefile(self.root/'3DModel/Environment/Rocks/pick.png'))
        self.assertEqual(self.row('USD/Misc/Rocks/Rocks.usd')['present'],1)   # other genre untouched

    def test_folder_move_refusals(self):
        for rel in ('3DModel/a/x.obj','3DModel/a/b/x.obj','3DModel/c/x.obj','USD/Chair/Chair.usd','USD/p/x.usd'):self.write(rel)
        cases=(('3DModel','3DModel/c'),('3DModel/a','3DModel/a'),('3DModel/a','3DModel/a/b'),('3DModel/a/b','3DModel/a'),
               ('3DModel/a','USD/p'),('3DModel/a','USD/Chair'),('3DModel/a','3DModel/missing'))
        for src,dest in cases:
            with self.assertRaises(MoveError,msg=src+' -> '+dest):organize.move_folder(self.lib,self.rid,src,dest,self.data)
        (self.root/'3DModel/c/a').mkdir()
        with self.assertRaises(MoveError):organize.move_folder(self.lib,self.rid,'3DModel/a','3DModel/c',self.data)

    def test_texture_set_folders_move_together_with_tags(self):
        names=('albedo','ao','emissive','height','normal','roughness')
        for kit in ('Brick_Wall_1K','Plaster_Fine_1K'):
            for n in names:self.write('Texture/PBR/Misc/%s/%s_%s.tif'%(kit,kit,n))
        (self.root/'Texture/PBR/Masonry').mkdir();self.scan()
        rows=self.lib.assets();self.assertEqual(len(rows),12)
        tagged=self.row('Texture/PBR/Misc/Brick_Wall_1K/Brick_Wall_1K_albedo.tif');self.lib.update(tagged['id'],tags='tower',favorite=1)
        result=organize.move_folders(self.lib,self.rid,['Texture/PBR/Misc/Brick_Wall_1K','Texture/PBR/Misc/Plaster_Fine_1K'],'Texture/PBR/Masonry',self.data)
        self.assertEqual((result['folders'],result['moved']),(2,12))
        self.assertEqual(sorted(p.name for p in (self.root/'Texture/PBR/Masonry').iterdir()),['Brick_Wall_1K','Plaster_Fine_1K'])
        self.assertFalse(any((self.root/'Texture/PBR/Misc').iterdir()))
        moved=self.row('Texture/PBR/Masonry/Brick_Wall_1K/Brick_Wall_1K_albedo.tif')
        self.assertEqual((moved['id'],moved['tags'],moved['favorite']),(tagged['id'],'tower',1))
        self.scan();self.assertEqual(len(self.lib.assets()),12);self.assertEqual(self.row(moved['relpath'])['tags'],'tower')

    def test_multi_folder_move_skips_nested_and_is_all_or_nothing(self):
        for rel in ('3DModel/a/x.obj','3DModel/a/inner/y.obj','3DModel/b/z.obj','3DModel/c/b/w.obj'):self.write(rel)
        (self.root/'3DModel/dest').mkdir();self.scan()
        result=organize.move_folders(self.lib,self.rid,['3DModel/a','3DModel/a/inner'],'3DModel/dest',self.data)
        self.assertEqual(result['folders'],1);self.assertTrue((self.root/'3DModel/dest/a/inner/y.obj').is_file())
        # b would collide with c/b once both go to c's sibling: the second folder is refused, nothing moves.
        (self.root/'3DModel/c/b').mkdir(exist_ok=True)
        with self.assertRaises(MoveError):organize.move_folders(self.lib,self.rid,['3DModel/b'],'3DModel/c',self.data)
        self.assertTrue((self.root/'3DModel/b/z.obj').is_file())
        with self.assertRaises(MoveError):organize.move_folders(self.lib,self.rid,['3DModel/dest/a','3DModel/b'],'3DModel/dest/a',self.data)
        self.assertTrue((self.root/'3DModel/b/z.obj').is_file())
        with self.assertRaises(MoveError):organize.move_folders(self.lib,self.rid,['3DModel/dest/a'],'3DModel/dest',self.data)

    def test_new_file_at_old_path_does_not_reuse_moved_id(self):
        self.write('3DModel/a/chair.fbx');(self.root/'3DModel/b').mkdir();self.scan()
        old=self.row('3DModel/a/chair.fbx');self.move(['3DModel/a/chair.fbx'],'3DModel/b')
        self.write('3DModel/a/chair.fbx','new');self.scan()   # same computed id as the moved asset
        rows=self.lib.assets();self.assertEqual(len(rows),2);self.assertEqual(len({r['id'] for r in rows}),2)
        self.assertEqual(self.row('3DModel/b/chair.fbx')['id'],old['id'])

    def test_stale_row_at_destination_is_replaced(self):
        self.write('3DModel/a/chair.fbx');(self.root/'3DModel/b').mkdir();self.scan()
        with self.lib.connect() as db:
            db.execute("INSERT INTO assets (id,root_id,relpath,label,kind,present) VALUES ('stale',?,?,?,?,0)",(self.rid,'3DModel/b/chair.fbx','chair','model'))
        old=self.row('3DModel/a/chair.fbx');self.move(['3DModel/a/chair.fbx'],'3DModel/b')
        self.assertEqual(self.row('3DModel/b/chair.fbx')['id'],old['id'])


if __name__=='__main__':unittest.main()
