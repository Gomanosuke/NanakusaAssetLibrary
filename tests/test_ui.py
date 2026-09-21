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

    def test_catalog_creation_selection_and_registration_target(self):
        import hou
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/'asset';root.mkdir()
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            first=root/'Catalog'/'Props.db';second=root/'Catalog'/'Plants.db'
            if __import__('os').name=='nt':widget.default_root=str(root).lower()
            self.assertEqual(widget.catalog_path(),root/'Catalog'/'solaris_assets.db')
            with patch.object(ui.QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(first),'')):
                widget.new_catalog()
            self.assertTrue(first.is_file());self.assertTrue(hou.AssetGalleryDataSource(str(first)).isValid())
            self.assertEqual(widget.catalogs.currentText(),'Props.db')
            self.assertEqual(widget.catalog_path().resolve(),first.resolve())
            with patch.object(ui.QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(first),'')):
                with self.assertRaises(FileExistsError):widget.new_catalog()
            with patch.object(ui.QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(second),'')):
                widget.new_catalog()
            widget.catalogs.setCurrentIndex(next(i for i in range(widget.catalogs.count()) if ui.core.path_key(widget.catalogs.itemData(i))==ui.core.path_key(first)))
            self.assertEqual(widget.catalog_path().resolve(),first.resolve())
            self.assertIn('Open Catalog',[a.text() for a in widget.build_catalog_menu().actions()])
            with patch.object(widget,'selected_rows',return_value=[{'kind':'usd','label':'Chair','tags':''}]),patch.object(widget.library,'resolve',return_value=root/'USD'/'Chair.usda'),patch.object(widget,'thumbnail_path',return_value=None),patch.object(ui.ops,'register_catalog',return_value=(1,True)) as register:
                widget.add_catalog()
                self.assertEqual(register.call_args.args[1].resolve(),first.resolve())
            widget.close();widget.deleteLater()
            restored=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            self.assertEqual(restored.catalog_path().resolve(),first.resolve())
            self.assertEqual(restored.catalogs.currentText(),'Props.db')
            restored.close();restored.deleteLater()

if __name__=='__main__':unittest.main()
