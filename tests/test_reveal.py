from pathlib import Path
import sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from nanakusa_asset_library import reveal

class RevealTests(unittest.TestCase):
    def test_files_are_grouped_per_folder_without_duplicates(self):
        groups=reveal.group_by_folder(['C:/lib/a/x.usdz','C:/lib/b/y.usdz','C:/lib/a/z.usdz','C:/lib/a/x.usdz'])
        self.assertEqual(list(groups),[Path('C:/lib/a'),Path('C:/lib/b')])
        self.assertEqual(groups[Path('C:/lib/a')],[Path('C:/lib/a/x.usdz'),Path('C:/lib/a/z.usdz')])

    def test_one_window_per_folder_with_its_files_selected(self):
        opened=[]
        with patch.object(reveal,'WINDOWS',True),patch.object(reveal,'_select_with_shell',return_value=True) as shell,\
             patch.object(reveal.subprocess,'Popen') as popen:
            reveal.select_in_file_browser(['C:/lib/a/x.usdz','C:/lib/a/z.usdz','C:/lib/b/y.png'],opened.append)
        self.assertEqual([c.args for c in shell.call_args_list],
                         [(Path('C:/lib/a'),[Path('C:/lib/a/x.usdz'),Path('C:/lib/a/z.usdz')]),(Path('C:/lib/b'),[Path('C:/lib/b/y.png')])])
        popen.assert_not_called();self.assertEqual(opened,[])

    def test_falls_back_to_explorer_select_then_to_opening_the_folder(self):
        opened=[]
        with patch.object(reveal,'WINDOWS',True),patch.object(reveal,'_select_with_shell',side_effect=OSError('no shell')),\
             patch.object(reveal.subprocess,'Popen') as popen:
            reveal.select_in_file_browser(['C:/my lib/x.usdz'],opened.append)
        self.assertEqual(popen.call_args.args[0],'explorer /select,"%s"'%Path('C:/my lib/x.usdz'))   # quoted after the comma
        self.assertEqual(opened,[])
        with patch.object(reveal,'WINDOWS',True),patch.object(reveal,'_select_with_shell',return_value=False),\
             patch.object(reveal.subprocess,'Popen',side_effect=OSError):
            reveal.select_in_file_browser(['C:/lib/x.usdz'],opened.append)
        self.assertEqual(opened,[Path('C:/lib')])
        with patch.object(reveal,'WINDOWS',False),patch.object(reveal,'_select_with_shell') as shell:
            reveal.select_in_file_browser(['/lib/y.usdz'],opened.append)
        shell.assert_not_called();self.assertEqual(opened[-1],Path('/lib'))

if __name__=='__main__':unittest.main()
