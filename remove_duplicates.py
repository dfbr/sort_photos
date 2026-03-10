#!/usr/bin/env python3
"""
Photo Duplicate Removal Script
Finds duplicate photos by hash and keeps only the oldest copy.
"""

import os
import sys
import hashlib
import numpy as np
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from PIL import Image
from pillow_heif import register_heif_opener

# Register HEIF/HEIC support
register_heif_opener()

# Supported media extensions (defined at module level for multiprocessing)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.bmp', '.gif', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mpg', '.avi', '.m4v', '.mkv', '.wmv'}


def calculate_image_content_hash(file_path: Path) -> Optional[str]:
    """
    Calculate SHA256 hash of image pixel data only (excludes EXIF metadata).
    Module-level function for multiprocessing compatibility.
    
    Args:
        file_path: Path to the image file
        
    Returns:
        Hex string of the content hash
    """
    try:
        # Load image and convert to RGB (standardize format)
        img = Image.open(file_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Hash the pixel data as a numpy array
        pixel_data = np.array(img)
        sha256_hash = hashlib.sha256()
        sha256_hash.update(pixel_data.tobytes())
        return sha256_hash.hexdigest()
    except Exception as e:
        return None


def calculate_file_hash_wrapper(args: Tuple[Path, bool]) -> Tuple[Path, Optional[str]]:
    """
    Wrapper function for multiprocessing to hash a single file.
    Module-level function for pickling compatibility.
    
    Args:
        args: Tuple of (file_path, content_only)
        
    Returns:
        Tuple of (file_path, hash_or_none)
    """
    file_path, content_only = args
    ext_lower = file_path.suffix.lower()
    
    # Check if this is an image and we're in content-only mode
    if content_only and ext_lower in IMAGE_EXTENSIONS:
        file_hash = calculate_image_content_hash(file_path)
    else:
        # Hash the entire file
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    sha256_hash.update(chunk)
            file_hash = sha256_hash.hexdigest()
        except Exception:
            file_hash = None
    
    return (file_path, file_hash)


class DuplicateRemover:
    """Find and remove duplicate photos by content hash."""
    
    def __init__(self, photos_dir: str, content_only: bool = False, workers: Optional[int] = None):
        """
        Initialize the duplicate remover.
        
        Args:
            photos_dir: Directory containing photos to deduplicate
            content_only: If True, hash only image pixel data (ignores EXIF metadata)
            workers: Number of parallel workers (default: CPU count)
        """
        self.photos_dir = Path(photos_dir)
        self.keep_dir = Path("keep")
        self.trash_dir = Path(".photo_sorter_trash")
        self.content_only = content_only
        self.workers = workers if workers else cpu_count()
        
        # Statistics
        self.total_files = 0
        self.duplicates_found = 0
        self.files_deleted = 0
        self.space_freed = 0
        self.scan_time = 0.0
        self.hash_time = 0.0
        self.total_time = 0.0
    
    def find_all_media(self) -> List[Path]:
        """
        Recursively find all media files in the photos directory.
        Excludes the keep and trash directories if they are subdirectories of the scan location.
        
        Returns:
            List of Path objects for all media files
        """
        start_time = time.time()
        media_files = []
        keep_dir_abs = self.keep_dir.absolute()
        trash_dir_abs = self.trash_dir.absolute()
        photos_dir_abs = self.photos_dir.absolute()
        
        # Only exclude keep/trash if the scan directory is their parent (i.e., they're subdirectories)
        should_exclude_keep = photos_dir_abs in keep_dir_abs.parents
        should_exclude_trash = photos_dir_abs in trash_dir_abs.parents
        
        print("Scanning for media files...")
        for root, dirs, files in os.walk(self.photos_dir):
            root_path = Path(root).absolute()
            
            # Skip the keep and trash directories only if they're subdirectories of our scan location
            skip_this_dir = False
            if should_exclude_keep and (root_path == keep_dir_abs or keep_dir_abs in root_path.parents):
                skip_this_dir = True
            if should_exclude_trash and (root_path == trash_dir_abs or trash_dir_abs in root_path.parents):
                skip_this_dir = True
                
            if skip_this_dir:
                continue
                
            for file in files:
                file_path = Path(root) / file
                ext_lower = file_path.suffix.lower()
                if ext_lower in IMAGE_EXTENSIONS or ext_lower in VIDEO_EXTENSIONS:
                    media_files.append(file_path)
        
        self.scan_time = time.time() - start_time
        return media_files
    
    def get_file_age(self, file_path: Path) -> float:
        """
        Get the creation time (or modification time as fallback) of a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Timestamp (earlier timestamp = older file)
        """
        try:
            stat = file_path.stat()
            # Use birth time (creation time) on macOS, fall back to mtime
            return getattr(stat, "st_birthtime", stat.st_mtime)
        except Exception:
            return float('inf')  # If error, treat as newest
    
    def build_hash_map(self, media_files: List[Path]) -> Dict[str, List[Tuple[Path, float]]]:
        """
        Build a map of file hashes to lists of (file_path, timestamp).
        Uses multiprocessing for parallel hashing.
        
        Args:
            media_files: List of media file paths
            
        Returns:
            Dictionary mapping hash -> [(file_path, timestamp), ...]
        """
        start_time = time.time()
        hash_map: Dict[str, List[Tuple[Path, float]]] = defaultdict(list)
        hashed_count = 0
        failed_count = 0
        total_files = len(media_files)
        
        print(f"Calculating hashes for {total_files:,} files using {self.workers} workers...")
        
        # Prepare arguments for parallel processing
        hash_args = [(file_path, self.content_only) for file_path in media_files]
        
        # Process files in parallel
        with Pool(processes=self.workers) as pool:
            # Use imap_unordered for better progress tracking
            for idx, (file_path, file_hash) in enumerate(pool.imap_unordered(calculate_file_hash_wrapper, hash_args, chunksize=10), 1):
                if file_hash:
                    timestamp = self.get_file_age(file_path)
                    hash_map[file_hash].append((file_path, timestamp))
                    hashed_count += 1
                else:
                    failed_count += 1
                
                # Update progress bar
                percent = (idx / total_files) * 100
                bar_length = 40
                filled_length = int(bar_length * idx // total_files)
                bar = '█' * filled_length + '░' * (bar_length - filled_length)
                print(f'\r  Progress: |{bar}| {percent:.1f}% ({idx:,}/{total_files:,})', end='', flush=True)
        
        print()  # New line after progress bar
        self.hash_time = time.time() - start_time
        
        print(f"\nHash calculation complete:")
        if self.content_only:
            print(f"  Mode: Content-only (pixel data, ignoring EXIF metadata)")
        else:
            print(f"  Mode: Full file hash (including metadata)")
        print(f"  Successfully hashed: {hashed_count:,} files")
        print(f"  Failed to hash: {failed_count:,} files")
        print(f"  Unique hashes: {len(hash_map):,}")
        print(f"  Potential duplicates: {sum(1 for files in hash_map.values() if len(files) > 1):,} groups")
        print(f"  Time elapsed: {self.hash_time:.1f}s")
        
        return hash_map
    
    def find_and_remove_duplicates(self, dry_run: bool = False) -> None:
        """
        Find duplicate files and remove the most recent copies.
        
        Args:
            dry_run: If True, only report duplicates without deleting
        """
        overall_start = time.time()
        
        # Find all media files
        media_files = self.find_all_media()
        self.total_files = len(media_files)
        
        if self.total_files == 0:
            print("No media files found!")
            return
        
        print(f"Found {self.total_files:,} media files in {self.scan_time:.1f}s\n")
        
        # Build hash map
        hash_map = self.build_hash_map(media_files)
        
        # Find and process duplicates
        print("\nSearching for duplicates...")
        duplicate_groups = {h: files for h, files in hash_map.items() if len(files) > 1}
        
        if not duplicate_groups:
            print("\nNo duplicates found!")
            return
        
        self.duplicates_found = len(duplicate_groups)
        print(f"\nFound {self.duplicates_found} unique files with duplicates.")
        
        # Process each duplicate group
        for file_hash, file_list in duplicate_groups.items():
            # Sort by timestamp (oldest first)
            sorted_files = sorted(file_list, key=lambda x: x[1])
            
            # Keep the oldest, mark others for deletion
            oldest_file, oldest_time = sorted_files[0]
            duplicates_to_delete = sorted_files[1:]
            
            print(f"\n  Duplicate group ({len(file_list)} copies):")
            print(f"    KEEPING (oldest): {oldest_file}")
            
            for dup_file, dup_time in duplicates_to_delete:
                file_size = dup_file.stat().st_size
                self.space_freed += file_size
                self.files_deleted += 1
                
                if dry_run:
                    print(f"    WOULD DELETE: {dup_file} ({file_size:,} bytes)")
                else:
                    try:
                        dup_file.unlink()
                        print(f"    DELETED: {dup_file} ({file_size:,} bytes)")
                    except Exception as e:
                        self.files_deleted -= 1  # Revert count on error
                        self.space_freed -= file_size
                        print(f"    ERROR deleting {dup_file}: {e}")
        
        self.total_time = time.time() - overall_start
        
        # Print summary
        self.print_summary(dry_run)
    
    def print_summary(self, dry_run: bool = False) -> None:
        """Print summary statistics."""
        print("\n" + "="*70)
        print("SUMMARY")
        print("="*70)
        print(f"Total media files scanned:     {self.total_files:,}")
        print(f"Unique files with duplicates:  {self.duplicates_found:,}")
        print(f"Total duplicate copies:        {self.files_deleted:,}")
        
        if dry_run:
            print(f"Would delete:                  {self.files_deleted:,} files")
            print(f"Would free up:                 {self.space_freed:,} bytes ({self.space_freed / (1024**2):.2f} MB)")
        else:
            print(f"Files deleted:                 {self.files_deleted:,}")
            print(f"Space freed:                   {self.space_freed:,} bytes ({self.space_freed / (1024**2):.2f} MB)")
        
        print(f"\nTiming:")
        print(f"  File scanning:                 {self.scan_time:.1f}s")
        print(f"  Hash calculation:              {self.hash_time:.1f}s")
        print(f"  Total time:                    {self.total_time:.1f}s")
        print("="*70)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Find and remove duplicate photos, keeping only the oldest copy of each."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting files"
    )
    parser.add_argument(
        "--photos-dir",
        default="photos",
        help="Directory containing photos (default: photos)"
    )
    parser.add_argument(
        "--content-only",
        action="store_true",
        help="Hash only image pixel data, ignoring EXIF metadata (videos still use full file hash)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Number of parallel workers for hashing (default: {cpu_count()} - your CPU count)"
    )
    
    args = parser.parse_args()
    
    # Check if photos directory exists
    photos_path = Path(args.photos_dir)
    if not photos_path.exists():
        print(f"Error: Photos directory '{args.photos_dir}' not found!")
        print(f"Current directory: {os.getcwd()}")
        sys.exit(1)
    
    # Run duplicate removal
    remover = DuplicateRemover(args.photos_dir, content_only=args.content_only, workers=args.workers)
    
    if args.dry_run:
        print("DRY RUN MODE: No files will be deleted\n")
    else:
        print("WARNING: This will permanently delete duplicate files!")
        response = input("Are you sure you want to continue? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            print("Cancelled.")
            sys.exit(0)
        print()
    
    if args.content_only:
        print("CONTENT-ONLY MODE: Hashing image pixel data only (ignoring EXIF metadata)\n")
    
    remover.find_and_remove_duplicates(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
