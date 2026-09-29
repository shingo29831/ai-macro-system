# src/main.py
# @role: Application entry point that initializes the PySide6 application and handles the core lifecycle.

import sys
from PySide6.QtWidgets import QApplication
from ui.views.main_window import MainWindow
from ui.viewmodels.main_viewmodel import MainViewModel
from engines.manager import LocalServerManager

def main():
    # Why: Windows環境での高DPIスケーリングによる画面キャプチャ・座標のズレを完全防止
    from core.executor.os_env_controller import set_dpi_awareness
    set_dpi_awareness()

    app = QApplication(sys.argv)
    
    # Why: メインGUIスレッドで透過オーバーレイHUDを事前バインドしスレッド間競合を根絶
    from ui.views.cursor_overlay import CursorOverlayManager
    CursorOverlayManager.get_instance().init_ui()

    # Initialize and start local AI servers natively
    server_manager = LocalServerManager()
    server_manager.start_servers()
    
    # Connect the application lifecycle directly to server cleanup to prevent port conflicts (Errno 10048)
    app.aboutToQuit.connect(server_manager.stop_servers)
    
    # MVVMアーキテクチャの疎結合を維持するため、ViewModelを生成しViewへ注入する
    viewmodel = MainViewModel()
    window = MainWindow(viewmodel)
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()