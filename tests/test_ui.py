from pathlib import Path
import json,sys,tempfile,unittest
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

    def test_tree_drops_move_assets_and_folders_keeping_metadata(self):
        from hutil.PySide import QtCore,QtGui
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for rel in ('3DModel/Misc/chair.obj','3DModel/Misc/chair_thumbnail.png','3DModel/Sub/table.obj','USD/Misc/Lamp/Lamp.usda'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('placeholder')
            (root/'3DModel'/'Props').mkdir()
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            rid=widget.library.roots()[0]['id'];widget.library.scan(rid);widget.refresh()
            chair=next(r for r in widget.rows if r['label']=='chair');widget.library.update(chair['id'],tags='oak',favorite=1);widget.refresh()
            def target(rel):
                it=QtWidgets.QTreeWidgetItemIterator(widget.tree)
                while it.value():
                    if it.value().data(0,ui.ROLE) and it.value().data(0,ui.ROLE)[1]==rel:return it.value()
                    it+=1
            props=target('3DModel/Props').data(0,ui.ROLE)
            self.assertTrue(widget.can_drop({'assets':[chair['id']]},props))
            self.assertFalse(widget.can_drop({'assets':[chair['id']]},target('USD/Misc').data(0,ui.ROLE)))
            self.assertFalse(widget.can_drop({'assets':[chair['id']]},(rid,'',str(root))))
            self.assertFalse(widget.can_drop({'folders':[{'root_id':rid,'rel':'3DModel'}]},props))
            # A real drag event over the tree item is accepted and emits the drop once.
            mime=ui.dragdrop.mime_data_many([{'path':str(root/chair['relpath']),'kind':'model','label':'chair'}],[chair['id']])
            tree=widget.tree;tree.resize(300,600);tree.expandAll();point=QtCore.QPointF(tree.visualItemRect(target('3DModel/Props')).center())
            got=[];tree.dropped.connect(lambda payload,t:got.append((payload,t)))
            event=QtGui.QDragMoveEvent(point.toPoint(),QtCore.Qt.DropAction.MoveAction|QtCore.Qt.DropAction.CopyAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
            tree.dragMoveEvent(event);self.assertTrue(event.isAccepted())
            tree.dropEvent(QtGui.QDropEvent(point,QtCore.Qt.DropAction.MoveAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier))
            self.assertEqual(len(got),1)
            bad=QtGui.QDragMoveEvent(QtCore.QPoint(tree.visualItemRect(target('USD/Misc')).center()),QtCore.Qt.DropAction.MoveAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
            tree.dragMoveEvent(bad);self.assertFalse(bad.isAccepted())
            widget.organize_drop(*got[0])
            self.assertTrue((root/'3DModel/Props/chair.obj').is_file());self.assertTrue((root/'3DModel/Props/chair_thumbnail.png').is_file())
            moved=next(r for r in widget.library.assets() if r['label']=='chair')
            self.assertEqual((moved['id'],moved['tags'],moved['favorite']),(chair['id'],'oak',1))
            self.assertIn('Moved 1 asset(s) to 3DModel/Props',widget.status.text())
            widget.organize_drop({'folders':[{'root_id':rid,'rel':'3DModel/Sub'}]},target('3DModel/Props').data(0,ui.ROLE))
            self.assertTrue((root/'3DModel/Props/Sub/table.obj').is_file())
            self.assertEqual(widget.current_folder()[1],'3DModel/Props/Sub')
            # Starting a drag from the selected folders carries every selected folder.
            captured=[]
            class FakeDrag:
                def __init__(self,source):pass
                def setMimeData(self,mime):captured.append(mime)
                def exec(self,*a):return None
            tree.clearSelection()
            for rel in ('3DModel/Props/Sub','3DModel/Misc'):target(rel).setSelected(True)
            target('USD').setSelected(True)   # fixed categories are never dragged
            with patch.object(ui.dragdrop.QtGui,'QDrag',FakeDrag):tree.startDrag(QtCore.Qt.DropAction.MoveAction)
            self.assertEqual(sorted(f['rel'] for f in tree.payload(captured[0])['folders']),['3DModel/Misc','3DModel/Props/Sub'])
            tree.clearSelection();target('USD').setSelected(True);captured.clear()
            with patch.object(ui.dragdrop.QtGui,'QDrag',FakeDrag):tree.startDrag(QtCore.Qt.DropAction.MoveAction)
            self.assertEqual(captured,[])
            # If Qt never delivers drag events to the tree, a drag released over a folder still moves.
            chair=widget.rows[0]   # whatever the current folder lists
            hovered=target('3DModel/Misc')
            point=tree.viewport().mapToGlobal(tree.visualItemRect(hovered).center())
            got.clear()
            payload={'assets':[chair['id']]}
            with patch.object(ui.dragdrop.QtGui.QCursor,'pos',return_value=point),patch.object(ui.dragdrop.FolderTree,'left_button_down',return_value=False):
                tree.begin_tracking(payload);tree._follow();self.assertIs(tree._hover,hovered)
                tree.end_tracking(QtCore.Qt.DropAction.IgnoreAction)
                self.assertEqual(len(got),1);self.assertEqual(got[0][1][1],'3DModel/Misc');self.assertIsNone(tree._hover)
                got.clear();tree.begin_tracking(payload);tree.end_tracking(QtCore.Qt.DropAction.CopyAction);self.assertEqual(got,[])   # accepted elsewhere
                tree.begin_tracking(payload);tree._dropped=True;tree.end_tracking(QtCore.Qt.DropAction.IgnoreAction);self.assertEqual(got,[])   # normal drop already handled
            with patch.object(ui.dragdrop.QtGui.QCursor,'pos',return_value=point),patch.object(ui.dragdrop.FolderTree,'left_button_down',return_value=True):
                tree.begin_tracking(payload);tree.end_tracking(QtCore.Qt.DropAction.IgnoreAction);self.assertEqual(got,[])   # Esc: button still down
            with patch.object(ui.dragdrop.QtGui.QCursor,'pos',return_value=tree.viewport().mapToGlobal(QtCore.QPoint(-50,-50))),patch.object(ui.dragdrop.FolderTree,'left_button_down',return_value=False):
                tree.begin_tracking(payload);tree.end_tracking(QtCore.Qt.DropAction.IgnoreAction);self.assertEqual(got,[])   # released outside the tree
            # Selecting a folder must not list it inside the mouse press: a slow listing froze drags.
            calls=[]
            with patch.object(widget,'reset_page',side_effect=lambda *a:calls.append(1)):
                widget.folder_timer.timeout.disconnect();widget.folder_timer.timeout.connect(widget.reset_page)
                tree.setCurrentItem(target('3DModel/Props'));self.assertEqual(calls,[]);self.assertTrue(widget.folder_timer.isActive())
                tree.begin_tracking({'assets':[]});self.assertFalse(widget.folder_timer.isActive())   # a drag holds the listing back
                tree._poll.stop();tree.end_tracking(QtCore.Qt.DropAction.CopyAction);self.assertTrue(widget.folder_timer.isActive())   # ...and it runs afterwards
                widget.folder_timer.stop()
            widget.thumb_pending.add('busy')
            with self.assertRaises(ValueError):widget.organize_drop({'assets':[moved['id']]},target('3DModel/Misc').data(0,ui.ROLE))
            widget.close();widget.deleteLater()

    def test_pbr_sets_stack_drag_and_share_metadata(self):
        from hutil.PySide import QtCore
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            names=['stone_1K_'+c+'.tif' for c in ('albedo','roughness','normal','ao')]+['sky.hdr']
            for name in names:
                p=root/'Texture'/'PBR'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'placeholder')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            self.assertTrue(widget.stack.isChecked());self.assertEqual(widget.items.count(),2)
            stack=next(widget.items.item(i) for i in range(2) if len(widget.items.item(i).data(ui.STACK_ROLE))==4)
            self.assertIn('PBR set',stack.text());self.assertIn('2 items (5 assets)',widget.page_label.text())
            self.assertEqual(stack.data(ui.ROLE)['label'],'stone_1K_albedo')   # the base color image represents the stack
            widget.items.setCurrentItem(stack)
            self.assertEqual(len(widget.selected_rows()),4)
            self.assertIn('PBR set: 4 images',widget.info.text())
            # Dragging the stack carries every image, and the ids used for folder moves.
            captured=[]
            class FakeDrag:
                def __init__(self,source):pass
                def setMimeData(self,mime):captured.append(mime)
                def setPixmap(self,pix):pass
                def exec(self,*a):return None
            with patch.object(ui.dragdrop.QtGui,'QDrag',FakeDrag):widget.items.startDrag(QtCore.Qt.DropAction.CopyAction)
            payloads=ui.dragdrop.parse_items(captured[0])
            self.assertEqual(sorted(Path(p['path']).name for p in payloads),sorted(n for n in names if n!='sky.hdr'))
            self.assertEqual(len(ui.dragdrop.FolderTree.payload(captured[0])['assets']),4)
            # Tags and favorite apply to the whole stack.
            widget.tags.setText('stone rough');widget.tags_edited('stone rough');widget.save_metadata();widget.star.setChecked(True)
            rows=[r for r in widget.library.assets() if r['label'].startswith('stone')]
            self.assertEqual({r['tags'] for r in rows},{'stone rough'});self.assertEqual({r['favorite'] for r in rows},{1})
            self.assertTrue(widget.items.item(0).text().startswith('★') or widget.items.item(1).text().startswith('★'))
            # Off: every image is listed on its own, and the choice is remembered.
            widget.stack.setChecked(False)
            self.assertEqual(widget.items.count(),5);self.assertFalse(widget.settings['stack_pbr'])
            self.assertEqual(len(widget.library.assets(search='stone')),4)
            widget.close();widget.deleteLater()
            again=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            self.assertFalse(again.stack.isChecked());self.assertEqual(again.items.count(),5)
            again.close();again.deleteLater()

    def test_ctrl_middle_drag_resizes_icons_and_remembers_the_size(self):
        from hutil.PySide import QtCore,QtGui
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for rel in ('3DModel/a.obj','3DModel/b.obj'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('placeholder')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            items=widget.items;self.assertEqual(items.iconSize().width(),144)
            def event(kind,x,y,button=QtCore.Qt.MouseButton.MiddleButton,mods=QtCore.Qt.KeyboardModifier.ControlModifier):
                held=QtCore.Qt.MouseButton.NoButton if kind==QtCore.QEvent.Type.MouseButtonRelease else button
                return QtGui.QMouseEvent(kind,QtCore.QPointF(x,y),QtCore.QPointF(x,y),button,held,mods)
            T=QtCore.QEvent.Type
            items.mousePressEvent(event(T.MouseButtonPress,100,100))
            items.mouseMoveEvent(event(T.MouseMove,180,100));self.assertEqual(items.iconSize().width(),224)
            self.assertEqual(items.gridSize().width(),248)
            items.mouseMoveEvent(event(T.MouseMove,100,140));self.assertEqual(items.iconSize().width(),104)   # down = smaller
            items.mouseMoveEvent(event(T.MouseMove,-500,100));self.assertEqual(items.iconSize().width(),64)    # clamped
            items.mouseMoveEvent(event(T.MouseMove,900,100));self.assertEqual(items.iconSize().width(),512)
            items.mouseReleaseEvent(event(T.MouseButtonRelease,900,100))
            self.assertEqual(widget.settings['icon_size'],512);self.assertEqual(widget.icon_edge(),512)
            self.assertEqual(json.loads((base/'data'/'settings.json').read_text())['icon_size'],512)
            items.mouseMoveEvent(event(T.MouseMove,0,0))   # no drag in progress: ignored
            self.assertEqual(items.iconSize().width(),512)
            # Plain middle clicks and Ctrl-less drags do not resize.
            items.mousePressEvent(event(T.MouseButtonPress,100,100,mods=QtCore.Qt.KeyboardModifier.NoModifier))
            items.mouseMoveEvent(event(T.MouseMove,300,100,mods=QtCore.Qt.KeyboardModifier.NoModifier));self.assertEqual(items.iconSize().width(),512)
            widget.close();widget.deleteLater()
            again=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            self.assertEqual(again.items.iconSize().width(),512);again.close();again.deleteLater()

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
