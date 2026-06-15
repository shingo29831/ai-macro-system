# @role: 生成されたワークフローをローカルで自律実行し、即時ポーリングによる最速化を実現する。
# 
# 【参照元】
#   - ui/viewmodels/main_viewmodel.py (マクロ実行・強制停止)
# 
# 【参照先】
#   - models/data_types.py (Workflow, AppConfigモデル)
#   - core/healer/recovery_manager.py (異常検知・タイムアウト時の修復呼び出し)

from models.data_types import AppConfig

_is_running = False
_stop_requested = False

def run_workflow(workflow_id: str, config: AppConfig):
    """指定されたIDのマクロを読み込み、自律実行を開始する"""
    global _is_running, _stop_requested
    _is_running = True
    _stop_requested = False
    
    try:
        pass
    finally:
        _is_running = False
        _stop_requested = False

def stop_workflow():
    """実行中のマクロに対して緊急停止（キルスイッチ）シグナルを送る"""
    global _stop_requested
    _stop_requested = True