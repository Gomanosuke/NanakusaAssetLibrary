"""Drag payload and narrowly scoped Qt drop filter for this library only.

Other applications' drags and Houdini's normal node drags are untouched.
Plain text and file URLs are included for native parameter-field drops.
"""
import json
from pathlib import Path
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import houdini_ops as ops
from . import pbr

MIME = 'application/x-nanakusa-asset'
_filter = None

def mime_data(path, kind, label):
    return mime_data_many([{'path':path,'kind':kind,'label':label}])


def paths_text(paths):
    paths=[Path(p).resolve().as_posix() for p in paths]
    if len(paths)==1:return paths[0]
    return ' '.join('"'+p+'"' if any(c.isspace() for c in p) else p for p in paths)


def mime_data_many(items):
    items=[dict(item,path=Path(item['path']).resolve().as_posix()) for item in items]
    mime = QtCore.QMimeData()
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
                for group in groups:nodes.append(ops.texture_material(parent,group['maps'],group['label']))
            else:
                for p in payloads:nodes.append(ops.import_into_context(p['path'],p['kind'],p['label'],parent))
            origin=position if position is not None else hou.Vector2(0,0)
            for i,node in enumerate(nodes):
                node.setPosition(origin+hou.Vector2((i%5)*3,-(i//5)*2))
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
            if pane is None or pane.type()!=hou.paneTabType.NetworkEditor:
                return False
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
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library=library
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

    def startDrag(self, actions):
        items=self.selectedItems()
        if not items:return
        try:
            payloads=[]
            for item in items:
                row=item.data(QtCore.Qt.ItemDataRole.UserRole)
                payloads.append({'path':self.library.resolve(row),'kind':row['effective_kind'],'label':row['label']})
        except Exception as exc:
            hou.ui.setStatusMessage(str(exc),severity=hou.severityType.Error)
            return
        drag=QtGui.QDrag(self)
        drag.setMimeData(mime_data_many(payloads))
        drag.setPixmap(items[0].icon().pixmap(96,96))
        drag.exec(QtCore.Qt.DropAction.CopyAction)
