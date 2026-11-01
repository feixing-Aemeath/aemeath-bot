"""Screen reading shared by qq_screen and wechat_screen.

model     the two macOS seams: ScreenCapture and TextRecognizer, plus TextBlock/Screenshot
helper    their macOS implementation through a local JSON helper (Accessibility + Vision)
fake      Linux implementations replaying simulated OCR blocks
layout    one screenshot's blocks -> classified lines (time, notice, name, bubble lines)
chat      stitching overlapping screenshots, assembling messages, stable IDs
preview   the conversation list in preview-only mode
reader    the incremental per-chat reader with cursors
"""
