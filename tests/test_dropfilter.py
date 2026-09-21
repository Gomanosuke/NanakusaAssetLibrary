from pathlib import Path
import sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
import hou
from hutil.PySide import QtCore,QtGui,QtWidgets
from nanakusa_asset_library import dragdrop as dd,houdini_ops as ops


def cursor(pane_under_cursor):
    # hython has no hou.ui; the filter only asks which pane is under the cursor.
    return patch.object(hou,'ui',SimpleNamespace(paneTabUnderCursor=lambda:pane_under_cursor,setStatusMessage=lambda *a,**k:None),create=True)


def pane(kind):
    result=MagicMock();result.type.return_value=kind;return result


class DropFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def event(self,kind,path='C:/tmp/asset.usd'):
        mime=dd.mime_data_many([{'path':path,'kind':'usd','label':'asset'}],['id'])
        cls=QtGui.QDropEvent if kind=='drop' else QtGui.QDragMoveEvent
        args=(QtCore.QPointF(5,5),) if kind=='drop' else (QtCore.QPoint(5,5),)
        event=cls(*args,QtCore.Qt.DropAction.CopyAction|QtCore.Qt.DropAction.MoveAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
        event.mime=mime;return event

    def run_filter(self,pane_under_cursor,kind='drop'):
        widget=QtWidgets.QWidget();event=self.event(kind)
        with patch.object(dd.QtWidgets.QApplication,'widgetAt',return_value=None),cursor(pane_under_cursor):
            handled=dd.LibraryDropFilter().eventFilter(widget,event)
        return handled,event

    def test_scene_view_and_other_surfaces_swallow_the_drop(self):
        # Returning False would let Houdini treat the dropped file URL as a file to open.
        for target in (pane(hou.paneTabType.SceneViewer),pane(hou.paneTabType.CompositorViewer),pane(hou.paneTabType.PythonPanel),None):
            for kind in ('move','drop'):
                handled,event=self.run_filter(target,kind)
                self.assertTrue(handled,(target,kind));self.assertFalse(event.isAccepted(),(target,kind))

    def test_native_parameter_fields_still_receive_the_drop(self):
        handled,event=self.run_filter(pane(hou.paneTabType.Parm))
        self.assertFalse(handled)

    def test_library_tree_is_not_intercepted(self):
        tree=dd.FolderTree(lambda *a:True);event=self.event('move')
        with patch.object(dd.QtWidgets.QApplication,'widgetAt',return_value=tree.viewport()),cursor(pane(hou.paneTabType.PythonPanel)):
            self.assertFalse(dd.LibraryDropFilter().eventFilter(tree.viewport(),event))

    def test_text_fields_still_receive_the_path(self):
        field=QtWidgets.QLineEdit();event=self.event('drop')
        with patch.object(dd.QtWidgets.QApplication,'widgetAt',return_value=field),cursor(pane(hou.paneTabType.SceneViewer)):
            self.assertTrue(dd.LibraryDropFilter().eventFilter(field,event))
        self.assertEqual(field.text(),'C:/tmp/asset.usd');self.assertTrue(event.isAccepted())

    def test_other_mime_types_are_untouched(self):
        mime=QtCore.QMimeData();mime.setText('x')
        event=QtGui.QDragMoveEvent(QtCore.QPoint(1,1),QtCore.Qt.DropAction.CopyAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
        self.assertFalse(dd.LibraryDropFilter().eventFilter(QtWidgets.QWidget(),event))


class CatalogRelinkTests(unittest.TestCase):
    def test_moved_usd_entries_follow_the_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:  # the data source may hold the file briefly
            base=Path(folder);catalog=base/'cat.db'
            old=base/'a'/'Chair.usd';new=base/'b'/'Chair.usd';other=base/'a'/'Table.usd'
            source=hou.AssetGalleryDataSource(catalog.as_posix());source.startTransaction()
            first=source.addItem('Chair',old.as_posix(),thumbnail=b'');second=source.addItem('Table',other.as_posix(),thumbnail=b'')
            source.endTransaction(True)
            self.assertEqual(ops.relink_catalog_paths([catalog,base/'missing.db'],[(old,new)],base/'backups'),1)
            reopened=hou.AssetGalleryDataSource(catalog.as_posix())
            self.assertEqual(Path(reopened.filePath(first)),new);self.assertEqual(Path(reopened.filePath(second)),other)
            self.assertEqual(len(list((base/'backups').glob('cat_backup_*.db'))),1)
            self.assertEqual(ops.relink_catalog_paths([catalog],[(old,new)],base/'backups'),0)
            del source,reopened

if __name__=='__main__':unittest.main()
