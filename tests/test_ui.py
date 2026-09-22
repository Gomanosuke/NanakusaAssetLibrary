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
            model=next(i for i in range(widget.items.count()) if widget.item_row(widget.items.item(i))['kind']=='model')
            usd=next(i for i in range(widget.items.count()) if widget.item_row(widget.items.item(i))['kind']=='usd')
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
            self.assertTrue(widget.library.backup_index().parent.samefile(base/'data'/'backups'))
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
                item=widget.ensure_item(rid,rel);parent=item.parent()
                while parent is not None:parent.setExpanded(True);parent=parent.parent()
                return item
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
            stack=next(widget.items.item(i) for i in range(2) if len(widget.item_rows(widget.items.item(i)))==4)
            self.assertIn('PBR set',stack.text());self.assertIn('5 assets',widget.page_label.text());self.assertEqual(widget.items.count(),2)
            self.assertEqual(widget.item_row(stack)['label'],'stone_1K_albedo')   # the base color image represents the stack
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

    def test_usdz_preview_is_shown_cached_and_never_overrides_a_generated_thumbnail(self):
        import zipfile
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')   # 1x1 image
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            (root/'USD').mkdir(parents=True)
            for name,extra in (('with_preview',{'Thumbnails/thumbnail.png':png}),('plain',{})):
                with zipfile.ZipFile(root/'USD'/(name+'.usdz'),'w') as z:
                    z.writestr('a.usdc','x');[z.writestr(k,v) for k,v in extra.items()]
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            rows={r['label']:r for r in widget.rows}
            found=widget.thumbnail_path(rows['with_preview'])
            self.assertTrue(found.parent.samefile(base/'data'/'thumbnails'));self.assertEqual(found.read_bytes(),png)   # cached, nothing written next to the asset
            self.assertEqual(sorted(p.name for p in (root/'USD').iterdir()),['plain.usdz','with_preview.usdz'])
            self.assertIsNone(widget.thumbnail_path(rows['plain']))
            with patch.object(ui.embedded,'extract',wraps=ui.embedded.extract) as extract:
                widget.thumbnail_path(rows['plain']);widget.thumbnail_path(rows['with_preview'])
                self.assertEqual(extract.call_count,0)   # missing previews are remembered, existing ones come from the cache
            self.assertTrue(widget.items.item(0).icon().pixmap(32,32).width()>0)
            generated=root/'USD'/'with_preview_thumbnail.png';generated.write_bytes(png)
            self.assertTrue(widget.thumbnail_path(rows['with_preview']).samefile(generated))   # a generated thumbnail wins
            widget.close();widget.deleteLater()

    def test_big_libraries_stay_responsive(self):
        import time
        from hutil.PySide import QtGui,QtCore
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for i in range(60):
                d=root/'USD'/'Props'/f'pack{i:03d}';d.mkdir(parents=True);(d/f'pack{i:03d}.usd').write_text('x')
                image=QtGui.QImage(64,64,QtGui.QImage.Format.Format_RGB32);image.fill(0x336699);image.save(str(d/'thumbnail.png'))
            (root/'3DModel').mkdir()
            for i in range(30):(root/'3DModel'/f'm{i:02d}.obj').write_text('x')
            big=root/'Texture'/'huge.png';big.parent.mkdir(parents=True);big.write_bytes(bytes(3*1024*1024))
            with patch.object(ui.LibraryWidget,'next_info') as next_info:
                widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
                widget.resize(1400,900);widget.show();self.app.processEvents()
                widget.library.scan(widget.library.roots()[0]['id']);widget.rebuild_tree();widget.refresh()
                # Pictures arrive after refresh returns, in slices; items exist at once with flat placeholders.
                self.assertEqual(widget.items.count(),91)   # fewer than one chunk: all at once
                self.assertTrue(widget.icon_todo)
                start=time.monotonic()
                while (widget.icon_todo or widget.icon_timer.isActive()) and time.monotonic()-start<20:self.app.processEvents()
                self.assertFalse(widget.icon_todo)
                usd=next(i for i in range(widget.items.count()) if widget.item_row(widget.items.item(i))['kind']=='usd')
                widget.items.scrollToItem(widget.items.item(usd),QtWidgets.QAbstractItemView.ScrollHint.PositionAtTop)
                start=time.monotonic()
                while not(widget.items.verticalScrollBar().value()>0 and not widget.icon_todo and not widget.icon_timer.isActive() and not widget.scroll_timer.isActive()) and time.monotonic()-start<20:self.app.processEvents()
                self.assertNotEqual(widget.items.item(usd).icon().cacheKey(),widget.placeholder('usd').cacheKey())   # real thumbnail loaded
                # A 3 MB original image is never decoded on the UI thread.
                texture=next(r for r in widget.rows if r['kind']=='texture')
                self.assertIsNone(widget.thumbnail_path(texture))
                # Selecting quickly through assets starts no hython until the selection settles.
                for i in range(5):widget.items.setCurrentRow(i)
                self.assertTrue(widget.info_timer.isActive());next_info.assert_not_called()
                widget.info_timer.stop();widget.start_info();self.assertEqual(next_info.call_count,1)
                widget.close();widget.deleteLater()

    def test_folder_tree_comes_from_the_index_and_missing_thumbnails_are_checked_in_slices(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail') as queue:
            base=Path(folder);root=base/'asset'
            for i in range(40):
                p=root/'3DModel'/f'g{i%4}'/f'm{i:02d}.obj';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id'])
            with patch.object(ui.core,'visible_folders',side_effect=AssertionError('walked the disk')):
                widget.rebuild_tree()   # folders are read from the index
                widget.library.add_folder(widget.library.roots()[0]['id'],'3DModel/Fresh');widget.rebuild_tree()
            rid=widget.library.roots()[0]['id']
            self.assertIsNotNone(widget.ensure_item(rid,'3DModel/Fresh'));self.assertIsNotNone(widget.ensure_item(rid,'3DModel/g3'));self.assertIsNone(widget.ensure_item(rid,'3DModel/nope'))
            # An index made by an older version has no folder list: walk once and remember it.
            widget.library.set_folders(rid,[])
            widget.rebuild_tree();self.assertIn('3DModel/g0',widget.library.folders(rid))
            widget.refresh();widget.generate_missing_thumbnails()
            self.assertIsNotNone(widget.missing_scan)
            steps=0
            while widget.missing_scan is not None and steps<1000:self.app.processEvents();steps+=1
            self.assertIsNone(widget.missing_scan);self.assertEqual(len([c for c in queue.call_args_list if c.kwargs.get('geometry')]),40)
            self.assertIn('Queued 40',widget.status.text())
            widget.close();widget.deleteLater()

    def test_list_grows_as_it_is_scrolled_and_only_nearby_pictures_are_kept(self):
        import time
        from hutil.PySide import QtGui
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for i in range(450):
                d=root/'USD'/'Props'/f'pack{i:03d}';d.mkdir(parents=True);(d/f'pack{i:03d}.usd').write_text('x')
                image=QtGui.QImage(32,32,QtGui.QImage.Format.Format_RGB32);image.fill(0x336699);image.save(str(d/'thumbnail.png'))
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.CHUNK=100;widget.resize(1000,600);widget.show();self.app.processEvents()
            widget.library.scan(widget.library.roots()[0]['id']);widget.rebuild_tree();widget.reset_page()
            def settle():
                start=time.monotonic()
                while (widget.icon_todo or widget.icon_timer.isActive() or widget.scroll_timer.isActive()) and time.monotonic()-start<20:self.app.processEvents()
            settle()
            self.assertEqual(widget.items.count(),100);self.assertIn('450 assets',widget.page_label.text());self.assertIn('scroll for more',widget.page_label.text())
            bar=widget.items.verticalScrollBar();self.assertGreater(bar.maximum(),0)
            for expected in (200,300,400,450):
                bar.setValue(bar.maximum());settle()
                self.assertEqual(widget.items.count(),expected)
            self.assertNotIn('scroll for more',widget.page_label.text())
            bar.setValue(bar.maximum());settle();self.assertEqual(widget.items.count(),450)   # nothing left to add
            # Memory stays flat: only the pictures near the visible rows are loaded.
            first,last=widget.visible_range();self.assertGreater(first,300)
            self.assertLessEqual(len(widget.icons_loaded),7*max(widget.capacity(),last-first+1))
            self.assertTrue(all(i>=first-3*max(widget.capacity(),last-first+1) for i in widget.icons_loaded))
            self.assertEqual(widget.items.item(0).icon().cacheKey(),widget.placeholder('usd').cacheKey())   # scrolled far away: dropped
            bar.setValue(0);settle()
            self.assertNotEqual(widget.items.item(0).icon().cacheKey(),widget.placeholder('usd').cacheKey())   # back in view: loaded again
            # Refreshing (a tag edit, a move) keeps what was loaded and the scroll position; a new search starts at the top.
            bar.setValue(bar.maximum());settle();position=bar.value();widget.refresh();settle();self.app.processEvents()
            self.assertEqual(widget.items.count(),450);self.assertAlmostEqual(bar.value(),position,delta=5)
            widget.search.setText('pack00');widget.search_timer.stop();widget.reset_page();settle()
            self.assertEqual(widget.items.count(),10);self.assertEqual(bar.value(),0)
            widget.close();widget.deleteLater()

    def test_scan_runs_in_a_separate_python_and_can_be_cancelled(self):
        from nanakusa_asset_library.core import Library
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            base=Path(folder);root=base/'asset'
            for i in range(30):
                p=root/'3DModel'/f'm{i:02d}.obj';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('x')
            library=Library(base/'data');rid=library.add_root(root)
            self.assertIsNotNone(ui.ScanJob.find_python(ui.hou.getenv('HFS')))   # Houdini ships its own Python
            got=[]
            job=ui.ScanJob(library,library.roots());job.done.connect(got.append);job.run()
            self.assertEqual(got[0][0][1]['count'],30);self.assertEqual(library.count(),30);self.assertTrue(library.folders(rid))
            # No separate interpreter: the same scan runs in the calling thread.
            (root/'3DModel'/'extra.obj').write_text('x');got.clear()
            job=ui.ScanJob(library,library.roots());job.python=None;job.done.connect(got.append);job.run()
            self.assertEqual(got[0][0][1]['count'],31)
            # A cancelled scan leaves the index as it was.
            (root/'3DModel'/'more.obj').write_text('x')
            job=ui.ScanJob(library,library.roots())
            with patch.object(ui.ScanJob,'isInterruptionRequested',return_value=True):
                self.assertTrue(job.scan_root(library.roots()[0])['cancelled'])
            self.assertEqual(library.count(),31)
            # A failing worker is reported, not swallowed.
            with patch.object(ui.ScanJob,'scan_root',side_effect=RuntimeError('boom')):
                got.clear();job=ui.ScanJob(library,library.roots());job.done.connect(got.append);job.run()
            self.assertIn('boom',got[0][0][1]['errors'][0])

    def test_folder_items_are_created_only_when_a_folder_is_opened(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for i in range(300):
                p=root/'3DModel'/f'g{i%3}'/f'sub{i:03d}'/f'm{i:03d}.obj';p.parent.mkdir(parents=True);p.write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            rid=widget.library.roots()[0]['id'];widget.library.scan(rid);widget.rebuild_tree()
            self.assertLess(len(widget.folder_items),10)   # 300+ folders exist, only the genres are items
            genre=widget.folder_items[(rid,'3DModel')];self.assertEqual(genre.childCount(),1)   # a placeholder that makes it expandable
            genre.setExpanded(True);self.assertEqual([genre.child(i).text(0) for i in range(genre.childCount())],['g0','g1','g2'])
            self.assertEqual(widget.folder_items[(rid,'3DModel/g0')].childCount(),1)
            # Opening state and selection survive a rebuild; a selected folder deep down is created on demand.
            widget.select_folder((rid,'3DModel/g1/sub004',str(root)));self.assertEqual(widget.current_folder()[1],'3DModel/g1/sub004')
            widget.rebuild_tree();self.assertEqual(widget.current_folder()[1],'3DModel/g1/sub004')
            self.assertTrue(widget.folder_items[(rid,'3DModel/g1')].isExpanded());self.assertFalse(widget.folder_items[(rid,'3DModel/g0')].isExpanded())
            widget.close();widget.deleteLater()

    def test_statistics_read_in_an_earlier_session_need_no_new_hython(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'queue_thumbnail'),patch.object(ui.LibraryWidget,'next_info') as next_info:
            base=Path(folder);root=base/'asset';p=root/'3DModel'/'a.obj';p.parent.mkdir(parents=True);p.write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            row=widget.library.assets()[0];stat=p.stat()
            widget.library.save_info(row['id'],f'{stat.st_mtime_ns}:{stat.st_size}',{'info':{'Polygons':'12'}})
            widget.items.setCurrentRow(0)
            self.assertIn('Polygons: 12',widget.stats.text());self.assertFalse(widget.info_timer.isActive());next_info.assert_not_called()
            # A result computed now is stored for the next session (errors are not).
            key=(str(p),stat.st_mtime_ns,stat.st_size);widget.info_ids[key]=(row['id'],'x:y')
            widget.info_done(key,{'error':'boom'});self.assertIsNone(widget.library.info(row['id'],'x:y'))
            widget.info_done(key,{'info':{'Polygons':'7'}});self.assertEqual(widget.library.info(row['id'],'x:y'),{'info':{'Polygons':'7'}})
            widget.close();widget.deleteLater()

    def test_helper_processes_run_at_low_priority(self):
        import os,subprocess
        options=ui.background()
        if os.name=='nt':self.assertTrue(options['creationflags']&subprocess.BELOW_NORMAL_PRIORITY_CLASS and options['creationflags']&subprocess.CREATE_NO_WINDOW)
        else:self.assertIn('preexec_fn',options)

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
