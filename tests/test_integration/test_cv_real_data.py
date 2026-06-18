# @role: 実際に記録されたスクリーンショット画像を用いて、CVサーバー（YOLO/OCR）の解析結果を目視確認・検証するための結合テスト。

import sys
import os
import pytest
from pathlib import Path

# アプリケーション実行時とモジュールのパス解決を一致させるため、src をシステムパスに追加
sys.path.insert(0, os.path.abspath('src'))

from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

def get_latest_macro_image() -> str:
    """macros/ ディレクトリから最新の記録画像（フルスクリーン）を1枚取得する"""
    macros_root = Path(__file__).resolve().parent.parent.parent / "macros"
    if not macros_root.exists():
        return None

    # 最新のマクロディレクトリを探す (wf_XXX)
    macro_dirs = sorted(
        [d for d in macros_root.iterdir() if d.is_dir() and d.name.startswith("wf_")], 
        key=os.path.getmtime, 
        reverse=True
    )
    
    for m_dir in macro_dirs:
        images_dir = m_dir / "images"
        if images_dir.exists():
            # evt_XXX_pre.png（操作直前の全体画像）を探す
            images = list(images_dir.glob("*_pre.png"))
            if images:
                return str(images[0].resolve())
    return None

@pytest.mark.skipif(not get_latest_macro_image(), reason="記録された画像(macros/wf_*/images/*_pre.png)が見つかりません。先にアプリで記録を行ってください。")
def test_real_image_cv_analysis():
    """正常系: 実際の画像パスをCVサーバーに送信し、解析結果を取得できることを確認する"""
    image_path = get_latest_macro_image()
    
    print(f"\n\n{'='*50}")
    print(f"🔍 実データ解析テスト開始")
    print(f"対象画像: {image_path}")
    print(f"{'='*50}")

    # YOLOの解析実行
    print("\n▼ YOLOによるUI要素検出を実行中...")
    ui_results = detect_ui_elements(image_path)
    print(f"検出されたUI要素数: {len(ui_results)}")
    for i, ui in enumerate(ui_results):
        print(f"  [{i+1:02d}] Type: {ui.type:10s} | Confidence: {ui.confidence:.2f} | BBox: {ui.boundingBox}")

    # OCRの解析実行
    print("\n▼ OCRによるテキスト認識を実行中...")
    text_results = read_text_from_image(image_path)
    print(f"検出されたテキスト数: {len(text_results)}")
    for i, txt in enumerate(text_results):
        # 日本語などのテキストが見やすいようにフォーマット
        print(f"  [{i+1:02d}] Text: '{txt.content}' | Confidence: {txt.confidence:.2f} | BBox: {txt.boundingBox}")

    print(f"\n{'='*50}\n")

    # API呼び出し自体が成功していることをアサート
    assert isinstance(ui_results, list), "YOLOの戻り値がリストではありません。"
    assert isinstance(text_results, list), "OCRの戻り値がリストではありません。"