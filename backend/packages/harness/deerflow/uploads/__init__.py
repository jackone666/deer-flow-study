"""Uploads 子包：上传文件管理相关工具。

提供 ``get_uploads_dir`` / ``ensure_uploads_dir`` / ``normalize_filename`` /
``validate_path_traversal`` / ``delete_file_safe`` 等与上传目录、文件名安全、
路径穿越防护有关的纯业务函数；Gateway 与 Client 共享这一组实现。
"""

from .manager import (
    PathTraversalError,
    claim_unique_filename,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    normalize_filename,
    upload_artifact_url,
    upload_virtual_path,
    validate_path_traversal,
    validate_thread_id,
)

__all__ = [
    "get_uploads_dir",
    "ensure_uploads_dir",
    "normalize_filename",
    "PathTraversalError",
    "claim_unique_filename",
    "validate_path_traversal",
    "list_files_in_dir",
    "delete_file_safe",
    "upload_artifact_url",
    "upload_virtual_path",
    "enrich_file_listing",
    "validate_thread_id",
]
