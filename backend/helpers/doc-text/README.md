# doc-text

macOS 14+，只读给定文件，不联网，不使用屏幕录制、辅助功能或 EventKit。

```sh
printf '{"path":"/absolute/path/document.pdf"}' | doc-text pdf
printf '{"path":"/absolute/path/image.heic"}' | doc-text image
```

PDF 返回 `{ "pages": [{ "page": 1, "text": "…", "ocr": false }] }`；页中文字少于 20 个字符时渲染该页并用 Vision 识别（zh-Hans/en-US）。图片返回 `{ "text": "…" }`。失败返回固定 error 并退出 1。Python 端负责 sha256 缓存、文字上限和失败状态。docx 由 Python 调用系统 textutil，不经过此工具。

本次未在沙盒编译。安装脚本把它作为第三个 helper 安装，`--no-helper` 同时跳过。
