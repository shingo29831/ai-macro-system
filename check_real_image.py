import sys
import os
import json

# srcディレクトリ内のモジュールを読み込めるようにパスを追加
sys.path.insert(0, os.path.abspath('src'))

from engines.yolo.detector import detect_ui_elements
from engines.ocr.reader import read_text_from_image

def main():
    # テストに使いたい実際のスクリーンショット画像のパス
    image_path = os.path.abspath("sample.png")

    if not os.path.exists(image_path):
        print(f"【エラー】画像が見つかりません: {image_path}")
        print("適当なスクリーンショットを撮影し、'sample.png' という名前でプロジェクト直下に配置してください。")
        return

    print(f"=== 対象画像: {image_path} ===\n")

    # 1. YOLOによるUI検出テスト
    print("--- 🟢 YOLO UI検出テスト ---")
    try:
        ui_results = detect_ui_elements(image_path)
        print(f"検出数: {len(ui_results)}件")
        for i, ui in enumerate(ui_results):
            # pydanticモデルなのでプロパティとしてアクセス
            print(f"  [{i+1}] 種類: {ui.type}, 確信度: {ui.confidence:.2f}, 座標: {ui.boundingBox}")
    except Exception as e:
        print(f"YOLOエラー: {e}")

    # 2. OCRによるテキスト認識テスト
    print("\n--- 🔵 OCR テキスト認識テスト ---")
    try:
        text_results = read_text_from_image(image_path)
        print(f"検出数: {len(text_results)}件")
        for i, txt in enumerate(text_results):
            print(f"  [{i+1}] テキスト: '{txt.content}', 確信度: {txt.confidence:.2f}, 座標: {txt.boundingBox}")
    except Exception as e:
        print(f"OCRエラー: {e}")

if __name__ == "__main__":
    main()