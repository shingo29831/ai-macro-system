# Role: 画像比較（SSIM, ORB）および画面マッチングの待機ロジックを担当するモジュール

import time
import logging
from pathlib import Path
import cv2
import numpy as np

from core.recorder.screen_capturer import take_screenshot
from core.executor.window_manager import get_system_window_rects

logger = logging.getLogger(__name__)

def calculate_ssim(img1, img2, mask=None):
    C1 = 6.5025
    C2 = 58.5225
    i1 = img1.astype(np.float32)
    i2 = img2.astype(np.float32)
    mu1 = cv2.GaussianBlur(i1, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(i2, (11, 11), 1.5)
    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(i1 ** 2, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(i2 ** 2, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(i1 * i2, (11, 11), 1.5) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))
    
    if mask is not None:
        valid_pixels = mask > 0
        if not np.any(valid_pixels):
            return 0.0
        return float(ssim_map[valid_pixels].mean())
    return float(ssim_map.mean())

def calculate_orb_match(img1, img2, mask=None):
    orb = cv2.ORB_create(nfeatures=500)
    kp1, des1 = orb.detectAndCompute(img1, mask)
    kp2, des2 = orb.detectAndCompute(img2, mask)
    if des1 is None or des2 is None or len(kp1) == 0 or len(kp2) == 0:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    good_matches = [m for m in matches if m.distance < 50]
    return float(len(good_matches) / max(len(kp1), 1))

def wait_for_screen_match(target_dir: Path, raw_event_id: str, win_x: int, win_y: int, win_w: int, win_h: int, workflow_id: str, status_callback, step_index: int, timeout: float = 30.0, check_cancel_callback=None) -> dict:
    result_info = {"matched": False, "time_taken": 0.0, "scores": {}}
    if not raw_event_id or win_w <= 0 or win_h <= 0:
        return result_info

    pre_image_path = target_dir / "images" / f"{raw_event_id}_pre.png"
    if not pre_image_path.exists():
        return result_info
        
    exec_logs_dir = target_dir / "execution_logs"
    exec_logs_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        import shutil
        target_copy_path = exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_target.png"
        if not target_copy_path.exists():
            shutil.copy2(pre_image_path, target_copy_path)
    except Exception as e:
        logger.warning(f"Failed to copy target image: {e}")

    def update_ui(text, is_warning):
        if status_callback:
            status_callback(text, is_warning)
        else:
            try:
                from ui.views.running_dialog import RunningDialog
                RunningDialog.set_status(text, is_warning)
            except Exception:
                pass

    try:
        pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
        if pre_img_cv is None:
            return result_info
            
        img_h, img_w = pre_img_cv.shape
        
        _, monitor_info = take_screenshot()
        offset_x = monitor_info.get("left", 0) if isinstance(monitor_info, dict) else 0
        offset_y = monitor_info.get("top", 0) if isinstance(monitor_info, dict) else 0
        
        margin = 8
        x1 = max(0, win_x - offset_x + margin)
        y1 = max(0, win_y - offset_y + margin)
        x2 = min(img_w, win_x - offset_x + win_w - margin)
        y2 = min(img_h, win_y - offset_y + win_h - margin)
        
        if x2 <= x1 or y2 <= y1:
            x1, y1 = max(0, win_x + margin), max(0, win_y + margin)
            x2, y2 = min(img_w, win_x + win_w - margin), min(img_h, win_y + win_h - margin)
            if x2 <= x1 or y2 <= y1:
                return result_info
            
        pre_crop = pre_img_cv[y1:y2, x1:x2]
        
        scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
        if scale < 1.0:
            pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            pre_crop_eval = pre_crop.copy()
            
        waiting_logged = False
        frame_buffer = [] 
        start_time = time.time()
        last_frame_crop = None
        
        while True:
            if check_cancel_callback and check_cancel_callback():
                break
                
            if time.time() - start_time > timeout:
                logger.warning(f"[{workflow_id}] Screen match timeout ({timeout}s). Proceeding to next action.")
                if waiting_logged:
                    update_ui("タイムアウトしました。マクロを再開します。", False)
                
                try:
                    if 'curr_crop_color' in locals() and 'video_mask' in locals() and 'text_mask' in locals():
                        if video_mask.shape[:2] != curr_crop_color.shape[:2]:
                            video_mask_resized = cv2.resize(video_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST)
                            text_mask_resized = cv2.resize(text_mask, (curr_crop_color.shape[1], curr_crop_color.shape[0]), interpolation=cv2.INTER_NEAREST)
                        else:
                            video_mask_resized = video_mask
                            text_mask_resized = text_mask
                        
                        overlay = curr_crop_color.copy()
                        overlay[video_mask_resized == 255] = [0, 0, 255]
                        overlay[text_mask_resized == 255] = [0, 255, 0]
                        
                        alpha = 0.5
                        highlighted_img = cv2.addWeighted(overlay, alpha, curr_crop_color, 1 - alpha, 0)
                        
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_timeout_masked.png"), highlighted_img)
                        logger.info(f"[{workflow_id}] Saved timeout masked image (Video: Red, Text: Green).")
                except Exception as e:
                    logger.warning(f"Failed to save timeout masked image: {e}")
                    
                break

            curr_img_pil, curr_monitor = take_screenshot()
            curr_img_color = cv2.cvtColor(np.array(curr_img_pil), cv2.COLOR_RGB2BGR)
            curr_img_cv = cv2.cvtColor(curr_img_color, cv2.COLOR_BGR2GRAY)
            
            c_offset_x = curr_monitor.get("left", 0) if isinstance(curr_monitor, dict) else 0
            c_offset_y = curr_monitor.get("top", 0) if isinstance(curr_monitor, dict) else 0
            
            curr_h, curr_w = curr_img_cv.shape
            cx1 = max(0, win_x - c_offset_x + margin)
            cy1 = max(0, win_y - c_offset_y + margin)
            cx2 = min(curr_w, win_x - c_offset_x + win_w - margin)
            cy2 = min(curr_h, win_y - c_offset_y + win_h - margin)
            
            if cx2 <= cx1 or cy2 <= cy1:
                cx1, cy1 = max(0, win_x), max(0, win_y)
                cx2, cy2 = min(curr_w, win_x + win_w), min(curr_h, win_y + win_h)
                
            if cx2 > cx1 and cy2 > cy1:
                curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
                curr_crop_color = curr_img_color[cy1:cy2, cx1:cx2]
                if scale < 1.0:
                    curr_crop_eval = cv2.resize(curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA)
                else:
                    curr_crop_eval = curr_crop.copy()
                
                frame_buffer.append(curr_crop_eval)
                if len(frame_buffer) > 5:
                    frame_buffer.pop(0)
                
                video_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
                if len(frame_buffer) >= 3:
                    std_dev = np.std(frame_buffer, axis=0)
                    video_mask = (std_dev > 20).astype(np.uint8) * 255
                    
                    edges_list = [cv2.Canny(f, 50, 150) for f in frame_buffer]
                    static_edges = edges_list[0]
                    for e in edges_list[1:]:
                        static_edges = cv2.bitwise_and(static_edges, e)
                    
                    kernel_protect = np.ones((3, 3), np.uint8)
                    static_edges_dilated = cv2.dilate(static_edges, kernel_protect, iterations=1)
                    
                    video_mask[static_edges_dilated == 255] = 0
                    
                    if np.count_nonzero(video_mask) / video_mask.size > 0.3:
                        video_mask = np.zeros_like(video_mask)
                
                diff_for_mask = cv2.absdiff(pre_crop_eval, curr_crop_eval)
                _, diff_thresh = cv2.threshold(diff_for_mask, 30, 255, cv2.THRESH_BINARY)

                kernel_text = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                diff_dilated = cv2.dilate(diff_thresh, kernel_text, iterations=1)

                text_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
                contours, _ = cv2.findContours(diff_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                for cnt in contours:
                    x, y, w, h = cv2.boundingRect(cnt)
                    if 2 <= w < curr_crop_eval.shape[1] * 0.8 and 5 <= h < curr_crop_eval.shape[0] * 0.5:
                        region_curr = curr_crop_eval[y:y+h, x:x+w]
                        region_pre = pre_crop_eval[y:y+h, x:x+w]
                        region_diff = diff_thresh[y:y+h, x:x+w]

                        edges_curr = cv2.Canny(region_curr, 50, 150)
                        edges_pre = cv2.Canny(region_pre, 50, 150)

                        valid_edges_curr = cv2.bitwise_and(edges_curr, edges_curr, mask=region_diff)
                        valid_edges_pre = cv2.bitwise_and(edges_pre, edges_pre, mask=region_diff)

                        area = w * h
                        edge_count = max(np.count_nonzero(valid_edges_curr), np.count_nonzero(valid_edges_pre))
                        edge_density = edge_count / area if area > 0 else 0
                        if edge_density > 0.05:
                            text_mask[y:y+h, x:x+w] = cv2.bitwise_or(text_mask[y:y+h, x:x+w], region_diff)

                            try:
                                debug_dir = exec_logs_dir / "text_detection_debug"
                                debug_dir.mkdir(parents=True, exist_ok=True)
                                timestamp = int(time.time() * 1000)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_curr.png"), region_curr)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_pre.png"), region_pre)
                                cv2.imwrite(str(debug_dir / f"step_{step_index:03d}_{raw_event_id}_{timestamp}_diff.png"), region_diff)
                            except Exception as e:
                                logger.warning(f"Failed to save text detection debug image: {e}")

                dynamic_mask = cv2.bitwise_or(video_mask, text_mask)
                
                system_rects = get_system_window_rects()
                for (sl, st, sr, sb) in system_rects:
                    abs_cx1 = c_offset_x + cx1
                    abs_cy1 = c_offset_y + cy1
                    ml = max(0, int((sl - abs_cx1) * scale))
                    mt = max(0, int((st - abs_cy1) * scale))
                    mr = min(curr_crop_eval.shape[1], int((sr - abs_cx1) * scale))
                    mb = min(curr_crop_eval.shape[0], int((sb - abs_cy1) * scale))
                    if mr > ml and mb > mt:
                        dynamic_mask[mt:mb, ml:mr] = 255
                
                static_mask = cv2.bitwise_not(dynamic_mask)
                valid_area = np.count_nonzero(static_mask)

                is_screen_changing = False
                if last_frame_crop is not None:
                    diff_with_last = cv2.absdiff(last_frame_crop, curr_crop_eval)
                    _, thresh_last = cv2.threshold(diff_with_last, 30, 255, cv2.THRESH_BINARY)
                    
                    thresh_last = cv2.bitwise_and(thresh_last, thresh_last, mask=static_mask)
                    
                    if valid_area > 500:
                        change_ratio = np.count_nonzero(thresh_last) / valid_area
                        if change_ratio > 0.05:
                            is_screen_changing = True
                last_frame_crop = curr_crop_eval.copy()
                
                pre_crop_static = cv2.bitwise_and(pre_crop_eval, pre_crop_eval, mask=static_mask)
                curr_crop_static = cv2.bitwise_and(curr_crop_eval, curr_crop_eval, mask=static_mask)
                
                diff = cv2.absdiff(pre_crop_static, curr_crop_static)
                _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
                
                if valid_area > 500: 
                    diff_ratio = np.count_nonzero(thresh) / valid_area
                else:
                    diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
                    _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)
                    diff_ratio = np.count_nonzero(thresh_full) / (curr_crop_eval.shape[0] * curr_crop_eval.shape[1])
                
                is_pixel_match = diff_ratio <= 0.05
                
                res = cv2.matchTemplate(curr_crop_static, pre_crop_static, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                is_struct_match = (max_val >= 0.88) and (diff_ratio <= 0.10)
                
                pre_edges = cv2.Canny(pre_crop_static, 50, 150)
                curr_edges = cv2.Canny(curr_crop_static, 50, 150)
                pre_edge_count = np.count_nonzero(pre_edges)
                
                is_edge_match = False
                max_val_edges = 0.0
                if pre_edge_count > 50:
                    res_edges = cv2.matchTemplate(curr_edges, pre_edges, cv2.TM_CCOEFF_NORMED)
                    _, max_val_edges, _, _ = cv2.minMaxLoc(res_edges)
                    if max_val_edges >= 0.75 and diff_ratio <= 0.15:
                        is_edge_match = True

                ssim_val = calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
                orb_score = calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

                result_info["scores"] = {
                    "diff_ratio": float(diff_ratio),
                    "sim": float(max_val),
                    "edge_sim": float(max_val_edges),
                    "ssim": ssim_val,
                    "orb": orb_score
                }

                is_ssim_match = ssim_val >= 0.75
                is_orb_match = orb_score >= 0.15 or (orb_score >= 0.08 and max_val >= 0.6)

                if is_pixel_match or is_struct_match or is_edge_match or is_ssim_match or is_orb_match:
                    try:
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_curr.png"), curr_crop)
                        cv2.imwrite(str(exec_logs_dir / f"step_{step_index:03d}_{raw_event_id}_match_pre.png"), pre_crop)
                    except Exception:
                        pass
                        
                    if waiting_logged:
                        if not is_pixel_match and (is_ssim_match or is_orb_match or is_struct_match):
                            update_ui("動的領域・テキストを除外して一致を確認しました。\nマクロを再開します。", False)
                        else:
                            update_ui("画面の一致を確認しました。\nマクロを再開します。", False)
                            
                        logger.info(f"[{workflow_id}] Screen matched (diff: {diff_ratio:.1%}, sim: {max_val:.2f}, ssim: {ssim_val:.2f}, orb: {orb_score:.2f}). Resuming.")
                        time.sleep(1.5)
                    result_info["matched"] = True
                    break
                else:
                    if is_screen_changing:
                        update_ui("画面遷移を待機しています...", False)
                        waiting_logged = False
                    else:
                        if time.time() - start_time > 3.0:
                            detail_msg = f"記録時と同じ画面にしてください。\n差分: {diff_ratio:.1%} / 構造: {ssim_val:.2f} / 特徴: {orb_score:.2f}"
                            update_ui(detail_msg, True)
                            if not waiting_logged:
                                logger.info(f"[{workflow_id}] Waiting for screen to match... (diff: {diff_ratio:.1%}, sim: {max_val:.2f}, ssim: {ssim_val:.2f}, orb: {orb_score:.2f})")
                                waiting_logged = True
                        elif not waiting_logged:
                            update_ui("画面の応答を待機しています...", False)
            else:
                if time.time() - start_time > 3.0:
                    update_ui("記録時と同じ画面にしてください。\n(ウィンドウサイズが異なります)", True)
                    if not waiting_logged:
                        logger.info(f"[{workflow_id}] Waiting for screen to match... (size mismatch)")
                        waiting_logged = True
                elif not waiting_logged:
                    update_ui("画面の応答を待機しています...", False)
            
            time.sleep(0.5)
            
        result_info["time_taken"] = time.time() - start_time
    except Exception as e:
        logger.warning(f"Error during screen match waiting: {e}")
        
    return result_info

def is_screen_match(pre_image_path: Path, curr_img_cv, win_x: int, win_y: int, win_w: int, win_h: int, offset_x: int, offset_y: int, global_dynamic_mask=None) -> tuple[bool, dict]:
    scores = {"diff_ratio": 1.0, "sim": 0.0, "edge_sim": 1.0, "ssim": 0.0, "orb": 0.0}

    if not pre_image_path.exists():
        return False, scores

    pre_img_cv = cv2.imread(str(pre_image_path), cv2.IMREAD_GRAYSCALE)
    if pre_img_cv is None:
        return False, scores

    img_h, img_w = pre_img_cv.shape
    margin = 8
    
    x1 = max(0, win_x - offset_x + margin)
    y1 = max(0, win_y - offset_y + margin)
    x2 = min(img_w, win_x - offset_x + win_w - margin)
    y2 = min(img_h, win_y - offset_y + win_h - margin)
    
    if x2 <= x1 or y2 <= y1:
        x1, y1 = max(0, win_x + margin), max(0, win_y + margin)
        x2, y2 = min(img_w, win_x + win_w - margin), min(img_h, win_y + win_h - margin)
        if x2 <= x1 or y2 <= y1:
            return False, scores
            
    pre_crop = pre_img_cv[y1:y2, x1:x2]
    curr_h, curr_w = curr_img_cv.shape
    cx1 = max(0, win_x - offset_x + margin)
    cy1 = max(0, win_y - offset_y + margin)
    cx2 = min(curr_w, win_x - offset_x + win_w - margin)
    cy2 = min(curr_h, win_y - offset_y + win_h - margin)
    
    if cx2 <= cx1 or cy2 <= cy1:
        cx1, cy1 = max(0, win_x + margin), max(0, win_y + margin)
        cx2, cy2 = min(curr_w, win_x + win_w - margin), min(curr_h, win_y + win_h - margin)
        if cx2 <= cx1 or cy2 <= cy1:
            return False, scores
            
    curr_crop = curr_img_cv[cy1:cy2, cx1:cx2]
    
    scale = min(1.0, 512.0 / max(pre_crop.shape[0], pre_crop.shape[1]))
    if scale < 1.0:
        pre_crop_eval = cv2.resize(pre_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        curr_crop_eval = cv2.resize(curr_crop, (pre_crop_eval.shape[1], pre_crop_eval.shape[0]), interpolation=cv2.INTER_AREA)
    else:
        pre_crop_eval = pre_crop.copy()
        if pre_crop.shape != curr_crop.shape:
            curr_crop_eval = cv2.resize(curr_crop, (pre_crop.shape[1], pre_crop.shape[0]), interpolation=cv2.INTER_AREA)
        else:
            curr_crop_eval = curr_crop.copy()

    static_mask = np.ones_like(curr_crop_eval, dtype=np.uint8) * 255
    if global_dynamic_mask is not None:
        dynamic_crop = global_dynamic_mask[cy1:cy2, cx1:cx2]
        if scale < 1.0:
            dynamic_crop_eval = cv2.resize(dynamic_crop, (curr_crop_eval.shape[1], curr_crop_eval.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            dynamic_crop_eval = dynamic_crop.copy()
            if dynamic_crop.shape != curr_crop_eval.shape:
                dynamic_crop_eval = cv2.resize(dynamic_crop, (curr_crop_eval.shape[1], curr_crop_eval.shape[0]), interpolation=cv2.INTER_NEAREST)
        
        curr_crop_eval[dynamic_crop_eval == 255] = 0
        pre_crop_eval[dynamic_crop_eval == 255] = 0
        static_mask[dynamic_crop_eval == 255] = 0

    system_rects = get_system_window_rects()
    for (sl, st, sr, sb) in system_rects:
        abs_cx1 = offset_x + cx1
        abs_cy1 = offset_y + cy1
        ml = max(0, int((sl - abs_cx1) * scale))
        mt = max(0, int((st - abs_cy1) * scale))
        mr = min(curr_crop_eval.shape[1], int((sr - abs_cx1) * scale))
        mb = min(curr_crop_eval.shape[0], int((sb - abs_cy1) * scale))
        if mr > ml and mb > mt:
            curr_crop_eval[mt:mb, ml:mr] = 0
            pre_crop_eval[mt:mb, ml:mr] = 0
            static_mask[mt:mb, ml:mr] = 0

    diff_for_mask = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, diff_thresh = cv2.threshold(diff_for_mask, 30, 255, cv2.THRESH_BINARY)

    kernel_text = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    diff_dilated = cv2.dilate(diff_thresh, kernel_text, iterations=1)

    text_mask = np.zeros_like(curr_crop_eval, dtype=np.uint8)
    contours, _ = cv2.findContours(diff_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 2 <= w < curr_crop_eval.shape[1] * 0.8 and 5 <= h < curr_crop_eval.shape[0] * 0.5:
            region_curr = curr_crop_eval[y:y+h, x:x+w]
            region_pre = pre_crop_eval[y:y+h, x:x+w]
            region_diff = diff_thresh[y:y+h, x:x+w]

            edges_curr = cv2.Canny(region_curr, 50, 150)
            edges_pre = cv2.Canny(region_pre, 50, 150)

            valid_edges_curr = cv2.bitwise_and(edges_curr, edges_curr, mask=region_diff)
            valid_edges_pre = cv2.bitwise_and(edges_pre, edges_pre, mask=region_diff)

            area = w * h
            edge_count = max(np.count_nonzero(valid_edges_curr), np.count_nonzero(valid_edges_pre))
            edge_density = edge_count / area if area > 0 else 0
            if edge_density > 0.05:
                cv2.rectangle(text_mask, (x, y), (x+w, y+h), 255, -1)

    curr_crop_eval[text_mask == 255] = 0
    pre_crop_eval[text_mask == 255] = 0
    static_mask[text_mask == 255] = 0

    diff_full = cv2.absdiff(pre_crop_eval, curr_crop_eval)
    _, thresh_full = cv2.threshold(diff_full, 30, 255, cv2.THRESH_BINARY)
    
    valid_area = np.count_nonzero(static_mask)
    if valid_area > 500:
        diff_ratio = np.count_nonzero(thresh_full) / valid_area
    else:
        diff_ratio = np.count_nonzero(thresh_full) / (pre_crop_eval.shape[0] * pre_crop_eval.shape[1])
    
    pre_edges = cv2.Canny(pre_crop_eval, 50, 150)
    curr_edges = cv2.Canny(curr_crop_eval, 50, 150)
    
    edge_diff = cv2.absdiff(pre_edges, curr_edges)
    if valid_area > 500:
        edge_diff_ratio = np.count_nonzero(cv2.bitwise_and(edge_diff, edge_diff, mask=static_mask)) / valid_area
    else:
        edge_diff_ratio = np.count_nonzero(edge_diff) / (pre_crop_eval.shape[0] * pre_crop_eval.shape[1])
    
    res = cv2.matchTemplate(curr_crop_eval, pre_crop_eval, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)

    ssim_val = calculate_ssim(pre_crop_eval, curr_crop_eval, mask=static_mask)
    orb_score = calculate_orb_match(pre_crop_eval, curr_crop_eval, mask=static_mask)

    scores = {
        "diff_ratio": float(diff_ratio),
        "sim": float(max_val),
        "edge_sim": float(edge_diff_ratio),
        "ssim": ssim_val,
        "orb": orb_score
    }

    is_exact_match = (diff_ratio <= 0.05) and (edge_diff_ratio <= 0.03) and (max_val >= 0.92)
    is_high_match = (diff_ratio <= 0.15) and (edge_diff_ratio <= 0.10) and (max_val >= 0.85)
    is_structural_match = (ssim_val >= 0.75) or (orb_score >= 0.15) or (orb_score >= 0.08 and max_val >= 0.6)
    
    return (is_exact_match or is_high_match or is_structural_match), scores