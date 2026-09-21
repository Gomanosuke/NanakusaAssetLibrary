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
    def test_images_of_one_set_stack_and_others_stay_single(self):
        base='Texture/PBR/Misc/TCom_Various_HighRise_1K/TCom_Various_HighRise_1K_'
        data=rows(*(base+c+'.tif' for c in ('albedo','ao','normal','roughness')),'Texture/HDR/sky.hdr','Texture/PBR/Misc/lone_albedo.tif')
        entries=pbr.stack_entries(data)
        self.assertEqual([len(e['rows']) for e in entries],[4,1,1])
        stack=entries[0];self.assertEqual(stack['label'],'TCom_Various_HighRise_1K');self.assertEqual(stack['rep']['label'],'TCom_Various_HighRise_1K_albedo')
        self.assertEqual(stack['channels'],['base_color','roughness','normal','ao'])
        self.assertEqual([len(e['rows']) for e in pbr.stack_entries(data,enabled=False)],[1]*6)

    def test_resolutions_folders_and_ambiguous_channels_do_not_merge(self):
        data=rows('Texture/a/stone_1K_albedo.tif','Texture/a/stone_1K_normal.tif','Texture/a/stone_2K_albedo.tif','Texture/a/stone_2K_normal.tif',
                  'Texture/b/stone_1K_albedo.tif','Texture/b/stone_1K_albedo.png','Texture/b/stone_1K_roughness.tif')
        self.assertEqual([len(e['rows']) for e in pbr.stack_entries(data)],[2,2,1,1,1])   # the folder with two albedo images is left alone
        self.assertEqual(len(pbr.stack_entries(rows('Texture/a/x_albedo.usd',kind='usd'))),1)

class ChannelTokenTests(unittest.TestCase):
    def test_last_token_is_the_channel(self):
        # Words in the set name must not be mistaken for the channel.
        self.assertEqual(pbr.identify('Plaster_Rough_1K_normal.png'),('normal','plaster_rough'))
        self.assertEqual(pbr.identify('Metal_Plate_normal.png'),('normal','metal_plate'))
        self.assertEqual(pbr.stack_info('a/Plaster_Rough_1K_albedo.png')[3],'base_color')

if __name__=='__main__':unittest.main()
