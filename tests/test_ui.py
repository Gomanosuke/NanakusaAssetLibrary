from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from hutil.PySide import QtWidgets
from nanakusa_asset_library import ui


class LibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_context_actions_and_metadata_stay_with_active_asset(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for rel in ('3DModel/mesh.obj','USD/item/item.usda'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('placeholder')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            model=next(i for i in range(widget.items.count()) if widget.items.item(i).data(ui.ROLE)['kind']=='model')
            usd=next(i for i in range(widget.items.count()) if widget.items.item(i).data(ui.ROLE)['kind']=='usd')
            widget.items.setCurrentRow(model);aid=widget.metadata_id
            labels=[a.text() for a in widget.build_asset_menu().actions()]
            self.assertIn('Import Selected',labels);self.assertIn('Show in Explorer',labels);self.assertNotIn('Add Catalog',labels)
            widget.tags.setText('stone');widget.tags_edited('stone');widget.star.setChecked(True)
            widget.items.setCurrentRow(usd)
            rows={r['id']:r for r in widget.library.assets()}
            self.assertEqual(rows[aid]['tags'],'stone');self.assertEqual(rows[aid]['favorite'],1)
            self.assertEqual(rows[widget.metadata_id]['tags'],'')
            self.assertIn('Add Catalog',[a.text() for a in widget.build_asset_menu().actions()])
            self.assertFalse(any(b.text() in ('Import Selected','Copy Paths') for b in widget.findChildren(QtWidgets.QPushButton)))
            self.assertFalse(widget.preview.isHidden())
            self.assertEqual(widget.library.backup_index().parent,base/'data'/'backups')
            widget.close();widget.deleteLater()

if __name__=='__main__':unittest.main()
