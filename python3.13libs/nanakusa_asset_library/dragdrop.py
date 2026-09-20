"""Drag payload and narrowly scoped Qt drop filter for this library only.

Other applications' drags and Houdini's normal node drags are untouched.
Plain text and file URLs are included for native parameter-field drops.
"""
import json
from pathlib import Path
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import houdini_ops as ops

MIME = 'application/x-nanakusa-asset'
_filter = None

def mime_data(path, kind, label):
    mime = QtCore.QMimeData()
    path = Path(path).resolve().as_posix()
    mime.setText(path)
    mime.setUrls([QtCore.QUrl.fromLocalFile(path)])
    mime.setData(MIME, json.dumps({'schema': 1, 'path': path, 'kind': kind, 'label': label}).encode('utf-8'))
    return mime

def parse(mime):
    if not mime.hasFormat(MIME):
        return None
    data = json.loads(bytes(mime.data(MIME)).decode('utf-8'))
    if data.get('schema') != 1 or data.get('kind') not in ('model','usd','texture'):
        raise ValueError('Unsupported NanakusaAssetLibrary drag payload')
    return data

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
    if not can_import(parent, payload['kind']):
        raise ValueError('This asset cannot be dropped in this network')
    with hou.undos.group('NanakusaAssetLibrary drop'):
        node = ops.import_into_context(payload['path'],payload['kind'],payload['label'],parent)
        if position is not None:
            node.setPosition(position)
        node.setSelected(True,clear_all_selected=True)
        if hasattr(node,'setDisplayFlag'):
            node.setDisplayFlag(True)
        if hasattr(node,'setRenderFlag'):
            node.setRenderFlag(True)
        return node

class LibraryDropFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        types=(QtCore.QEvent.Type.DragEnter,QtCore.QEvent.Type.DragMove,QtCore.QEvent.Type.Drop)
        if event.type() not in types or not event.mimeData().hasFormat(MIME):
            return False
        try:
            payload=parse(event.mimeData())
            # Let Qt/Houdini receive the plain path in parameter text fields.
            widget=obj
            while widget is not None:
                if isinstance(widget,(QtWidgets.QLineEdit,QtWidgets.QTextEdit,QtWidgets.QPlainTextEdit)):
                    if event.type()==QtCore.QEvent.Type.Drop:
                        if isinstance(widget,QtWidgets.QLineEdit):
                            if widget.isReadOnly():return False
                            widget.setText(payload['path'])
                            widget.textEdited.emit(payload['path'])
                            widget.editingFinished.emit()
                        else:
                            if widget.isReadOnly():return False
                            widget.insertPlainText(payload['path'])
                        event.acceptProposedAction()
                        return True
                    event.acceptProposedAction()
                    return True
                widget=widget.parentWidget() if isinstance(widget,QtWidgets.QWidget) else None
            pane=hou.ui.paneTabUnderCursor()
            if pane is None or pane.type()!=hou.paneTabType.NetworkEditor:
                return False
            if not can_import(pane.pwd(),payload['kind']):
                event.ignore()
                return True  # Texture drags must not become unexpected file/COP nodes.
            if event.type()==QtCore.QEvent.Type.Drop:
                node=import_payload(payload,pane.pwd(),pane.cursorPosition())
                hou.ui.setStatusMessage('NanakusaAssetLibrary: '+node.path())
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
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library=library
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, actions):
        item=self.currentItem()
        if item is None:return
        row=item.data(QtCore.Qt.ItemDataRole.UserRole)
        try:
            path=self.library.resolve(row)
        except Exception as exc:
            hou.ui.setStatusMessage(str(exc),severity=hou.severityType.Error)
            return
        drag=QtGui.QDrag(self)
        drag.setMimeData(mime_data(path,row['effective_kind'],row['label']))
        drag.setPixmap(item.icon().pixmap(96,64))
        drag.exec(QtCore.Qt.DropAction.CopyAction)
