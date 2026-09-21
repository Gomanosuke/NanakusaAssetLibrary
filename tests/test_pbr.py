from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library import pbr

class PbrTests(unittest.TestCase):
    def test_group_sets_and_preserve_unknown_images(self):
        groups=pbr.group_images(['stone_1K_albedo.tif','stone_1K_nor_gl.tif','wood_roughness.png','reference.png'])
        self.assertEqual(len(groups),3)
        self.assertEqual(set(groups[0]['maps']),{'base_color','normal'})
        self.assertEqual(pbr.identify('stone_normalgl.png'),('normal','stone'))

    def test_ambiguous_resolution_and_directx_rejected(self):
        with self.assertRaises(ValueError):pbr.group_images(['stone_1K_albedo.tif','stone_2K_albedo.tif'])
        for name in ('stone_normal_dx.png','stone_normaldx.png','stone_nor_dx.png'):
            with self.assertRaises(ValueError):pbr.group_images([name])

if __name__=='__main__':unittest.main()
