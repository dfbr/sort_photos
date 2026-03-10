# Photo & Video Sorter Application

An interactive Python application to help you review and sort through your photo and video collection.

## About

You have approximately 13,600 files in your photos directory:
- 8,030 JPG files
- 4,284 HEIC files
- 1,255 MOV files
- 22 PNG files
- Plus some additional video files (MPG, mp4)

This application displays photos and videos one at a time and lets you decide what to do with each one.

By default, videos autoplay muted in a loop.

If OpenCV video decoding stalls for a specific clip, the app automatically falls back to static preview for that item.
You can force static preview mode for all videos with `PHOTO_SORTER_AUTOPLAY_VIDEOS=0`.

## Usage

To run the application:

```bash
venv/bin/python photo_sorter.py
```

Or activate the virtual environment first:

```bash
source venv/bin/activate
python photo_sorter.py
```

To force static-preview mode (optional):

```bash
PHOTO_SORTER_AUTOPLAY_VIDEOS=0 python photo_sorter.py
```

## Controls

When viewing each photo or video, you have these options:

- **D** - Mark file for deletion (action deferred until quit)
- **K** - Keep the file (moves it to the `keep` directory immediately)
- **Spacebar** - Skip (leave it for later review)
- **R** - Rotate the current media 90° clockwise (can be pressed multiple times)
- **U** - Undo the last decision (multi-level - can undo delete, keep, or skip)
- **Q** or **ESC** - Quit and finalize all deletions

## Features

- Displays photos and videos in a window with information overlay
- **Apple Live Photo pairing**: image + video with the same basename in the same folder are treated as one review item
- **Videos autoplay muted** in a loop by default
- Automatic fallback to static preview for problematic video files
- Optional global static mode via `PHOTO_SORTER_AUTOPLAY_VIDEOS=0`
- Shows progress (current file number out of total)
- Automatically resizes large photos/videos to fit your screen
- Supports **images**: JPG, PNG, HEIC, BMP, GIF, TIFF
- Supports **videos**: MOV, MP4, MPG, AVI, M4V, MKV, WMV
- Preserves folder structure when moving files to "keep" directory
- Shows statistics when you quit (deleted, kept, skipped counts)
- The "keep" directory is excluded from scanning, so files you keep won't be shown again

## Notes

- **Deferred deletion**: Files marked with 'D' are NOT immediately deleted - they stay in place until you press 'Q' to quit
- **Multi-level undo**: Press 'U' to undo any decision (delete/keep/skip) and go back to that item. You can undo multiple times to walk back through your entire decision history
- **Keep is immediate**: Files marked with 'K' are moved to the `keep` directory right away, but undo will restore them
- Files marked for "keep" are moved (not copied) to the `keep` directory
- The relative folder structure is preserved (e.g., `photos/2024/01/photo.jpg` becomes `keep/2024/01/photo.jpg`)
- In default mode, videos autoplay muted
- If a video decoder stalls, that item falls back to static preview automatically
- You can globally force static preview mode with `PHOTO_SORTER_AUTOPLAY_VIDEOS=0`
- **Live Photo decisions are linked**: delete/keep/skip apply to the paired still+video together
- **Rotation persistence**: If you rotate and then press 'K', the saved file in `keep` is written in the rotated orientation
- You can quit at any time and resume later - the application will start from the beginning of remaining files
- Progress is printed to the console every 10 files

## Requirements

All required packages have been installed:
- opencv-python (for image display and keyboard handling)
- pillow (for image loading)
- pillow-heif (for HEIC/HEIF support)
- numpy (image processing)
