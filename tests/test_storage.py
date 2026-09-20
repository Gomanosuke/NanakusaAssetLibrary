from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library.storage import thumbnail_destination, thumbnail_candidates


class StorageTests(unittest.TestCase):
    def row(self,relative,kind):
        return dict(root_path='/assets',relpath=relative,kind=kind,id='abc',mtime=1,size=10,thumbnail='')

    def test_model_thumbnail_is_specific_to_filename(self):
        for name in ('mesh.fbx','mesh.bgeo.sc','mesh.geo.sc'):
            row=self.row('3DModel/'+name,'model')
            self.assertEqual(thumbnail_destination(row,'/data'),Path('/assets/3DModel/mesh_thumbnail.png'))
        self.assertNotIn(Path('/assets/3DModel/thumbnail.png'),thumbnail_candidates(row,'/data'))

    def test_usd_thumbnail_matches_component_output_layout(self):
        row=self.row('USD/tree/tree.usd','usd')
        self.assertEqual(thumbnail_destination(row,'/data'),Path('/assets/USD/tree/thumbnail.png'))
        self.assertIn(Path('/assets/USD/tree/thumbnail.jpg'),thumbnail_candidates(row,'/data'))

    def test_only_texture_cache_is_in_data_and_invalidates_old_padded_cache(self):
        row=self.row('Texture/color.tif','texture')
        result=thumbnail_destination(row,'/data')
        self.assertEqual(result.parent,Path('/data/thumbnails'))
        self.assertTrue(result.name.startswith('square_v2_'))

if __name__=='__main__':unittest.main()
