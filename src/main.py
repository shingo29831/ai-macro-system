# @role: アプリケーションのエントリポイント。QApplicationの初期化とメインウィンドウの起動処理、およびバックグラウンドサーバーの管理を制御する。
import sys
import logging
from PySide6.QtWidgets import QApplication
from ui.views.main_window import MainWindow
from ui.viewmodels.main_viewmodel import MainViewModel
from engines.manager import LocalServerManager

# グローバルロガーの基本設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    # UIスレッドのフリーズを防ぐため、GUI初期化前に別プロセスとしてAIサーバー群をウォームアップ
    server_manager = LocalServerManager()
    server_manager.start_servers()

    app = QApplication(sys.argv)
    
    app.setStyle('Fusion')
    
    viewmodel = MainViewModel()
    window = MainWindow(viewmodel)
    window.show()
    
    exit_code = app.exec()
    
    sys.exit(exit_code)