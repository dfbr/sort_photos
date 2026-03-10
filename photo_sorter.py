#!/usr/bin/env python3
"""
Photo & Video Sorting Application
Displays photos and videos one by one and allows user to:
- Press 'd' to delete
- Press 'k' to keep (move to keep directory)
- Press spacebar to skip
Videos autoplay muted in a loop until you make a choice.
"""

import os
import sys
import shutil
import subprocess
from datetime import datetime
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np

try:
    from PIL import Image, ExifTags
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORT = True
except ImportError:
    from PIL import Image, ExifTags
    HEIC_SUPPORT = False
    print("Warning: HEIC support not available. Install pillow-heif for HEIC support.")


class PhotoSorter:
    """Interactive photo and video sorting application."""
    
    # Supported image extensions
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.bmp', '.gif', '.tiff', '.tif'}
    
    # Supported video extensions
    VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mpg', '.avi', '.m4v', '.mkv', '.wmv'}
    ORIENTATION_CW_MAP = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}
    
    def __init__(self, photos_dir: str, keep_dir: str = "keep"):
        """
        Initialize the photo sorter.
        
        Args:
            photos_dir: Directory containing photos to sort
            keep_dir: Directory where kept photos will be moved
        """
        self.photos_dir = Path(photos_dir)
        self.keep_dir = Path(keep_dir)
        self.trash_dir = Path(".photo_sorter_trash")
        
        # Create keep directory if it doesn't exist
        self.keep_dir.mkdir(exist_ok=True)
        self.trash_dir.mkdir(exist_ok=True)
        
        # Decision history for multi-level undo (all actions until quit)
        self.decision_history: List[Dict[str, Any]] = []
        
        # Statistics
        self.total_photos = 0
        self.processed = 0
        self.deleted = 0
        self.kept = 0
        self.skipped = 0

        # Autoplay is enabled by default. Set PHOTO_SORTER_AUTOPLAY_VIDEOS=0 to force static preview mode.
        self.autoplay_videos = os.environ.get("PHOTO_SORTER_AUTOPLAY_VIDEOS", "1") == "1"
        self.exiftool_path = shutil.which("exiftool")
        if not self.exiftool_path:
            print("Warning: exiftool not found. Lossless image rotation is disabled to prevent quality loss.")
        
    def find_all_photos(self) -> List[Path]:
        """
        Recursively find all photo and video files in the photos directory.
        Excludes the keep directory.
        
        Returns:
            List of Path objects for all photos and videos
        """
        media_files = []
        keep_dir_abs = self.keep_dir.absolute()
        trash_dir_abs = self.trash_dir.absolute()
        
        print("Scanning for photos and videos...")
        for root, dirs, files in os.walk(self.photos_dir):
            root_path = Path(root).absolute()
            
            # Skip the keep and trash directories
            if (
                root_path == keep_dir_abs
                or keep_dir_abs in root_path.parents
                or root_path == trash_dir_abs
                or trash_dir_abs in root_path.parents
            ):
                continue
                
            for file in files:
                file_path = Path(root) / file
                ext_lower = file_path.suffix.lower()
                if ext_lower in self.IMAGE_EXTENSIONS or ext_lower in self.VIDEO_EXTENSIONS:
                    media_files.append(file_path)
        
        return sorted(media_files)

    def build_media_queue(self, media_files: List[Path]) -> List[Dict[str, Any]]:
        """
        Build review queue, pairing Apple Live Photos (image + video with same stem in same folder).

        Returns:
            List of queue entries where each entry is a single media file or a live-photo pair.
        """
        by_group: Dict[Tuple[Path, str], List[Path]] = {}
        for media_path in media_files:
            key = (media_path.parent, media_path.stem)
            by_group.setdefault(key, []).append(media_path)

        paired_paths = set()
        queue_entries: List[Dict[str, Any]] = []

        # Create one queue item per live photo pair
        for _, group_paths in by_group.items():
            images = sorted([path for path in group_paths if path.suffix.lower() in self.IMAGE_EXTENSIONS])
            videos = sorted([path for path in group_paths if path.suffix.lower() in self.VIDEO_EXTENSIONS])

            if images and videos:
                display_path = images[0]
                all_paths = [display_path] + [path for path in group_paths if path != display_path]
                linked_paths = [path for path in all_paths if path != display_path]
                queue_entries.append(
                    {
                        "display_path": display_path,
                        "is_video": False,
                        "is_live_photo": True,
                        "all_paths": all_paths,
                        "linked_paths": linked_paths,
                    }
                )
                paired_paths.update(all_paths)

        # Add remaining standalone media files
        for media_path in media_files:
            if media_path in paired_paths:
                continue

            queue_entries.append(
                {
                    "display_path": media_path,
                    "is_video": self.is_video(media_path),
                    "is_live_photo": False,
                    "all_paths": [media_path],
                    "linked_paths": [],
                }
            )

        return sorted(queue_entries, key=lambda item: item["display_path"])
    
    def is_video(self, file_path: Path) -> bool:
        """Check if file is a video."""
        return file_path.suffix.lower() in self.VIDEO_EXTENSIONS
    
    def load_image(self, image_path: Path) -> Optional[np.ndarray]:
        """
        Load an image file and convert to OpenCV format.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Numpy array in BGR format for OpenCV, or None if loading fails
        """
        try:
            # Use PIL to load the image (handles HEIC and other formats)
            pil_image = Image.open(image_path)
            
            # Convert to RGB if needed
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            
            # Convert to numpy array and then to BGR for OpenCV
            img_array = np.array(pil_image)
            img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            
            return img_bgr
        except Exception as e:
            print(f"Error loading {image_path}: {e}")
            return None

    def load_video_preview_frame(self, video_path: Path) -> Optional[np.ndarray]:
        """Load the first decodable frame from a video for static preview mode."""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            print(f"Error: Could not open video {video_path}")
            return None

        try:
            ret, frame = cap.read()
            if not ret or frame is None:
                print(f"Error: Could not decode preview frame for {video_path}")
                return None
            return frame
        finally:
            cap.release()

    def review_still_media(
        self,
        image: np.ndarray,
        info_text: str,
        metadata_date: str,
        window_name: str,
        is_video: bool,
    ) -> tuple[int, int]:
        """Display a still frame/image and wait for a decision key."""
        rotation_steps = 0

        while True:
            rotated_image = self.apply_rotation_to_frame(image, rotation_steps)
            display_image = self.resize_for_display(rotated_image)
            display_image = self.add_info_overlay(
                display_image,
                info_text,
                metadata_date,
                is_video=is_video,
            )

            cv2.imshow(window_name, display_image)

            key = cv2.waitKey(0) & 0xFF
            if key == ord('r') or key == ord('R'):
                rotation_steps = (rotation_steps + 1) % 4
                continue
            return key, rotation_steps

    def _format_datetime_string(self, value: str) -> Optional[str]:
        """Normalize common EXIF date formats into a readable value."""
        if not value:
            return None

        value = str(value).strip()
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.strftime("%d/%m/%Y %H:%M:%S")
            except ValueError:
                continue

        return value

    def _get_file_date(self, media_path: Path) -> str:
        """Get filesystem date as fallback metadata date."""
        try:
            stat = media_path.stat()
            ts = getattr(stat, "st_birthtime", stat.st_mtime)
            return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return "Unknown"

    def get_media_date(self, media_path: Path, is_video: bool) -> str:
        """Get best available metadata date for a media file."""
        if is_video:
            return self._get_file_date(media_path)

        try:
            with Image.open(media_path) as image:
                exif_data = image.getexif()
                if exif_data:
                    preferred_tags = ["DateTimeOriginal", "DateTimeDigitized", "DateTime"]
                    for tag_name in preferred_tags:
                        for tag_id, tag_value in exif_data.items():
                            if ExifTags.TAGS.get(tag_id) == tag_name:
                                normalized = self._format_datetime_string(str(tag_value))
                                if normalized:
                                    return normalized
        except Exception:
            pass

        return self._get_file_date(media_path)
    
    def resize_for_display(self, image: np.ndarray, max_width: int = 1920, max_height: int = 1080) -> np.ndarray:
        """
        Resize image to fit within display dimensions while maintaining aspect ratio.
        
        Args:
            image: Input image
            max_width: Maximum width
            max_height: Maximum height
            
        Returns:
            Resized image
        """
        height, width = image.shape[:2]
        
        # Calculate scaling factor
        scale_w = max_width / width
        scale_h = max_height / height
        scale = min(scale_w, scale_h, 1.0)  # Don't upscale
        
        if scale < 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
            return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
        
        return image

    def apply_rotation_to_frame(self, frame: np.ndarray, rotation_steps: int) -> np.ndarray:
        """Apply clockwise 90-degree rotation steps to an image/video frame."""
        steps = rotation_steps % 4
        if steps == 0:
            return frame
        if steps == 1:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if steps == 2:
            return cv2.rotate(frame, cv2.ROTATE_180)
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    def add_info_overlay(
        self,
        image: np.ndarray,
        info_text: str,
        metadata_date: str,
        is_video: bool = False,
    ) -> np.ndarray:
        """
        Add information overlay to the image.
        
        Args:
            image: Input image
            info_text: Text to display
            metadata_date: Metadata/EXIF date text to display
            is_video: Whether this is a video frame
            
        Returns:
            Image with overlay
        """
        img_with_overlay = image.copy()
        height, width = img_with_overlay.shape[:2]
        
        # Create semi-transparent overlay at the top
        overlay = img_with_overlay.copy()
        cv2.rectangle(overlay, (0, 0), (width, 110), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, img_with_overlay, 0.4, 0, img_with_overlay)
        
        # Add text
        font = cv2.FONT_HERSHEY_SIMPLEX
        video_indicator = " [VIDEO - PLAYING]" if is_video else ""
        cv2.putText(img_with_overlay, info_text + video_indicator, (10, 25), font, 0.7, (255, 255, 255), 2)
        cv2.putText(img_with_overlay, f"Date: {metadata_date}", (10, 55), font, 0.6, (220, 220, 220), 1)
        cv2.putText(img_with_overlay, "Press: [D]elete | [K]eep | [Space]Skip | [R]otate | [U]ndo | [Q]uit", 
                   (10, 85), font, 0.6, (200, 200, 200), 1)
        
        return img_with_overlay
    
    def delete_photo(self, photo_path: Path) -> bool:
        """Delete a photo file."""
        try:
            photo_path.unlink()
            self.deleted += 1
            return True
        except Exception as e:
            print(f"Error deleting {photo_path}: {e}")
            return False

    def move_to_keep(self, media_path: Path, rotation_steps: int = 0, is_video: bool = False) -> Optional[Path]:
        """Move media to keep directory with rotation, return the destination path."""
        try:
            rel_path = media_path.relative_to(self.photos_dir)
            dest_path = self.keep_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            steps = rotation_steps % 4
            if steps == 0:
                shutil.move(str(media_path), str(dest_path))
            else:
                if is_video:
                    if not self.rotate_and_save_video(media_path, dest_path, steps):
                        return None
                else:
                    if not self.rotate_and_save_image(media_path, dest_path, steps):
                        return None
            return dest_path
        except Exception as e:
            print(f"Error moving {media_path} to keep: {e}")
            return None
    
    def restore_from_keep(self, keep_path: Path, original_path: Path) -> bool:
        """Restore a file from keep directory back to original location."""
        try:
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(keep_path), str(original_path))
            return True
        except Exception as e:
            print(f"Error restoring {original_path} from keep: {e}")
            return False

    def _orientation_after_cw_rotation(self, current_orientation: int, rotation_steps: int) -> int:
        """Compute EXIF orientation after clockwise 90-degree rotations."""
        steps = rotation_steps % 4
        orientation = current_orientation if current_orientation in self.ORIENTATION_CW_MAP else 1
        for _ in range(steps):
            orientation = self.ORIENTATION_CW_MAP.get(orientation, 1)
        return orientation

    def rotate_and_save_image(self, source_path: Path, dest_path: Path, rotation_steps: int) -> bool:
        """Keep image quality by moving file and updating EXIF orientation metadata only."""
        steps = rotation_steps % 4
        if steps == 0:
            try:
                shutil.move(str(source_path), str(dest_path))
                return True
            except Exception as e:
                print(f"Error moving image {source_path}: {e}")
                return False

        if not self.exiftool_path:
            print("Error: exiftool not found; cannot apply lossless image rotation without re-encoding")
            return False

        try:
            current_orientation = 1
            with Image.open(source_path) as image:
                exif = image.getexif()
                current_orientation = int(exif.get(274, 1))

            new_orientation = self._orientation_after_cw_rotation(current_orientation, steps)
            shutil.move(str(source_path), str(dest_path))

            cmd = [
                self.exiftool_path,
                "-overwrite_original",
                "-n",
                f"-Orientation={new_orientation}",
                str(dest_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip() or "unknown error"
                print(f"Error applying lossless rotation to {dest_path}: {err}")
                # Restore original location on failure.
                try:
                    shutil.move(str(dest_path), str(source_path))
                except Exception:
                    pass
                return False

            return True
        except Exception as e:
            print(f"Error rotating image {source_path}: {e}")
            if dest_path.exists() and not source_path.exists():
                try:
                    shutil.move(str(dest_path), str(source_path))
                except Exception:
                    pass
            return False

    def rotate_and_save_video(self, source_path: Path, dest_path: Path, rotation_steps: int) -> bool:
        """Rotate a video and save it to destination by re-encoding."""
        cap = cv2.VideoCapture(str(source_path))
        if not cap.isOpened():
            print(f"Error: Could not open video for rotation {source_path}")
            return False

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 30.0

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            steps = rotation_steps % 4
            out_width, out_height = (height, width) if steps in (1, 3) else (width, height)

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=dest_path.suffix)
            temp_path = Path(temp_file.name)
            temp_file.close()

            fourcc_func = getattr(cv2, "VideoWriter_fourcc", None)
            if fourcc_func is None:
                fourcc_func = getattr(cv2.VideoWriter, "fourcc", None)
            if fourcc_func is None:
                print("Error: OpenCV fourcc function is not available")
                if temp_path.exists():
                    temp_path.unlink()
                return False

            writer = None
            for codec in ("mp4v", "avc1", "MJPG", "XVID"):
                fourcc = fourcc_func(*codec)
                writer_candidate = cv2.VideoWriter(str(temp_path), fourcc, fps, (out_width, out_height))
                if writer_candidate.isOpened():
                    writer = writer_candidate
                    break
                writer_candidate.release()

            if writer is None:
                print(f"Error: Could not create video writer for {dest_path}")
                if temp_path.exists():
                    temp_path.unlink()
                return False

            wrote_frame = False
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                rotated_frame = self.apply_rotation_to_frame(frame, steps)
                writer.write(rotated_frame)
                wrote_frame = True

            writer.release()
            cap.release()

            if not wrote_frame:
                print(f"Error: No frames written for video {source_path}")
                if temp_path.exists():
                    temp_path.unlink()
                return False

            shutil.move(str(temp_path), str(dest_path))
            source_path.unlink()
            return True
        except Exception as e:
            print(f"Error rotating video {source_path}: {e}")
            return False
        finally:
            cap.release()
    
    def record_delete_decision(self, media_entry: Dict[str, Any], idx: int) -> bool:
        """Record a delete decision for later execution on quit."""
        decision = {
            "type": "delete",
            "idx": idx,
            "media_entry": media_entry,
            "all_paths": media_entry["all_paths"].copy(),
        }
        self.decision_history.append(decision)
        self.deleted += len(media_entry["all_paths"])
        self.processed += 1
        return True
    
    def record_keep_decision(self, media_entry: Dict[str, Any], idx: int, rotation_steps: int) -> bool:
        """Record and execute a keep decision (immediate move to keep directory)."""
        keep_records: List[Tuple[Path, Path]] = []  # (original, destination)
        display_path = media_entry["display_path"]
        display_is_video = media_entry["is_video"]
        
        # Move displayed file with rotation
        dest = self.move_to_keep(display_path, rotation_steps=rotation_steps, is_video=display_is_video)
        if dest is None:
            return False
        keep_records.append((display_path, dest))
        
        # Move linked files without rotation
        for linked_path in media_entry["linked_paths"]:
            dest = self.move_to_keep(linked_path, rotation_steps=0, is_video=self.is_video(linked_path))
            if dest is None:
                # Rollback on failure
                for orig, kept in reversed(keep_records):
                    self.restore_from_keep(kept, orig)
                return False
            keep_records.append((linked_path, dest))
        
        decision = {
            "type": "keep",
            "idx": idx,
            "media_entry": media_entry,
            "keep_records": keep_records,
        }
        self.decision_history.append(decision)
        self.kept += len(media_entry["all_paths"])
        self.processed += 1
        return True
    
    def record_skip_decision(self, media_entry: Dict[str, Any], idx: int) -> bool:
        """Record a skip decision."""
        decision = {
            "type": "skip",
            "idx": idx,
            "media_entry": media_entry,
        }
        self.decision_history.append(decision)
        self.skipped += 1
        self.processed += 1
        return True
    
    def undo_last_decision(self) -> Optional[int]:
        """Undo the most recent decision and return the queue index to revisit."""
        if not self.decision_history:
            return None
        
        decision = self.decision_history.pop()
        decision_type = decision["type"]
        idx = decision["idx"]
        
        if decision_type == "delete":
            # Delete was deferred, just adjust counters
            self.deleted -= len(decision["all_paths"])
            self.processed -= 1
        
        elif decision_type == "keep":
            # Restore files from keep directory
            keep_records = decision["keep_records"]
            for original_path, keep_path in reversed(keep_records):
                if not self.restore_from_keep(keep_path, original_path):
                    print(f"Warning: could not restore {original_path} from keep")
            self.kept -= len(keep_records)
            self.processed -= 1
        
        elif decision_type == "skip":
            self.skipped -= 1
            self.processed -= 1
        
        return idx

    def finalize_all_actions(self) -> None:
        """Execute all deferred delete actions (called on quit)."""
        delete_count = 0
        for decision in self.decision_history:
            if decision["type"] == "delete":
                for media_path in decision["all_paths"]:
                    try:
                        if media_path.exists():
                            media_path.unlink()
                            delete_count += 1
                    except Exception as e:
                        print(f"Error permanently deleting {media_path}: {e}")
        
        # Clean up temp trash directory
        if self.trash_dir.exists():
            try:
                shutil.rmtree(self.trash_dir)
            except Exception as e:
                print(f"Error cleaning up temp trash: {e}")
        
        if delete_count > 0:
            print(f"\nFinalized {delete_count} file deletion(s)")
    
    def print_statistics(self):
        """Print current statistics."""
        print("\n" + "="*60)
        print(f"Statistics:")
        print(f"  Total photos:    {self.total_photos}")
        print(f"  Processed:       {self.processed}")
        print(f"  Kept:            {self.kept}")
        print(f"  Deleted:         {self.deleted}")
        print(f"  Skipped:         {self.skipped}")
        print(f"  Remaining:       {self.total_photos - self.processed}")
        print("="*60 + "\n")
    
    def play_video_and_get_action(
        self,
        video_path: Path,
        info_text: str,
        metadata_date: str,
        window_name: str,
    ) -> tuple[Optional[int], int]:
        """
        Play a video in a loop and wait for user action.
        
        Args:
            video_path: Path to the video file
            info_text: Text to display for current queue item
            metadata_date: Metadata date text for current queue item
            window_name: Name of the display window
            
        Returns:
            Tuple of (key code pressed by user, rotation steps), or (None, rotation_steps) if video couldn't be loaded
        """
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            print(f"Error: Could not open video {video_path}")
            return None, 0
        
        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30  # Default fallback
        frame_delay = int(max(10, min(1000 / fps, 100)))  # Keep UI responsive even with odd FPS metadata
        
        rotation_steps = 0
        failed_reads = 0
        last_good_frame: Optional[np.ndarray] = None
        max_failed_reads = 25
        
        while True:
            ret, frame = cap.read()
            
            if not ret:
                failed_reads += 1

                # First try to rewind to frame 0 for normal loop behavior.
                if failed_reads == 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

                # Pump events and allow quit keys while decoder is failing.
                key = cv2.waitKey(15) & 0xFF
                if key in [ord('d'), ord('D'), ord('k'), ord('K'), ord(' '), ord('u'), ord('U'), ord('q'), ord('Q'), 27]:
                    cap.release()
                    return key, rotation_steps

                if failed_reads >= max_failed_reads:
                    cap.release()
                    if last_good_frame is not None:
                        print(f"Warning: autoplay decode stalled for {video_path.name}; using static preview for this item.")
                        return self.review_still_media(
                            last_good_frame,
                            info_text + " [VIDEO - STATIC FALLBACK]",
                            metadata_date,
                            window_name,
                            is_video=True,
                        )
                    return None, rotation_steps
                continue

            failed_reads = 0
            last_good_frame = frame
            
            # Rotate then resize for display
            rotated_frame = self.apply_rotation_to_frame(frame, rotation_steps)
            display_frame = self.resize_for_display(rotated_frame)
            
            # Add info overlay
            display_frame = self.add_info_overlay(
                display_frame,
                info_text,
                metadata_date,
                is_video=True,
            )
            
            # Show frame
            cv2.imshow(window_name, display_frame)
            
            # Wait for keypress (short delay for video playback)
            key = cv2.waitKey(frame_delay) & 0xFF
            
            # Check for valid keypresses
            if key == ord('r') or key == ord('R'):
                rotation_steps = (rotation_steps + 1) % 4
                continue

            if key in [ord('d'), ord('D'), ord('k'), ord('K'), ord(' '), ord('u'), ord('U'), ord('q'), ord('Q'), 27]:
                cap.release()
                return key, rotation_steps
        
        cap.release()
        return None, rotation_steps
    
    def run(self):
        """Main application loop."""
        # Find all photos and videos
        media_files = self.find_all_photos()
        media_queue = self.build_media_queue(media_files)
        self.total_photos = len(media_queue)
        
        if self.total_photos == 0:
            print("No photos or videos found!")
            return
        
        print(f"Found {self.total_photos} review items to process.")
        print("\nStarting media review...")
        print("Controls:")
        print("  D - Mark for deletion (deferred until quit)")
        print("  K - Keep file (move to 'keep' directory immediately)")
        print("  Space - Skip (review later)")
        print("  R - Rotate 90° clockwise")
        print("  U - Undo last decision (multi-level)")
        print("  Q - Quit and finalize all deletions\n")
        print("Important: key presses are captured by the photo/video window, not this terminal.\n")
        if self.autoplay_videos:
            print("Video mode: autoplay enabled (set PHOTO_SORTER_AUTOPLAY_VIDEOS=0 to force static preview)\n")
        else:
            print("Video mode: static preview (set PHOTO_SORTER_AUTOPLAY_VIDEOS=1 for autoplay)\n")
        
        window_name = "Photo & Video Sorter"
        try:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        except cv2.error as e:
            print("Error: Could not initialize OpenCV display window.")
            print("This app needs GUI access. Try running it in a local desktop session.")
            print(f"OpenCV error: {e}")
            return
        
        try:
            idx = 0
            while idx < len(media_queue):
                media_entry = media_queue[idx]
                media_path = media_entry["display_path"]
                is_video_file = media_entry["is_video"]
                rotation_steps = 0

                live_suffix = " [LIVE PHOTO]" if media_entry["is_live_photo"] else ""
                info_text = f"Media {idx + 1}/{self.total_photos}: {media_path.name}{live_suffix}"
                metadata_date = self.get_media_date(media_path, is_video=is_video_file)
                
                if is_video_file:
                    if self.autoplay_videos:
                        # Handle video autoplay mode
                        key, rotation_steps = self.play_video_and_get_action(
                            media_path,
                            info_text,
                            metadata_date,
                            window_name,
                        )
                        if key is None:
                            print(f"Skipping {media_path} - could not load")
                            self.processed += 1
                            idx += 1
                            continue
                    else:
                        # Handle video as static preview frame (more stable on macOS)
                        image = self.load_video_preview_frame(media_path)
                        if image is None:
                            print(f"Skipping {media_path} - could not load")
                            self.processed += 1
                            idx += 1
                            continue
                        key, rotation_steps = self.review_still_media(
                            image,
                            info_text + " [VIDEO - STATIC PREVIEW]",
                            metadata_date,
                            window_name,
                            is_video=True,
                        )
                else:
                    # Handle image
                    image = self.load_image(media_path)
                    if image is None:
                        print(f"Skipping {media_path} - could not load")
                        self.processed += 1
                        idx += 1
                        continue
                    key, rotation_steps = self.review_still_media(
                        image,
                        info_text,
                        metadata_date,
                        window_name,
                        is_video=False,
                    )
                
                # Process the key press (same for both images and videos)
                if key == ord('d') or key == ord('D'):
                    # Delete (deferred until quit)
                    if self.record_delete_decision(media_entry, idx):
                        if media_entry["is_live_photo"]:
                            print(f"Marked for deletion: Live Photo {media_path} + {len(media_entry['linked_paths'])} linked file(s)")
                        else:
                            print(f"Marked for deletion: {media_path}")
                    idx += 1
                
                elif key == ord('k') or key == ord('K'):
                    # Keep (immediate move to keep directory)
                    if self.record_keep_decision(media_entry, idx, rotation_steps=rotation_steps):
                        if media_entry["is_live_photo"]:
                            print(f"Kept Live Photo: {media_path} + {len(media_entry['linked_paths'])} linked file(s)")
                        else:
                            print(f"Kept: {media_path}")
                    idx += 1
                
                elif key == ord(' '):  # Spacebar
                    # Skip
                    if self.record_skip_decision(media_entry, idx):
                        print(f"Skipped: {media_path}")
                    idx += 1
                
                elif key == ord('b') or key == ord('B'):  # Back (deprecated, use Undo instead)
                    print("Tip: Use 'U' to undo the last decision")

                elif key == ord('u') or key == ord('U'):  # Undo last decision
                    undo_idx = self.undo_last_decision()
                    if undo_idx is None:
                        print("No decisions to undo")
                    else:
                        print(f"Undid last decision, returning to item {undo_idx + 1}")
                        idx = undo_idx
                
                elif key == ord('q') or key == ord('Q') or key == 27:  # Q or ESC
                    # Quit and finalize all actions
                    print("\nQuitting and finalizing all actions...")
                    cv2.destroyAllWindows()
                    self.finalize_all_actions()
                    self.print_statistics()
                    return
                
                # Print progress every 10 files
                if (idx) % 10 == 0 and idx > 0:
                    print(f"Progress: {idx}/{self.total_photos} files reviewed")

        except KeyboardInterrupt:
            print("\nInterrupted by user. Finalizing pending actions...")
        
        finally:
            cv2.destroyAllWindows()
            print("\nCompleted all reviews!")
            self.finalize_all_actions()
            self.print_statistics()


def main():
    """Main entry point."""
    # Default paths
    photos_dir = "photos"
    keep_dir = "keep"
    
    # Check if photos directory exists
    if not Path(photos_dir).exists():
        print(f"Error: Photos directory '{photos_dir}' not found!")
        print(f"Current directory: {os.getcwd()}")
        sys.exit(1)
    
    # Create and run the sorter
    sorter = PhotoSorter(photos_dir, keep_dir)
    sorter.run()


if __name__ == "__main__":
    main()
