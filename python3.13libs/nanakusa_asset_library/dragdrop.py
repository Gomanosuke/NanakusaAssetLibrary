"""Drag payload and narrowly scoped Qt drop filter for this library only.

Other applications' drags and Houdini's normal node drags are untouched.
Plain text and file URLs are included for native parameter-field drops.
"""
import json
import os
from pathlib import Path
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import houdini_ops as ops
from . import pbr

MIME = 'application/x-nanakusa-asset'
IDS_MIME = 'application/x-nanakusa-asset-ids'    # in-library moves onto folders
FOLDER_MIME = 'application/x-nanakusa-folder'
STACK_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1
_filter = None

def mime_data(path, kind, label):
    return mime_data_many([{'path':path,'kind':kind,'label':label}])


def paths_text(paths):
    paths=[Path(p).resolve().as_posix() for p in paths]
    if len(paths)==1:return paths[0]
    return ' '.join('"'+p+'"' if any(c.isspace() for c in p) else p for p in paths)


def mime_data_many(items, ids=None):
    items=[dict(item,path=Path(item['path']).resolve().as_posix()) for item in items]
    mime = QtCore.QMimeData()
    if ids:mime.setData(IDS_MIME, json.dumps(list(ids)).encode('utf-8'))
    mime.setText(paths_text([item['path'] for item in items]))
    mime.setUrls([QtCore.QUrl.fromLocalFile(item['path']) for item in items])
    mime.setData(MIME, json.dumps({'schema': 2, 'items':items}).encode('utf-8'))
    return mime

def parse(mime):
    items=parse_items(mime)
    return items[0] if items else None


def parse_items(mime):
    if not mime.hasFormat(MIME):
        return None
    data = json.loads(bytes(mime.data(MIME)).decode('utf-8'))
    items=[data] if data.get('schema')==1 else data.get('items') if data.get('schema')==2 else None
    if not isinstance(items,list) or not items or any(not isinstance(x,dict) or x.get('kind') not in ('model','usd','texture') or not isinstance(x.get('path'),str) or not isinstance(x.get('label'),str) for x in items):
        raise ValueError('Unsupported NanakusaAssetLibrary drag payload')
    return items

def is_material_context(parent):
    ancestors=[]
    ancestor=parent.parent()
    while ancestor is not None:
        ancestors.append(ancestor);ancestor=ancestor.parent()
    return parent.childTypeCategory() == hou.vopNodeTypeCategory() and (
        parent.type().name() in ('materiallibrary','matnet','materialbuilder') or
        parent.path() == '/mat' or
        any(a.type().name() == 'materiallibrary' for a in ancestors) or
        any(n in parent.type().name().lower() for n in ('material','shader')) or
        (parent.type().name() == 'subnet' and parent.shaderLanguageName() == 'MaterialX'))

def can_import(parent, kind):
    category = parent.childTypeCategory()
    if kind == 'texture':
        return is_material_context(parent)
    return category in (hou.lopNodeTypeCategory(), hou.objNodeTypeCategory(), hou.sopNodeTypeCategory())

def import_payload(payload, parent, position=None):
    return import_payloads([payload],parent,position)[0]


def import_payloads(payloads, parent, position=None):
    if not payloads or any(not can_import(parent,p['kind']) for p in payloads):
        raise ValueError('This asset cannot be dropped in this network')
    for payload in payloads:
        if not Path(payload['path']).is_file():raise FileNotFoundError(payload['path'])
    groups=pbr.group_images([p['path'] for p in payloads]) if all(p['kind']=='texture' for p in payloads) else None
    before=set(parent.children())
    selected=tuple(hou.selectedNodes())
    display=parent.displayNode() if hasattr(parent,'displayNode') else None
    with hou.undos.group('NanakusaAssetLibrary drop'):
        try:
            nodes=[]
            if groups is not None:
                for i,group in enumerate(groups):nodes.append(ops.texture_material(parent,group['maps'],group['label'],position if i==0 else None))
            else:
                for p in payloads:nodes.append(ops.import_into_context(p['path'],p['kind'],p['label'],parent))
            origin=position if position is not None else hou.Vector2(0,0)
            for i,node in enumerate(nodes):
                # A surface inside an existing builder was already laid out with its UV and images.
                if node.type().name()!='mtlxstandard_surface':node.setPosition(origin+hou.Vector2((i%5)*3,-(i//5)*2))
                node.setSelected(True,clear_all_selected=(i==0))
            output=nodes[-1]
            if len(nodes)>1 and parent.childTypeCategory() in (hou.lopNodeTypeCategory(),hou.sopNodeTypeCategory()):
                output=parent.createNode('merge','asset_merge')
                for i,node in enumerate(nodes):output.setInput(i,node)
                output.setPosition(origin+hou.Vector2(3,-((len(nodes)-1)//5+1)*2))
                output.setSelected(True)
            if hasattr(output,'setDisplayFlag'):output.setDisplayFlag(True)
            if hasattr(output,'setRenderFlag'):output.setRenderFlag(True)
            return nodes
        except Exception:
            for node in set(parent.children())-before:node.destroy()
            if display is not None:display.setDisplayFlag(True)
            for node in selected:node.setSelected(True)
            raise


def native_overlay(pane, widget):
    """Houdini 22 draws P parameters in a separate native RE_Window.

    Its screen rectangle is inside the pane. The graph drawable instead spans
    the enclosing desktop/floating panel. Never infer a parameter from selection.
    """
    if not pane.isShowingParmDialog():return False
    bounds=pane.qtScreenGeometry()
    while isinstance(widget,QtWidgets.QWidget):
        if widget.objectName()=='RE_Window':
            rect=QtCore.QRect(widget.mapToGlobal(QtCore.QPoint(0,0)),widget.size())
            if bounds.contains(rect) and rect.height()<bounds.height():return True
        widget=widget.parentWidget()
    return False

class LibraryDropFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        types=(QtCore.QEvent.Type.DragEnter,QtCore.QEvent.Type.DragMove,QtCore.QEvent.Type.Drop)
        if event.type() not in types or not event.mimeData().hasFormat(MIME):
            return False
        try:
            payloads=parse_items(event.mimeData())
            path_text=paths_text([p['path'] for p in payloads])
            # Let Qt/Houdini receive the plain path in parameter text fields.
            hit=QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
            widget=hit or obj
            while widget is not None:
                if widget.property('nanakusaInternalDrop'):
                    return False  # Folder tree of the library: moves assets, never imports.
                if isinstance(widget,(QtWidgets.QLineEdit,QtWidgets.QTextEdit,QtWidgets.QPlainTextEdit)):
                    if event.type()==QtCore.QEvent.Type.Drop:
                        if isinstance(widget,QtWidgets.QLineEdit):
                            if widget.isReadOnly():return False
                            widget.setText(path_text)
                            widget.textEdited.emit(path_text)
                            widget.editingFinished.emit()
                        else:
                            if widget.isReadOnly():return False
                            widget.insertPlainText(path_text)
                        event.acceptProposedAction()
                        return True
                    event.acceptProposedAction()
                    return True
                widget=widget.parentWidget() if isinstance(widget,QtWidgets.QWidget) else None
            pane=hou.ui.paneTabUnderCursor()
            if pane is not None and pane.type()==hou.paneTabType.Parm:
                return False  # Native parameter fields apply their own drop rules.
            if pane is None or pane.type()!=hou.paneTabType.NetworkEditor:
                # Scene View, main window and every other surface: do nothing. Without
                # this Houdini treats the dropped file URL as a file to open.
                event.ignore()
                return True
            if native_overlay(pane,hit):return False
            if any(not can_import(pane.pwd(),p['kind']) for p in payloads):
                event.ignore()
                return True  # Texture drags must not become unexpected file/COP nodes.
            if event.type()==QtCore.QEvent.Type.Drop:
                nodes=import_payloads(payloads,pane.pwd(),pane.cursorPosition())
                hou.ui.setStatusMessage('NanakusaAssetLibrary: imported '+str(len(nodes))+' asset(s)')
            event.acceptProposedAction()
            return True
        except Exception as exc:
            event.ignore()
            hou.ui.setStatusMessage('NanakusaAssetLibrary: '+str(exc),severity=hou.severityType.Error)
            return True

def install():
    global _filter
    app=QtWidgets.QApplication.instance()
    if _filter is None:
        _filter=LibraryDropFilter(app)
        app.installEventFilter(_filter)
    return _filter

class AssetList(QtWidgets.QListWidget):
    MIN_ICON, MAX_ICON = 64, 512
    iconSizeFinished = QtCore.Signal(int)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library=library
        self._sizing=None
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

    def apply_icon_size(self, size):
        size=max(self.MIN_ICON,min(self.MAX_ICON,int(size)))
        self.setIconSize(QtCore.QSize(size,size))
        self.setGridSize(QtCore.QSize(size+24,size+48))
        return size

    # Ctrl + middle-button drag resizes the icons: right or up = larger, left or down = smaller.
    def mousePressEvent(self, event):
        if event.button()==QtCore.Qt.MouseButton.MiddleButton and event.modifiers()&QtCore.Qt.KeyboardModifier.ControlModifier:
            self._sizing=(event.position().toPoint(),self.iconSize().width())
            self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
            event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._sizing is not None:
            start,size=self._sizing;point=event.position().toPoint()
            self.apply_icon_size(size+(point.x()-start.x())-(point.y()-start.y()))
            event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._sizing is not None and event.button()==QtCore.Qt.MouseButton.MiddleButton:
            self._sizing=None;self.unsetCursor()
            self.iconSizeFinished.emit(self.iconSize().width())
            event.accept();return
        super().mouseReleaseEvent(event)

    def expand(self, item):
        """Rows an item stands for; the library widget expands a PBR stack into its images."""
        return [item.data(QtCore.Qt.ItemDataRole.UserRole)]

    def startDrag(self, actions):
        items=self.selectedItems()
        if not items:return
        try:
            rows={}
            for item in items:
                for row in self.expand(item):rows[row['id']]=row
            payloads=[{'path':self.library.resolve(row),'kind':row['effective_kind'],'label':row['label']} for row in rows.values()]
        except Exception as exc:
            hou.ui.setStatusMessage(str(exc),severity=hou.severityType.Error)
            return
        drag=QtGui.QDrag(self)
        drag.setMimeData(mime_data_many(payloads,list(rows)))
        drag.setPixmap(items[0].icon().pixmap(96,96))
        tree=getattr(self,'folder_tree',None)
        if tree is not None:tree.begin_tracking({'assets':list(rows)})
        result=None
        try:
            # Copy stays the default for Houdini; only the folder tree accepts Move.
            result=drag.exec(QtCore.Qt.DropAction.CopyAction|QtCore.Qt.DropAction.MoveAction,QtCore.Qt.DropAction.CopyAction)
        finally:
            if tree is not None:tree.end_tracking(result)


class FolderTree(QtWidgets.QTreeWidget):
    """Folder list that receives assets and folders dropped on it to reorganize them."""
    dropped = QtCore.Signal(object, object)   # payload, target (root id, relative folder, root path)
    dragStarted = QtCore.Signal()
    dragFinished = QtCore.Signal()
    ROLE = QtCore.Qt.ItemDataRole.UserRole

    def __init__(self, validate, parent=None):
        super().__init__(parent)
        self.validate = validate  # callable(payload, target) -> bool
        self.setProperty('nanakusaInternalDrop', True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragDrop)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDropIndicatorShown(False)
        self._hover = None
        self._tracking = None
        self._dropped = False
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._follow)
        self._expand = QtCore.QTimer(self)
        self._expand.setSingleShot(True)
        self._expand.setInterval(600)
        self._expand.timeout.connect(self._expand_hover)

    def mimeTypes(self):
        return [IDS_MIME, FOLDER_MIME]

    @staticmethod
    def payload(mime):
        if mime.hasFormat(IDS_MIME):
            return {'assets': json.loads(bytes(mime.data(IDS_MIME)).decode('utf-8'))}
        if mime.hasFormat(FOLDER_MIME):
            return {'folders': json.loads(bytes(mime.data(FOLDER_MIME)).decode('utf-8'))}
        return None

    def startDrag(self, actions):
        # Every selected folder travels; libraries and the fixed USD / Texture / 3DModel
        # categories stay put, and all folders must belong to the same library.
        chosen = [i.data(0, self.ROLE) for i in self.selectedItems()]
        chosen = [d for d in chosen if d and '/' in d[1]]
        if not chosen or len({d[0] for d in chosen}) > 1:
            return
        mime = QtCore.QMimeData()
        mime.setData(FOLDER_MIME, json.dumps([{'root_id': d[0], 'rel': d[1]} for d in chosen]).encode('utf-8'))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        self.begin_tracking(self.payload(mime))
        result = None
        try:
            result = drag.exec(QtCore.Qt.DropAction.MoveAction)
        finally:
            self.end_tracking(result)

    # A drag that starts inside the library must still land on the tree when Qt does not
    # deliver drag events to it (seen in docked panes until the cursor left and re-entered).
    # While such a drag runs the cursor is followed directly; if nobody accepted the drop
    # and the button was released over a folder, the move is performed from here.
    def begin_tracking(self, payload):
        self._tracking = payload
        self._dropped = False
        self._poll.start()
        self.dragStarted.emit()

    @staticmethod
    def left_button_down():
        # Qt keeps the pre-drag button state after an OS drag loop, so ask the OS on Windows.
        if os.name == 'nt':
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        return bool(QtWidgets.QApplication.mouseButtons() & QtCore.Qt.MouseButton.LeftButton)

    def _tracked_item(self):
        pos = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        return self.itemAt(pos) if self.viewport().rect().contains(pos) else None

    def _follow(self):
        item = self._tracked_item()
        data = item.data(0, self.ROLE) if item else None
        self._set_hover(item if item is not None and data and self.validate(self._tracking, data) else None)

    def end_tracking(self, result):
        self._poll.stop()
        payload, self._tracking = self._tracking, None
        dropped, self._dropped = self._dropped, False
        item = self._tracked_item()
        self._set_hover(None)
        self.dragFinished.emit()
        if dropped or payload is None or item is None or result != QtCore.Qt.DropAction.IgnoreAction:
            return
        if self.left_button_down():
            return  # Cancelled with Esc: the button is still down.
        target = item.data(0, self.ROLE)
        if target and target[1]:
            self.dropped.emit(payload, target)

    def _target(self, event):
        item = self.itemAt(event.position().toPoint())
        return item, (item.data(0, self.ROLE) if item else None)

    def _set_hover(self, item):
        if item is not self._hover:
            self._hover = item
            self.viewport().update()
            self._expand.stop()
            if item is not None and item.childCount() and not item.isExpanded():
                self._expand.start()

    def _expand_hover(self):
        if self._hover is not None:
            self._hover.setExpanded(True)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        payload = self.payload(event.mimeData())
        item, target = self._target(event)
        bar = self.verticalScrollBar()
        y = event.position().toPoint().y()
        if y < 24:
            bar.setValue(bar.value() - 8)
        elif y > self.viewport().height() - 24:
            bar.setValue(bar.value() + 8)
        if payload is not None and target and self.validate(payload, target):
            self._set_hover(item)
            event.setDropAction(QtCore.Qt.DropAction.MoveAction)
            event.accept()
        else:
            self._set_hover(None)
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_hover(None)
        event.accept()

    def dropEvent(self, event):
        payload = self.payload(event.mimeData())
        item, target = self._target(event)
        self._set_hover(None)
        if payload is None or not target or not self.validate(payload, target):
            event.ignore()
            return
        event.setDropAction(QtCore.Qt.DropAction.MoveAction)
        event.accept()
        self._dropped = True
        self.dropped.emit(payload, target)

    def drawRow(self, painter, option, index):
        super().drawRow(painter, option, index)
        if self._hover is not None and index == self.indexFromItem(self._hover):
            painter.fillRect(option.rect, QtGui.QColor(90, 150, 255, 90))
