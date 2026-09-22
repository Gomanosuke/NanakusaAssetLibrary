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


def rows(*relpaths,kind='texture'):
    return [dict(id=str(i),root_id='r',kind=kind,label=Path(p).stem,relpath=p) for i,p in enumerate(relpaths)]

class StackTests(unittest.TestCase):
    def test_images_of_one_set_form_a_stack_led_by_the_base_color(self):
        base='Texture/PBR/Misc/Brick_Wall_1K/Brick_Wall_1K_'
        [stack]=pbr.group_entries(rows(*(base+c+'.tif' for c in ('ao','normal','roughness','albedo'))))
        self.assertEqual(stack['label'],'Brick_Wall_1K');self.assertEqual(stack['rep']['label'],'Brick_Wall_1K_albedo')
        self.assertEqual(stack['channels'],['base_color','roughness','normal','ao']);self.assertEqual(len(stack['rows']),4)

    def test_a_single_image_an_ambiguous_set_or_an_unrecognised_name_stays_single(self):
        self.assertEqual([len(e['rows']) for e in pbr.group_entries(rows('Texture/a/lone_albedo.tif'))],[1])
        self.assertEqual([len(e['rows']) for e in pbr.group_entries(rows('Texture/b/stone_1K_albedo.tif','Texture/b/stone_1K_albedo.png','Texture/b/stone_1K_roughness.tif'))],[1,1,1])
        self.assertEqual([len(e['rows']) for e in pbr.group_entries(rows('Texture/b/sky.hdr','Texture/b/reference.png'))],[1,1])

class ChannelTokenTests(unittest.TestCase):
    def test_last_token_is_the_channel(self):
        # Words in the set name must not be mistaken for the channel.
        self.assertEqual(pbr.identify('Plaster_Rough_1K_normal.png'),('normal','plaster_rough'))
        self.assertEqual(pbr.identify('Metal_Plate_normal.png'),('normal','metal_plate'))
        self.assertEqual(pbr.stack_info('a/Plaster_Rough_1K_albedo.png')[3],'base_color')

if __name__=='__main__':unittest.main()
