# photo-export

独立的 macOS 14+ PhotoKit 图片只读导出工具。用 `backend/scripts/install_mac.sh` 构建、嵌入 Info.plist 并签名安装到 `~/Library/Application Support/AemeathBot/bin/photo-export`；`--no-helper` 跳过。程序放弃父进程责任，让照片授权归于自身。不会执行 PhotoKit 写入请求。PhotoKit 没有只读授权级别，读取使用 `.readWrite`，详见 [入口说明](../../../docs/webdav.md)。

命令 `status`、`authorize` 无需 stdin，返回 `{"permission":"authorized|limited|denied|restricted|not_determined|unavailable"}`。仅 authorize 主动申请权限。

- `list` 从 stdin 读 `{"from":"2026-10-29T00:00:00+08:00","to":"2026-10-30T00:00:00+08:00"}`。区间左闭右开，只列图片，按 creationDate 排序，最多 2000 条；输出 `[{id,created_at,is_screenshot,width,height}]`。
- `export` 从 stdin 读 `{"id":"<PhotoKit localIdentifier>","dest":"/absolute/staging/original"}`。写出原图资源，不转码；dest 无扩展名时补上原始扩展名（HEIC 保留），已有文件拒绝覆盖。父目录必须存在。允许 iCloud 下载，原图分块写盘，不在内存中拼成完整 Data；120 秒取消并清理。返回 `{"path":".../original.heic","uti":"public.heic","bytes":123}`。

成功输出一行 JSON，失败退出码 1、固定 `{"error":"photo_export_failed"}`，不输出路径、照片或系统错误详情。list/export 权限不足直接失败，不申请权限。测试使用 Python `FakePhotoLibrary`，本次未编译 Swift；授权、iCloud 和实际导出仍需沙盒外验证。
