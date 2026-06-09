# @role: アプリケーションのエントリポイント。QApplicationの初期化とメインウィンドウの起動処理を制御する。
import sys
from PySide6.QtWidgets import QApplication
from ui.views.main_window import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # OS固有のダークモードによる配色崩れを防ぎ、ホワイトテーマのCSSを正確に適用するため設定
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())