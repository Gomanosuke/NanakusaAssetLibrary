from pathlib import Path
import sys,tempfile,unittest,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library import embedded,storage
try:
    from pxr import Usd,UsdGeom,UsdMedia,UsdUtils,Sdf
except ImportError:
    Usd=None

PNG=b'\x89PNG\r\n\x1a\n'+b'preview'


def package(path,files):
    with zipfile.ZipFile(path,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    return Path(path)


class EmbeddedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_common_names_are_found_and_textures_are_never_previews(self):
        for files,expected in (
            ({'a.usdc':b'x','Thumbnails/thumbnail.png':PNG,'0/albedo.jpg':b'tex'},PNG),
            ({'a.usdc':b'x','thumbnail.jpg':b'jpegdata','0/albedo.jpg':b'tex'},b'jpegdata'),
            ({'a.usdc':b'x','PREVIEW.PNG':PNG},PNG),
            ({'a.usdc':b'x','.thumbs/256x256/a.png':b'small','.thumbs/512x512/a.png':b'the-larger-one'},b'the-larger-one'),
            ({'a.usdc':b'x','Thumbnails/other.png':b'other','Thumbnails/my_thumbnail.png':b'named'},b'named')):
            found=embedded.thumbnail(package(self.base/'a.usdz',files))
            self.assertIsNotNone(found,files.keys());self.assertEqual(found[1],expected,files.keys())
        for files in ({'a.usdc':b'x','0/albedo.jpg':b'tex','textures/normal.png':PNG},{'a.usdc':b'x'},{'a.usdc':b'x','Thumbnails/empty.png':b''}):
            self.assertIsNone(embedded.thumbnail(package(self.base/'b.usdz',files)))

    def test_non_packages_and_broken_files_are_ignored(self):
        plain=self.base/'a.usd';plain.write_text('#usda 1.0')
        broken=self.base/'broken.usdz';broken.write_bytes(b'not a zip')
        self.assertIsNone(embedded.thumbnail(plain));self.assertIsNone(embedded.thumbnail(broken));self.assertIsNone(embedded.thumbnail(self.base/'missing.usdz'))

    def test_extract_writes_only_the_cache_and_keeps_the_package(self):
        source=package(self.base/'a.usdz',{'a.usdc':b'x','Thumbnails/thumbnail.png':PNG});before=source.read_bytes()
        target=embedded.extract(source,self.base/'cache'/'usdz_id_1_2')
        self.assertEqual(target.name,'usdz_id_1_2.png');self.assertEqual(target.read_bytes(),PNG)
        self.assertEqual(source.read_bytes(),before);self.assertEqual([p.name for p in (self.base/'cache').iterdir()],['usdz_id_1_2.png'])
        self.assertIsNone(embedded.extract(package(self.base/'n.usdz',{'a.usdc':b'x'}),self.base/'cache'/'none'))

    @unittest.skipIf(Usd is None,'needs pxr')
    def test_usd_standard_asset_previews_are_read(self):
        usda=self.base/'asset.usda';(self.base/'images').mkdir();(self.base/'images'/'cover.png').write_bytes(PNG)
        stage=Usd.Stage.CreateNew(str(usda));cube=UsdGeom.Cube.Define(stage,'/Asset');stage.SetDefaultPrim(cube.GetPrim())
        UsdMedia.AssetPreviewsAPI.Apply(cube.GetPrim()).SetDefaultThumbnails(UsdMedia.AssetPreviewsAPI.Thumbnails(Sdf.AssetPath('./images/cover.png')))
        stage.GetRootLayer().Save()
        usdz=self.base/'asset.usdz';self.assertTrue(UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(usda)),str(usdz)))
        suffix,data=embedded.thumbnail(usdz);self.assertEqual((suffix,data),('.png',PNG))   # the file name is not a convention: it comes from the metadata


class StorageTests(unittest.TestCase):
    def row(self,relative):
        return dict(root_path='/assets',relpath=relative,kind='usd',id='abc',mtime=1,size=10,thumbnail='')

    def test_embedded_cache_is_a_last_resort_in_the_data_folder(self):
        row=self.row('USD/tree.usdz');candidates=storage.thumbnail_candidates(row,'/data')
        cache=[c for c in candidates if c.parent==Path('/data/thumbnails')]
        self.assertEqual([c.suffix for c in cache],['.png','.jpg','.jpeg']);self.assertEqual(candidates[-3:],cache)
        self.assertEqual(candidates.index(storage.thumbnail_destination(row,'/data')),0)   # generated thumbnails win
        self.assertFalse([c for c in storage.thumbnail_candidates(self.row('USD/tree.usd'),'/data') if c.parent==Path('/data/thumbnails')])

if __name__=='__main__':unittest.main()
