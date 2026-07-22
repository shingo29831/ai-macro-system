from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt

from .action_block_widget import ActionBlockWidget

class MacroVisualCanvas(QWidget):
    def __init__(self, commands: list, workflow_dir: Path, parent=None):
        super().__init__(parent)
        self.commands = commands
        self.workflow_dir = workflow_dir
        self.blocks = []
        self.loops = []
        
        self._analyze_loops()
        self._build_ui()
        
    def _analyze_loops(self):
        """
        コマンドリストを解析し、ループの範囲と左右レーンの割り当てを計算する。
        """
        self.loops = []
        loop_stack = []
        action_idx = 0
        
        # 1. ループの範囲（開始・終了ブロックのインデックス）を特定
        for cmd in self.commands:
            method = cmd.get("method")
            if method == "loop_start":
                loop_stack.append({
                    "start_action_idx": action_idx,
                    "loop_count": cmd.get("args", {}).get("loop_count", 1)
                })
            elif method == "loop_end":
                if loop_stack:
                    loop_info = loop_stack.pop()
                    # loop_end の直前のアクションが終了ブロック
                    loop_info["end_action_idx"] = action_idx - 1
                    loop_info["size"] = loop_info["end_action_idx"] - loop_info["start_action_idx"]
                    self.loops.append(loop_info)
            else:
                # 実際のアクションブロックのみカウント
                action_idx += 1
                
        # 2. ループを大きさ（包含するブロック数）の降順にソート
        self.loops.sort(key=lambda x: x["size"], reverse=True)
        
        right_loops = []
        left_loops = []
        
        # 3. 左右に順番に割り振り、交差しないようにレーン（外側への距離）を計算
        for i, loop in enumerate(self.loops):
            if i % 2 == 0:
                direction = "right"
                target_list = right_loops
            else:
                direction = "left"
                target_list = left_loops
                
            # 同じ方向に割り当てられたループの中で、範囲が重なるものを探す
            overlapping_lanes = []
            for other in target_list:
                # 重なり判定（一方が他方の完全に外側でない場合は重なっている）
                if not (loop["end_action_idx"] < other["start_action_idx"] or loop["start_action_idx"] > other["end_action_idx"]):
                    overlapping_lanes.append(other["lane"])
                    
            # 重なるループの最大レーン + 1 を自分のレーンとする（重ならなければレーン0）
            lane = max(overlapping_lanes) + 1 if overlapping_lanes else 0
            
            loop["direction"] = direction
            loop["lane"] = lane
            target_list.append(loop)

    def _build_ui(self):
        # 中央揃えの縦レイアウト
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # ループ矢印が上下にはみ出さないようマージンを少し大きめに設定
        self.main_layout.setContentsMargins(100, 60, 100, 60)
        self.main_layout.setSpacing(80) # 矢印を描画するためのブロック間の余白
        
        for cmd in self.commands:
            method = cmd.get("method")
            if method in ["loop_start", "loop_end"]:
                continue
                
            block = ActionBlockWidget(cmd, self.workflow_dir)
            self.main_layout.addWidget(block)
            self.blocks.append(block)
            
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.blocks:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # --- 1. フロー矢印（ブロック間の下向き直線）の描画 ---
        flow_pen = QPen(QColor(180, 180, 180), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(flow_pen)
        
        for i in range(len(self.blocks) - 1):
            block1 = self.blocks[i]
            block2 = self.blocks[i+1]
            
            x = block1.geometry().center().x()
            y1 = block1.geometry().bottom()
            y2 = block2.geometry().top()
            
            painter.drawLine(x, y1, x, y2)
            
            arrow_size = 8
            painter.drawLine(x, y2, x - arrow_size, y2 - arrow_size)
            painter.drawLine(x, y2, x + arrow_size, y2 - arrow_size)

        # --- 2. ループ矢印の描画 ---
        loop_pen = QPen(QColor("#0078d4"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(loop_pen)
        
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        
        for loop in self.loops:
            start_idx = loop["start_action_idx"]
            end_idx = loop["end_action_idx"]
            
            if start_idx < 0 or end_idx >= len(self.blocks) or start_idx > end_idx:
                continue
                
            start_block = self.blocks[start_idx]
            end_block = self.blocks[end_idx]
            
            x_center = start_block.geometry().center().x()
            
            # ブロック間スペース(80)の中間点(40)を曲がるポイントとする
            y_bottom = end_block.geometry().bottom() + 40
            y_top = start_block.geometry().top() - 40
            
            # ブロックの幅(340)の半分(170) + 基本余白(20)
            base_offset = 190
            lane_width = 40
            offset = base_offset + loop["lane"] * lane_width
            
            if loop["direction"] == "right":
                x_turn = x_center + offset
            else:
                x_turn = x_center - offset
                
            # コの字型の線を描画（下端から出て上端へ戻る）
            painter.drawLine(x_center, y_bottom, x_turn, y_bottom)
            painter.drawLine(x_turn, y_bottom, x_turn, y_top)
            painter.drawLine(x_turn, y_top, x_center, y_top)
            
            # 矢印の先端（上端中央で下向きに合流）
            arrow_size = 8
            painter.drawLine(x_center, y_top, x_center - arrow_size, y_top - arrow_size)
            painter.drawLine(x_center, y_top, x_center + arrow_size, y_top - arrow_size)
            
            # ループ回数のテキストを描画
            text = f"{loop['loop_count']}回"
            text_y = (y_top + y_bottom) / 2
            
            if loop["direction"] == "right":
                painter.drawText(x_turn + 8, text_y, text)
            else:
                text_width = fm.horizontalAdvance(text)
                painter.drawText(x_turn - text_width - 8, text_y, text)