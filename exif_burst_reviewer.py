#!/usr/bin/env python3
"""
EXIF Burst Reviewer

Scans photos recursively, groups images whose EXIF capture times are within a configurable
window (default 10 seconds), and shows each group in a selectable grid.

Controls:
- Click thumbnail (or press 1-9/0) to toggle selection
- D: Mark selected photos for deletion (deferred until quit)
- K: Keep selected photos; if any are selected, non-selected in group are marked for deletion on quit
- R: Rotate all photos 90° clockwise
- N or Space: Next group with no deletion
- U: Undo last decision and return to that group
- A: Select all in current group
- C: Clear selection
- Q or Esc: Quit and finalize deferred deletions

Important behavior:
- Photos without EXIF date are excluded from grouping.
- Any photo not explicitly deleted is left in place.
"""

import math
import os
import random
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

try:
    from PIL import Image, ExifTags, ImageOps
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    from PIL import Image, ExifTags, ImageOps


@dataclass
class PhotoItem:
    path: Path
    exif_dt: datetime
    exif_text: str
    linked_paths: List[Path] = field(default_factory=list)


class ExifBurstReviewer:
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".bmp", ".gif", ".tiff", ".tif"}
    LIVE_PHOTO_VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
    ORIENTATION_CW_MAP = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}

    def __init__(self, photos_dir: str, window_seconds: int = 10, workers: int = 10, shuffle: bool = False):
        self.photos_dir = Path(photos_dir)
        self.window_seconds = window_seconds
        self.workers = max(1, workers)
        self.shuffle = shuffle

        self.total_scanned = 0
        self.with_exif = 0
        self.excluded_no_exif = 0
        self.total_groups = 0

        self.pending_deletes: Set[Path] = set()
        self.decision_history: List[Dict[str, Any]] = []

        self.current_items: List[PhotoItem] = []
        self.current_selected: Set[int] = set()
        self.current_layout: Dict[str, int] = {}
        self.current_canvas_size = (0, 0)  # (width, height)
        self.window_name = "EXIF Burst Reviewer"
        self.current_group_idx = 0
        self.current_rotation_steps = 0
        self.needs_redraw = True
        self.exiftool_path = shutil.which("exiftool")
        if not self.exiftool_path:
            print("Warning: exiftool not found. Rotation saves are disabled to prevent quality loss.")

    def parse_exif_datetime(self, value: str) -> Optional[datetime]:
        value = str(value).strip()
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    def extract_exif_datetime(self, image_path: Path) -> Optional[datetime]:
        try:
            with Image.open(image_path) as img:
                exif_data = img.getexif()
                if not exif_data:
                    return None

                preferred_tags = ["DateTimeOriginal", "DateTimeDigitized", "DateTime"]
                for tag_name in preferred_tags:
                    for tag_id, tag_value in exif_data.items():
                        if ExifTags.TAGS.get(tag_id) == tag_name:
                            parsed = self.parse_exif_datetime(str(tag_value))
                            if parsed:
                                return parsed
        except Exception:
            return None

        return None

    def find_image_paths(self) -> List[Path]:
        image_paths: List[Path] = []
        for root, _dirs, files in os.walk(self.photos_dir):
            for name in files:
                path = Path(root) / name
                if path.suffix.lower() not in self.IMAGE_EXTENSIONS:
                    continue
                image_paths.append(path)

        return image_paths

    def _build_video_lookup(self) -> Dict[Tuple[Path, str], List[Path]]:
        """Scan for video files that are Live Photo companions (same stem, same directory)."""
        lookup: Dict[Tuple[Path, str], List[Path]] = {}
        for root, _dirs, files in os.walk(self.photos_dir):
            root_path = Path(root)
            for name in files:
                path = root_path / name
                if path.suffix.lower() in self.LIVE_PHOTO_VIDEO_EXTENSIONS:
                    key = (root_path, path.stem.lower())
                    lookup.setdefault(key, []).append(path)
        return lookup

    def scan_items(self) -> List[PhotoItem]:
        items: List[PhotoItem] = []
        print(f"Scanning {self.photos_dir} recursively for images...")

        image_paths = self.find_image_paths()
        self.total_scanned = len(image_paths)
        print(f"Found {self.total_scanned:,} image files. Reading EXIF with {self.workers} workers...")

        if not image_paths:
            return items

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            exif_results = list(executor.map(self.extract_exif_datetime, image_paths))

        for path, exif_dt in zip(image_paths, exif_results):
            if exif_dt is None:
                self.excluded_no_exif += 1
                continue

            self.with_exif += 1
            items.append(PhotoItem(path=path, exif_dt=exif_dt, exif_text=exif_dt.strftime("%Y-%m-%d %H:%M:%S")))

        video_lookup = self._build_video_lookup()
        for item in items:
            key = (item.path.parent, item.path.stem.lower())
            if key in video_lookup:
                item.linked_paths = video_lookup[key]

        items.sort(key=lambda item: item.exif_dt)
        return items

    def build_groups(self, items: List[PhotoItem]) -> List[List[PhotoItem]]:
        if not items:
            return []

        groups: List[List[PhotoItem]] = []
        current_group: List[PhotoItem] = [items[0]]

        for item in items[1:]:
            prev = current_group[-1]
            delta = (item.exif_dt - prev.exif_dt).total_seconds()
            if delta <= self.window_seconds:
                current_group.append(item)
            else:
                if len(current_group) > 1:
                    groups.append(current_group)
                current_group = [item]

        if len(current_group) > 1:
            groups.append(current_group)

        self.total_groups = len(groups)
        return groups

    def apply_rotation_to_frame(self, frame: np.ndarray, rotation_steps: int) -> np.ndarray:
        """Apply clockwise 90-degree rotation steps to an image/frame."""
        steps = rotation_steps % 4
        if steps == 0:
            return frame
        if steps == 1:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if steps == 2:
            return cv2.rotate(frame, cv2.ROTATE_180)
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    def load_thumbnail(self, image_path: Path, max_w: int, max_h: int, rotation_steps: int = 0) -> np.ndarray:
        try:
            with Image.open(image_path) as img:
                # Match photo_sorter behavior: load full image, then downscale with OpenCV.
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")

                rgb = np.array(img)
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                rotated = self.apply_rotation_to_frame(bgr, rotation_steps)

                # For 90/270 degree rotations, the fitting box dimensions are swapped.
                steps = rotation_steps % 4
                target_w, target_h = (max_h, max_w) if steps in (1, 3) else (max_w, max_h)

                h, w = rotated.shape[:2]
                scale = min(target_w / w, target_h / h, 1.0)
                if scale < 1.0:
                    new_w = max(1, int(w * scale))
                    new_h = max(1, int(h * scale))
                    return cv2.resize(rotated, (new_w, new_h), interpolation=cv2.INTER_AREA)

                return rotated
        except Exception:
            fallback = np.zeros((max_h, max_w, 3), dtype=np.uint8)
            cv2.putText(fallback, "Load failed", (20, max_h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            return fallback

    def build_grid_image(self, group: List[PhotoItem], selected: Set[int]) -> np.ndarray:
        n = len(group)
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)

        # Render at display resolution so the image doesn't need upscaling by OpenCV.
        canvas_w = 1920
        canvas_h = 1060
        pad = 8
        header_h = 60
        label_reserve = 42  # height at the bottom of each cell for filename + date

        # Fill the canvas with evenly-sized cells.
        cell_w = (canvas_w - pad * (cols + 1)) // cols
        cell_h = (canvas_h - header_h - pad * (rows + 1)) // rows

        canvas = np.full((canvas_h, canvas_w, 3), 24, dtype=np.uint8)
        self.current_canvas_size = (canvas_w, canvas_h)

        rotation_indicator = f" | Rotation: {self.current_rotation_steps * 90}°" if self.current_rotation_steps else ""
        title = f"Group {self.current_group_idx + 1}/{self.total_groups} | {n} photos | Window={self.window_seconds}s{rotation_indicator}"
        subtitle = "Click/1-9-0 toggle | D delete | K keep (others->delete) | R rotate | N/Space next | U undo | A all | C clear | Q quit"
        cv2.putText(canvas, title, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (220, 220, 220), 2)
        cv2.putText(canvas, subtitle, (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (190, 190, 190), 1)

        self.current_layout = {
            "cols": cols,
            "rows": rows,
            "cell_w": cell_w,
            "cell_h": cell_h,
            "pad": pad,
            "header_h": header_h,
        }

        # Thumbnail slot: full cell width minus a small inset for the border,
        # and cell height minus the label area and a small top pad.
        thumb_max_w = cell_w - 8
        thumb_max_h = cell_h - label_reserve - 6

        for idx, item in enumerate(group):
            r = idx // cols
            c = idx % cols

            x = pad + c * (cell_w + pad)
            y = header_h + pad + r * (cell_h + pad)

            thumb = self.load_thumbnail(item.path, thumb_max_w, thumb_max_h, self.current_rotation_steps)
            th, tw = thumb.shape[:2]
            x_img = x + (cell_w - tw) // 2
            y_img = y + 6

            # Safely place thumbnail, clipping if necessary to prevent overflow
            y_end = min(y_img + th, canvas.shape[0])
            x_end = min(x_img + tw, canvas.shape[1])
            th_actual = y_end - y_img
            tw_actual = x_end - x_img

            if y_img >= 0 and x_img >= 0 and th_actual > 0 and tw_actual > 0:
                canvas[y_img:y_end, x_img:x_end] = thumb[:th_actual, :tw_actual]

            selected_color = (50, 200, 80) if idx in selected else (110, 110, 110)
            border_thickness = 4 if idx in selected else 2
            cv2.rectangle(canvas, (x, y), (x + cell_w, y + cell_h), selected_color, border_thickness)

            live_indicator = " [L]" if item.linked_paths else ""
            name_chars = max(20, cell_w // 14)  # scale label length with cell width
            label = f"{idx + 1}: {item.path.name[:name_chars]}{live_indicator}"
            cv2.putText(canvas, label, (x + 6, y + cell_h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (230, 230, 230), 1)
            cv2.putText(canvas, item.exif_text, (x + 6, y + cell_h - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (170, 170, 170), 1)

            if idx in selected:
                cv2.putText(canvas, "SELECTED", (x + 8, y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (50, 220, 90), 2)

        return canvas

    def map_window_click_to_canvas(self, x: int, y: int) -> tuple[int, int]:
        """Map window click coordinates to canvas coordinates, accounting for scaling/letterboxing."""
        canvas_w, canvas_h = self.current_canvas_size
        if canvas_w <= 0 or canvas_h <= 0:
            return x, y

        get_rect = getattr(cv2, "getWindowImageRect", None)
        if get_rect is None:
            return x, y

        try:
            _wx, _wy, win_w, win_h = get_rect(self.window_name)
            if win_w <= 0 or win_h <= 0:
                return x, y

            scale = min(win_w / canvas_w, win_h / canvas_h)
            if scale <= 0:
                return x, y

            disp_w = canvas_w * scale
            disp_h = canvas_h * scale
            x_off = (win_w - disp_w) / 2.0
            y_off = (win_h - disp_h) / 2.0

            canvas_x = int((x - x_off) / scale)
            canvas_y = int((y - y_off) / scale)
            return canvas_x, canvas_y
        except Exception:
            return x, y

    def index_from_click(self, x: int, y: int, count: int) -> Optional[int]:
        if not self.current_layout:
            return None

        cols = self.current_layout["cols"]
        cell_w = self.current_layout["cell_w"]
        cell_h = self.current_layout["cell_h"]
        pad = self.current_layout["pad"]
        header_h = self.current_layout["header_h"]

        if y < header_h + pad:
            return None

        y_rel = y - (header_h + pad)
        x_rel = x - pad
        if x_rel < 0 or y_rel < 0:
            return None

        col = x_rel // (cell_w + pad)
        row = y_rel // (cell_h + pad)
        if col >= cols:
            return None

        x_in_cell = x_rel % (cell_w + pad)
        y_in_cell = y_rel % (cell_h + pad)
        if x_in_cell >= cell_w or y_in_cell >= cell_h:
            return None

        idx = row * cols + col
        if idx < 0 or idx >= count:
            return None

        return idx

    def toggle_selection(self, idx: int) -> None:
        if idx in self.current_selected:
            self.current_selected.remove(idx)
        else:
            self.current_selected.add(idx)
        self.needs_redraw = True

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event not in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONUP):
            return

        mapped_x, mapped_y = self.map_window_click_to_canvas(x, y)
        idx = self.index_from_click(mapped_x, mapped_y, len(self.current_items))
        if idx is not None:
            self.toggle_selection(idx)

    def selected_paths(self) -> List[Path]:
        paths: List[Path] = []
        for idx in sorted(self.current_selected):
            if 0 <= idx < len(self.current_items):
                paths.append(self.current_items[idx].path)
        return paths

    def _orientation_after_cw_rotation(self, current_orientation: int, rotation_steps: int) -> int:
        """Compute EXIF orientation after clockwise 90-degree rotations."""
        steps = rotation_steps % 4
        orientation = current_orientation if current_orientation in self.ORIENTATION_CW_MAP else 1
        for _ in range(steps):
            orientation = self.ORIENTATION_CW_MAP.get(orientation, 1)
        return orientation

    def rotate_and_save_image(self, source_path: Path, rotation_steps: int) -> bool:
        """Apply rotation losslessly by updating EXIF orientation metadata only."""
        steps = rotation_steps % 4
        if steps == 0:
            return True

        if not self.exiftool_path:
            print("Error: exiftool not found; cannot apply lossless rotation without re-encoding")
            return False

        try:
            current_orientation = 1
            with Image.open(source_path) as image:
                exif = image.getexif()
                current_orientation = int(exif.get(274, 1))

            new_orientation = self._orientation_after_cw_rotation(current_orientation, steps)
            cmd = [
                self.exiftool_path,
                "-overwrite_original",
                "-n",
                f"-Orientation={new_orientation}",
                str(source_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip() or "unknown error"
                print(f"Error applying lossless rotation to {source_path}: {err}")
                return False

            return True
        except Exception as e:
            print(f"Error rotating image {source_path}: {e}")
            return False

    def undo_last_decision(self) -> Optional[int]:
        if not self.decision_history:
            return None

        decision = self.decision_history.pop()
        if decision["type"] in ("delete", "keep"):
            for path in decision["paths"]:
                self.pending_deletes.discard(path)

        return int(decision["group_idx"])

    def finalize_deletes(self) -> int:
        deleted_count = 0
        for path in sorted(self.pending_deletes):
            try:
                if path.exists():
                    path.unlink()
                    deleted_count += 1
            except Exception as e:
                print(f"Error deleting {path}: {e}")
        return deleted_count

    def print_summary(self, groups_reviewed: int, deleted_count: int) -> None:
        print("\n" + "=" * 72)
        print("SUMMARY")
        print("=" * 72)
        print(f"Images scanned:                 {self.total_scanned:,}")
        print(f"Images with EXIF datetime:      {self.with_exif:,}")
        print(f"Excluded (missing EXIF date):   {self.excluded_no_exif:,}")
        print(f"Candidate groups (>=2 photos):  {self.total_groups:,}")
        print(f"Groups reviewed this run:       {groups_reviewed:,}")
        print(f"Files deleted on finalize:      {deleted_count:,}")
        print("Note: files not explicitly deleted were left in place.")
        print("=" * 72)

    def run(self) -> None:
        if not self.photos_dir.exists():
            print(f"Error: Directory not found: {self.photos_dir}")
            return

        items = self.scan_items()
        groups = self.build_groups(items)

        if self.shuffle:
            random.shuffle(groups)

        print(f"Scanned images: {self.total_scanned:,}")
        print(f"Included (with EXIF date): {self.with_exif:,}")
        print(f"Excluded (no EXIF date): {self.excluded_no_exif:,}")
        print(f"Found {len(groups):,} groups within {self.window_seconds}s window")

        if not groups:
            print("No candidate groups found.")
            return

        try:
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        except cv2.error as e:
            print("Error: Could not initialize OpenCV window.")
            print(f"OpenCV error: {e}")
            return

        cv2.setMouseCallback(self.window_name, self.on_mouse)

        groups_reviewed = 0
        idx = 0

        while idx < len(groups):
            self.current_group_idx = idx
            self.current_items = groups[idx]
            self.current_selected = set()
            self.current_rotation_steps = 0
            self.needs_redraw = True

            while True:
                if self.needs_redraw:
                    grid = self.build_grid_image(self.current_items, self.current_selected)
                    cv2.imshow(self.window_name, grid)
                    self.needs_redraw = False

                key = cv2.waitKey(50) & 0xFF
                if key == 255:
                    continue

                if key in (ord("q"), ord("Q"), 27):
                    deleted_count = self.finalize_deletes()
                    cv2.destroyAllWindows()
                    self.print_summary(groups_reviewed, deleted_count)
                    return

                if key in (ord("a"), ord("A")):
                    self.current_selected = set(range(len(self.current_items)))
                    self.needs_redraw = True
                    continue

                if key in (ord("c"), ord("C")):
                    self.current_selected.clear()
                    self.needs_redraw = True
                    continue

                if key in (ord("r"), ord("R")):
                    self.current_rotation_steps = (self.current_rotation_steps + 1) % 4
                    self.needs_redraw = True
                    print(f"Rotation: {self.current_rotation_steps * 90}°")
                    continue

                if key in (ord("u"), ord("U")):
                    undo_idx = self.undo_last_decision()
                    if undo_idx is None:
                        print("No decisions to undo")
                        continue

                    idx = undo_idx
                    print(f"Undid last decision; returning to group {idx + 1}")
                    break

                if key in (ord("n"), ord("N"), ord(" ")):
                    self.decision_history.append({"type": "next", "group_idx": idx, "paths": []})
                    groups_reviewed += 1
                    idx += 1
                    break

                if key in (ord("k"), ord("K")):
                    kept = self.selected_paths()
                    to_delete: List[Path] = []

                    # If user selected items to keep, auto-mark all non-selected items in this group for deletion.
                    # Also include any Live Photo companion videos for deleted items.
                    if kept:
                        keep_set = set(kept)
                        for grp_item in self.current_items:
                            if grp_item.path not in keep_set:
                                to_delete.append(grp_item.path)
                                to_delete.extend(grp_item.linked_paths)
                        for path in to_delete:
                            self.pending_deletes.add(path)
                    
                    # Save rotated versions if rotation is applied
                    if self.current_rotation_steps > 0:
                        success_count = 0
                        for path in kept:
                            if self.rotate_and_save_image(path, self.current_rotation_steps):
                                success_count += 1
                        print(f"Keep selected: {len(kept)} file(s); {success_count} saved with {self.current_rotation_steps * 90}° rotation")
                    else:
                        print(f"Keep selected: {len(kept)} file(s); no files moved or deleted")

                    if kept:
                        print(f"Auto-marked for deletion on quit: {len(to_delete)} non-selected file(s)")
                    
                    self.decision_history.append(
                        {
                            "type": "keep",
                            "group_idx": idx,
                            "paths": to_delete,
                            "kept_paths": kept,
                            "rotation": self.current_rotation_steps,
                        }
                    )
                    groups_reviewed += 1
                    idx += 1
                    break

                if key in (ord("d"), ord("D")):
                    if not self.current_selected:
                        print("No photos selected for deletion")
                        continue
                    to_delete = []
                    for sel_idx in sorted(self.current_selected):
                        if 0 <= sel_idx < len(self.current_items):
                            sel_item = self.current_items[sel_idx]
                            to_delete.append(sel_item.path)
                            to_delete.extend(sel_item.linked_paths)

                    for path in to_delete:
                        self.pending_deletes.add(path)
                    self.decision_history.append({"type": "delete", "group_idx": idx, "paths": to_delete})
                    live_count = sum(1 for p in to_delete if p.suffix.lower() in self.LIVE_PHOTO_VIDEO_EXTENSIONS)
                    print(f"Marked for deletion on quit: {len(to_delete) - live_count} photo(s)" + (f" + {live_count} Live Photo video(s)" if live_count else ""))
                    groups_reviewed += 1
                    idx += 1
                    break

                if ord("0") <= key <= ord("9"):
                    digit = key - ord("0")
                    map_idx = 9 if digit == 0 else digit - 1
                    if map_idx < len(self.current_items):
                        self.toggle_selection(map_idx)

        deleted_count = self.finalize_deletes()
        cv2.destroyAllWindows()
        self.print_summary(groups_reviewed, deleted_count)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Review groups of photos captured close together by EXIF time and optionally delete selected photos."
        )
    )
    parser.add_argument(
        "--photos-dir",
        default="photos",
        help="Root directory to scan recursively (default: photos)",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=10,
        help="Max EXIF time gap in seconds to keep photos in the same group (default: 10)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Number of concurrent workers for EXIF scanning (default: 10)",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Randomize the order of groups (default: chronological by first photo in group)",
    )

    args = parser.parse_args()

    reviewer = ExifBurstReviewer(
        photos_dir=args.photos_dir,
        window_seconds=args.window_seconds,
        workers=args.workers,
        shuffle=args.shuffle,
    )
    reviewer.run()


if __name__ == "__main__":
    main()
