from pathlib import Path
import json,sys,tempfile,time,unittest
from unittest.mock import patch,MagicMock
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

    def test_folder_tree_migrates_older_indexes_in_the_background(self):
        # An index made before the scan recorded folders has none yet; rebuild_tree must not walk
        # the disk itself (that froze the panel on a real library) - a background job fills it in.
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            p=root/'3DModel'/'Sub'/'table.obj';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('placeholder')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            rid=widget.library.roots()[0]['id'];widget.library.scan(rid)
            widget.library.set_folders(rid,[])   # simulate an older index: scanned, but no folders recorded
            started=time.monotonic();widget.rebuild_tree();elapsed=time.monotonic()-started
            self.assertLess(elapsed,0.5,'rebuild_tree walked the disk inline instead of backgrounding it')
            self.assertIn(rid,widget.folder_migrations)
            job=next(j for j in ui._jobs if isinstance(j,ui.FolderMigrationJob) and j.root['id']==rid)
            self.assertTrue(job.wait(5000),'background folder migration did not finish')
            for _ in range(20):
                self.app.processEvents()
                if rid not in widget.folder_migrations:break
                time.sleep(0.05)
            self.assertNotIn(rid,widget.folder_migrations)
            self.assertIn('3DModel/Sub',widget.library.folders(rid))
            self.assertIn('3DModel/Sub',widget.folder_kids[rid].get('3DModel',[]))
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
            widget.rebuild_tree()   # scan() already recorded the folders; no need to wait for the background walk
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

    def test_ctrl_wheel_resizes_icons_and_remembers_the_size_once_scrolling_settles(self):
        from hutil.PySide import QtCore,QtGui
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for rel in ('3DModel/a.obj','3DModel/b.obj'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('placeholder')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            items=widget.items;self.assertEqual(items.iconSize().width(),144)
            def wheel(delta_y,mods=QtCore.Qt.KeyboardModifier.ControlModifier):
                pos=QtCore.QPointF(100,100)
                return QtGui.QWheelEvent(pos,pos,QtCore.QPoint(0,0),QtCore.QPoint(0,delta_y),
                    QtCore.Qt.MouseButton.NoButton,mods,QtCore.Qt.ScrollPhase.NoScrollPhase,False)
            items.wheelEvent(wheel(120));self.assertEqual(items.iconSize().width(),168)   # up = larger, one notch
            self.assertNotIn('icon_size',widget.settings)   # not saved yet: debounced
            items.wheelEvent(wheel(-120));self.assertEqual(items.iconSize().width(),144)   # down = smaller
            items.wheelEvent(wheel(2400));self.assertEqual(items.iconSize().width(),512)   # 20 notches: clamped
            self.assertEqual(items._wheel_timer.remainingTime()>0,True)   # still pending
            items._wheel_timer.timeout.emit()   # fire the debounce directly instead of sleeping in a test
            self.assertEqual(widget.settings['icon_size'],512);self.assertEqual(widget.icon_edge(),512)
            # Plain wheel scrolling (no Ctrl) scrolls the list instead of resizing.
            items.wheelEvent(wheel(120,mods=QtCore.Qt.KeyboardModifier.NoModifier))
            self.assertEqual(items.iconSize().width(),512)
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

    def test_generate_proxy_is_offered_for_usd_kinds_and_queues_a_background_job(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            (root/'USD'/'chair').mkdir(parents=True);(root/'USD'/'chair'/'chair.usda').write_text('x')
            import zipfile
            with zipfile.ZipFile(root/'USD'/'lamp.usdz','w') as z:z.writestr('a.usdc','x')
            (root/'3DModel').mkdir();(root/'3DModel'/'table.obj').write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            rows={r['label']:r for r in widget.rows}
            usda,usdz,obj=rows['chair'],rows['lamp'],rows['table']

            def select(row):
                widget.items.setCurrentRow(next(i for i in range(widget.items.count()) if widget.item_row(widget.items.item(i))['id']==row['id']))

            select(usda)
            labels=[a.text() for a in widget.build_asset_menu().actions()]
            self.assertIn('Generate Selected Proxies...',labels);self.assertIn('Generate Selected Element Switch...',labels)
            select(usdz)
            self.assertIn('Generate Selected Proxies...',[a.text() for a in widget.build_asset_menu().actions()])   # .usdz is repackaged with its proxy inside
            select(obj)
            self.assertNotIn('Generate Selected Proxies...',[a.text() for a in widget.build_asset_menu().actions()])   # not a USD kind

            widget.queue_proxy(obj,300);self.assertNotIn(obj['id'],widget.proxy_pending)   # not a USD kind: never queued
            widget.queue_element(obj);self.assertNotIn(obj['id'],widget.element_pending)

            with patch.object(ui,'ProxyJob') as job_cls:
                job=job_cls.return_value
                widget.queue_proxy(usda,300)
                self.assertIn(usda['id'],widget.proxy_pending)
                self.app.processEvents()
                job_cls.assert_called_once_with(usda['id'],Path(usda['root_path'])/usda['relpath'],base/'data'/'backups'/'proxy',300)
                self.assertIs(widget.proxy_job,job)
                job.done.connect.assert_called_once_with(widget.proxy_done)

            widget.proxy_done(usda['id'],'Proxy added (1 mesh(es))','')
            self.assertNotIn(usda['id'],widget.proxy_pending)
            self.assertIn('Proxy added',widget.status.text())

            widget.proxy_done('missing-id','','disk full')
            self.assertIn('missing-id',widget.proxy_failed)
            self.assertIn('Proxy generation failed',widget.status.text())

            with patch.object(ui,'ElementJob') as job_cls:
                job=job_cls.return_value
                widget.queue_element(usda)
                self.assertIn(usda['id'],widget.element_pending)
                self.app.processEvents()
                job_cls.assert_called_once_with(usda['id'],Path(usda['root_path'])/usda['relpath'],base/'data'/'backups'/'element')
                self.assertIs(widget.element_job,job)
                job.done.connect.assert_called_once_with(widget.element_done)

            widget.element_done(usda['id'],'Element switch added (3 elements)','')
            self.assertNotIn(usda['id'],widget.element_pending)
            self.assertIn('Element switch added',widget.status.text())

            widget.element_done('missing-id','','disk full')
            self.assertIn('missing-id',widget.element_failed)
            self.assertIn('Element switch generation failed',widget.status.text())

            select(usda)
            action_labels=[a.text() for a in widget.build_asset_menu().actions()]
            self.assertIn('Delete Element Switch',action_labels)

            with patch.object(ui,'ElementDeleteJob') as job_cls:
                job=job_cls.return_value
                widget.queue_element_delete(usda)
                self.assertIn(usda['id'],widget.element_delete_pending)
                self.app.processEvents()
                job_cls.assert_called_once_with(usda['id'],Path(usda['root_path'])/usda['relpath'],base/'data'/'backups'/'element')
                self.assertIs(widget.element_delete_job,job)
                job.done.connect.assert_called_once_with(widget.element_delete_done)

            widget.element_delete_done('missing-id','','disk full')
            self.assertIn('missing-id',widget.element_delete_failed)
            self.assertIn('Element switch removal failed',widget.status.text())
            widget.close();widget.deleteLater()

    def test_element_switch_generation_and_removal_drive_the_variant_tag_and_icon_badge(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            (root/'USD'/'pack').mkdir(parents=True);(root/'USD'/'pack'/'pack.usda').write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            row=next(r for r in widget.rows if r['label']=='pack')
            aid=row['id'];index=widget.item_index[aid]
            widget.icons_loaded.add(index)   # pretend the icon was already painted once, without a badge

            self.assertNotIn('variant',widget.row_index[aid]['tags'].split())
            widget.element_done(aid,'Element switch added (3 elements)','')
            self.assertIn('variant',widget.row_index[aid]['tags'].split())
            self.assertIn('variant',widget.library.assets_by_ids([aid])[0]['tags'].split())   # persisted, not just in memory
            self.assertNotIn(index,widget.icons_loaded)   # forced to repaint so the badge appears
            self.assertIn(index,widget.icon_todo)

            widget.icons_loaded.add(index);widget.icon_todo.clear()
            widget.element_delete_done(aid,'Element switch removed (1 prim(s))','')
            self.assertNotIn('variant',widget.row_index[aid]['tags'].split())
            self.assertNotIn('variant',widget.library.assets_by_ids([aid])[0]['tags'].split())
            self.assertNotIn(index,widget.icons_loaded)
            self.assertIn(index,widget.icon_todo)

            # a skip (no error, but no "added"/"removed" prefix) must not touch the tag
            widget.set_variant_tag(aid,True);self.assertIn('variant',widget.row_index[aid]['tags'].split())
            widget.element_done(aid,'Already has an element switch','')
            self.assertIn('variant',widget.row_index[aid]['tags'].split())   # unchanged by the skip message

            entry=next(e for e in widget.page_entries if e['rep']['id']==aid)
            with patch.object(ui.LibraryWidget,'variant_badge',return_value=ui.QtGui.QIcon()) as badge:
                entry['rep']['tags']=''
                widget.icons_loaded.discard(index);widget.icon_todo.append(index)
                widget.load_icons()
                badge.assert_not_called()
                entry['rep']['tags']='variant'
                widget.icons_loaded.discard(index);widget.icon_todo.append(index)
                widget.load_icons()
                badge.assert_called_once()
            widget.close();widget.deleteLater()

    def test_proxy_dialog_remembers_the_last_value_and_cancel_queues_nothing(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            (root/'USD'/'chair').mkdir(parents=True);(root/'USD'/'chair'/'chair.usda').write_text('x')
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.library.scan(widget.library.roots()[0]['id']);widget.refresh()
            widget.items.setCurrentRow(0);widget.items.selectAll()

            with patch.object(ui.QtWidgets.QInputDialog,'getInt',return_value=(150,False)) as dlg:
                widget.generate_proxy()
                dlg.assert_called_once()
                self.assertEqual(dlg.call_args.args[3],ui.proxy_gen.TARGET_TRIANGLES)   # default shown
            self.assertEqual(widget.proxy_queue,ui.deque())   # cancelled: nothing queued
            self.assertNotIn('proxy_target_triangles',widget.settings)

            with patch.object(ui.QtWidgets.QInputDialog,'getInt',return_value=(150,True)):
                widget.generate_proxy()
            self.assertEqual(widget.settings['proxy_target_triangles'],150)
            with patch.object(ui.QtWidgets.QInputDialog,'getInt') as dlg2:
                dlg2.return_value=(150,False)
                widget.ask_proxy_target()
                self.assertEqual(dlg2.call_args.args[3],150)   # remembers the last value as the new default
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
                while widget.icons_pending() and time.monotonic()-start<20:self.app.processEvents()
                self.assertFalse(widget.icon_todo)
                usd=next(i for i in range(widget.items.count()) if widget.item_row(widget.items.item(i))['kind']=='usd')
                widget.items.scrollToItem(widget.items.item(usd),QtWidgets.QAbstractItemView.ScrollHint.PositionAtTop)
                start=time.monotonic()
                while not(widget.items.verticalScrollBar().value()>0 and not widget.icons_pending() and not widget.scroll_timer.isActive()) and time.monotonic()-start<20:self.app.processEvents()
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
            rid0=widget.library.roots()[0]['id']
            if rid0 in widget.folder_migrations:
                # __init__ started its own background migration (the index was empty at that
                # point); let it finish before patching visible_folders, or it can race into the
                # patch and raise inside the background thread instead of just walking the disk.
                init_job=next(j for j in ui._jobs if isinstance(j,ui.FolderMigrationJob) and j.root['id']==rid0)
                init_job.wait(5000)
                steps=0
                while rid0 in widget.folder_migrations and steps<200:self.app.processEvents();steps+=1
            widget.library.scan(widget.library.roots()[0]['id'])
            with patch.object(ui.core,'visible_folders',side_effect=AssertionError('walked the disk')):
                widget.rebuild_tree()   # folders are read from the index
                widget.library.add_folder(widget.library.roots()[0]['id'],'3DModel/Fresh');widget.rebuild_tree()
            rid=widget.library.roots()[0]['id']
            self.assertIsNotNone(widget.ensure_item(rid,'3DModel/Fresh'));self.assertIsNotNone(widget.ensure_item(rid,'3DModel/g3'));self.assertIsNone(widget.ensure_item(rid,'3DModel/nope'))
            # An index made by an older version has no folder list: a background walk fills it in once,
            # without blocking rebuild_tree itself (see FolderMigrationJob).
            widget.library.set_folders(rid,[])
            widget.rebuild_tree()
            job=next(j for j in ui._jobs if isinstance(j,ui.FolderMigrationJob) and j.root['id']==rid)
            self.assertTrue(job.wait(5000))
            steps=0
            while rid in widget.folder_migrations and steps<200:self.app.processEvents();steps+=1
            self.assertIn('3DModel/g0',widget.library.folders(rid))
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
                while (widget.icons_pending() or widget.scroll_timer.isActive()) and time.monotonic()-start<20:self.app.processEvents()
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

    def test_proxy_job_swaps_the_file_in_next_to_the_source_not_the_system_temp_drive(self):
        # os.replace() refuses to move a file across drives on Windows; writing the generated
        # proxy into the system TEMP directory (a different drive from most real libraries) and
        # then trying to swap it into place used to fail there every time, silently leaving a
        # matching, useless backup behind and the source untouched. Regression for that bug.
        from pxr import Usd, UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);source=base/'asset.usda';backups=base/'backups'
            stage=Usd.Stage.CreateNew(str(source));xform=UsdGeom.Xform.Define(stage,'/Asset')
            mesh=UsdGeom.Mesh.Define(stage,'/Asset/geo')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)]);mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3])
            stage.SetDefaultPrim(xform.GetPrim());stage.GetRootLayer().Save()

            got=[]
            job=ui.ProxyJob('aid',source,backups,ui.proxy_gen.TARGET_TRIANGLES)
            job.done.connect(lambda *a:got.append(a))
            job.run()
            self.assertEqual(got,[('aid','Proxy added (1 mesh(es))','')])
            leftovers=[p.name for p in source.parent.iterdir() if 'nanakusa_generate_tmp' in p.name]
            self.assertEqual(leftovers,[])   # the temp file next to the source is always cleaned up
            backed_up=list(backups.iterdir())
            self.assertEqual(len(backed_up),1)
            stage.Reload()   # the file changed on disk in a separate hython process; this process's cached layer has not
            self.assertEqual(UsdGeom.Imageable(stage.GetPrimAtPath('/Asset/geo')).ComputePurpose(),'render')
            self.assertEqual(UsdGeom.Imageable(stage.GetPrimAtPath('/Asset/geo_proxy')).ComputePurpose(),'proxy')

    def test_element_job_runs_as_a_real_subprocess_without_import_errors(self):
        # element_gen.py is invoked by hython as a script (see _MeshGenerateJob.run()), not
        # imported as part of the package; a `from . import proxy_gen` there would fail the same
        # way lod_gen.py's did (see the 0.12.3 regression test above). Run the real subprocess.
        from pxr import Usd, UsdGeom
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);source=base/'pack.usda';backups=base/'backups'
            stage=Usd.Stage.CreateNew(str(source));root=UsdGeom.Xform.Define(stage,'/Root')
            for name in ('a','b','c'):
                group=UsdGeom.Xform.Define(stage,f'/Root/{name}')
                mesh=UsdGeom.Mesh.Define(stage,f'/Root/{name}/geo')
                mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2])
            stage.SetDefaultPrim(root.GetPrim());stage.GetRootLayer().Save()

            got=[]
            job=ui.ElementJob('aid',source,backups)
            job.done.connect(lambda *a:got.append(a))
            job.run()
            self.assertEqual(got,[('aid','Element switch added (3 elements)','')])
            stage.Reload()
            self.assertEqual(stage.GetPrimAtPath('/Root').GetVariantSet('element').GetVariantSelection(),'Element0')

            got.clear()
            job=ui.ElementDeleteJob('aid',source,backups)
            job.done.connect(lambda *a:got.append(a))
            job.run()
            self.assertEqual(got,[('aid','Element switch removed (1 prim(s))','')])
            stage.Reload()
            self.assertFalse(stage.GetPrimAtPath('/Root').GetVariantSets().HasVariantSet('element'))

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
        if os.name=='nt':
            # Windows' background process mode, not just a below-normal CPU class: it also lowers
            # disk I/O priority, which CPU class alone does not (a long proxy queue was observed
            # competing with the panel's own reads and freezing it for several seconds).
            self.assertTrue(options['creationflags']&ui.PROCESS_MODE_BACKGROUND_BEGIN and options['creationflags']&subprocess.CREATE_NO_WINDOW)
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

    def test_open_catalog_shows_manual_instructions_instead_of_the_call_that_freezes_houdini(self):
        # hou.ui.setSharedLayoutDataSource(hou.AssetGalleryDataSource(...)) is the call SideFX's
        # own Asset Gallery menu uses (AssetGallerySourceMenu.xml) - it is the *correct* API,
        # unlike setSharedAssetGalleryDataSource (a different, unrelated call this method used to
        # make by mistake, which just raised a TypeError). But live-testing in a disposable
        # Houdini 22.0.447 instance showed setSharedLayoutDataSource itself, called alone with no
        # pane ever created, reliably freezes every native Houdini menu/popup in the session
        # (confirmed repeatedly; this panel's own Qt menus were unaffected). Creating the
        # 'asset_gallery' Python Panel interface's pane, floating or docked, does not avoid it
        # either. This is a Houdini engine bug, not fixable here, so open_catalog() no longer
        # calls any of it - only checks this. hou.ui does not exist in batch hython, so it is
        # mocked (create=True) to prove open_catalog() never touches it.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            base=Path(folder);root=base/'asset';root.mkdir()
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.catalog_path().parent.mkdir(parents=True,exist_ok=True)
            widget.catalog_path().write_bytes(b'')
            fake_ui=MagicMock()
            with patch.object(ui.hou,'ui',fake_ui,create=True):
                widget.open_catalog()
            fake_ui.setSharedLayoutDataSource.assert_not_called()
            fake_ui.setSharedAssetGalleryDataSource.assert_not_called()
            fake_ui.curDesktop.assert_not_called()
            fake_ui.displayMessage.assert_called_once()
            message=fake_ui.displayMessage.call_args.args[0]
            self.assertIn(str(widget.catalog_path()),message)
            self.assertIn('Asset Catalog',message)
            widget.close();widget.deleteLater()

    def test_asset_info_tries_plain_python_first_and_falls_back_to_hython(self):
        # usd/texture: plain Python first (~0.2 s instead of hython's ~1.7 s start); a 'retry'
        # answer (composition errors without Houdini's plugins, a failed import) goes to hython.
        def run(kind,answers):
            job=ui.AssetInfoJob('k','C:/x.usdz',kind);job.python=None if kind=='model' else Path('python.exe')
            got=[];job.done.connect(lambda k,r:got.append(r))
            with patch.object(job,'call',side_effect=answers) as call:job.run()
            return got[0],[c.args[0] for c in call.call_args_list]
        result,commands=run('usd',[{'info':{'Meshes':1}}])
        self.assertEqual(result,{'info':{'Meshes':1}});self.assertEqual(len(commands),1);self.assertEqual(commands[0][-1],'plain')
        result,commands=run('usd',[{'retry':'composition errors'},{'info':{'Meshes':2}}])
        self.assertEqual(result,{'info':{'Meshes':2}});self.assertEqual(len(commands),2);self.assertIn('hython',Path(commands[1][0]).name)
        result,commands=run('texture',[RuntimeError('DLL load failed'),{'info':{'Channels':3}}])
        self.assertEqual(result,{'info':{'Channels':3}});self.assertEqual(len(commands),2)
        result,commands=run('model',[{'info':{'Meshes':3}}])   # 3DModel imports need hou: hython only
        self.assertEqual(len(commands),1);self.assertNotEqual(commands[0][-1],'plain')
        from nanakusa_asset_library import asset_info
        with self.assertRaises(asset_info.NeedsHython):asset_info.inspect('x.obj','model',plain=True)
        env=ui.plain_python_env('C:/HFS')
        self.assertNotIn('PXR_PLUGINPATH_NAME',env);self.assertTrue(env['PATH'].startswith(str(Path('C:/HFS')/'bin')))

    def test_background_jobs_bar_counts_queues_and_cancel_all_empties_them(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'):
            base=Path(folder);root=base/'asset';root.mkdir()
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.update_jobs_bar();self.assertTrue(widget.jobs_bar.isHidden())
            labels=lambda:{a.text():a.isEnabled() for a in widget.build_root_menu().actions() if a.text()}
            self.assertFalse(labels()['Cancel All']);self.assertFalse(labels()['Cancel Proxies'])
            widget.thumb_queue.extend([{'id':'a'},{'id':'b'}]);widget.thumb_pending.update({'a','b'})
            widget.proxy_queue.append(({'id':'c'},300));widget.proxy_pending.add('c')
            widget.update_jobs_bar()
            self.assertFalse(widget.jobs_bar.isHidden())
            self.assertIn('Thumbnails 2',widget.jobs_label.text());self.assertIn('Proxies 1',widget.jobs_label.text())
            self.assertTrue(labels()['Cancel Thumbnails (2)']);self.assertTrue(labels()['Cancel All'])
            self.assertFalse(labels()['Cancel Element Switches'])
            widget.cancel_all_jobs()
            self.assertFalse(widget.thumb_queue);self.assertFalse(widget.proxy_queue)
            self.assertFalse(widget.thumb_pending or widget.proxy_pending)   # can be queued again later
            self.assertTrue(widget.jobs_bar.isHidden())
            # Ctrl+F (a key press on the panel, not an application-wide shortcut) jumps to the search field.
            from hutil.PySide import QtGui,QtCore
            widget.search.setText('rock')
            with patch.object(widget.search,'setFocus') as focus:
                widget.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress,QtCore.Qt.Key.Key_F,QtCore.Qt.KeyboardModifier.ControlModifier))
                focus.assert_called_once()
            self.assertEqual(widget.search.selectedText(),'rock')
            widget.close();widget.deleteLater()

    def test_icons_are_decoded_off_the_ui_thread_and_cached(self):
        from hutil.PySide import QtGui
        with tempfile.TemporaryDirectory() as folder,patch.object(ui.LibraryWidget,'request_info'),patch.object(ui.LibraryWidget,'queue_thumbnail'):
            base=Path(folder);root=base/'asset'
            for i in range(6):
                d=root/'USD'/f'p{i}';d.mkdir(parents=True);(d/f'p{i}.usd').write_text('x')
                image=QtGui.QImage(40,20,QtGui.QImage.Format.Format_RGB32);image.fill(0x336699);image.save(str(d/'thumbnail.png'))
            widget=ui.LibraryWidget(data_dir=base/'data',initial_root=str(root))
            widget.resize(900,600);widget.show();self.app.processEvents()
            widget.library.scan(widget.library.roots()[0]['id']);widget.rebuild_tree()
            with patch.object(ui,'square_preview',side_effect=AssertionError('decoded on the UI thread')):
                widget.reset_page()
                start=time.monotonic()
                while widget.icons_pending() and time.monotonic()-start<20:self.app.processEvents()
            self.assertFalse(widget.icons_pending())
            for i in range(widget.items.count()):
                self.assertNotEqual(widget.items.item(i).icon().cacheKey(),widget.placeholder('usd').cacheKey())
            self.assertEqual(len(widget.icon_cache),6)
            widget.close();widget.deleteLater()

if __name__=='__main__':unittest.main()
