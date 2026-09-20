from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python3.13libs'))
from hutil.PySide import QtCore,QtGui,QtWidgets
from nanakusa_asset_library.ui import square_preview, ThumbnailJob


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_square_image_fills_square_without_bars(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'square.png'
            source=QtGui.QImage(64,64,QtGui.QImage.Format.Format_RGBA8888)
            source.fill(QtGui.QColor('red'));source.save(str(path))
            result=square_preview(path,144).toImage()
            self.assertEqual(result.size(),QtCore.QSize(144,144))
            for x,y in ((0,0),(143,143),(0,143),(143,0)):
                self.assertEqual(result.pixelColor(x,y),QtGui.QColor('red'))

    def test_wide_image_keeps_aspect_and_has_transparent_margins(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'wide.png'
            source=QtGui.QImage(400,100,QtGui.QImage.Format.Format_RGBA8888)
            source.fill(QtGui.QColor('red'));source.save(str(path))
            result=square_preview(path,200).toImage()
            self.assertEqual(result.pixelColor(0,100),QtGui.QColor('red'))
            self.assertEqual(result.pixelColor(100,0).alpha(),0)
            self.assertEqual(sum(result.pixelColor(100,y).alpha()>0 for y in range(200)),50)

    def test_conversion_does_not_bake_display_window_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'wide.png';dest=Path(folder)/'preview.png'
            image=QtGui.QImage(400,100,QtGui.QImage.Format.Format_RGBA8888)
            image.fill(QtGui.QColor('red'));image.save(str(source))
            results=[];job=ThumbnailJob('asset',source,dest)
            job.done.connect(lambda aid,path,error: results.append(error));job.run()
            self.assertEqual(results,[''])
            result=QtGui.QImage(str(dest))
            self.assertEqual(result.size(),QtCore.QSize(512,128))
            self.assertEqual(result.pixelColor(0,0),QtGui.QColor('red'))
            self.assertEqual(result.pixelColor(511,127),QtGui.QColor('red'))

if __name__=='__main__':unittest.main()
