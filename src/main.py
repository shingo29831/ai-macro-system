# @role: アプリケーションのエントリポイント。QApplicationの初期化とメインウィンドウの起動処理を制御する。
import sys
from PySide6.QtWidgets import QApplication
from ui.views.main_window import MainWindow
from ui.viewmodels.main_viewmodel import MainViewModel

if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    app.setStyle('Fusion')
    
    viewmodel = MainViewModel()
    window = MainWindow(viewmodel)
    window.show()
    
    sys.exit(app.exec())