"""共享的上传管理逻辑。

纯业务逻辑，不依赖 FastAPI/HTTP。Gateway 与 Client 都委托给本模块中的
函数，以便在两条链路之间复用同一套上传目录与文件安全规则。
"""

import errno
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id


class PathTraversalError(ValueError):
    """当路径逃逸出允许的基础目录时抛出。"""


class UnsafeUploadPathError(ValueError):
    """当上传目标不是一个安全的常规文件路径时抛出。"""


# thread_id must be alphanumeric, hyphens, underscores, or dots only.
_SAFE_THREAD_ID = re.compile(r"^[a-zA-Z0-9._-]+$")


def validate_thread_id(thread_id: str) -> None:
    """拒绝包含对文件系统不安全字符的 thread ID。

    Raises:
        ValueError: 当 ``thread_id`` 为空或含有不安全字符时。
    """
    if not thread_id or not _SAFE_THREAD_ID.match(thread_id):
        raise ValueError(f"Invalid thread_id: {thread_id!r}")


def get_uploads_dir(thread_id: str) -> Path:
    """返回指定 thread 的 uploads 目录路径（不会创建目录或产生副作用）。

    Args:
        thread_id: 当前 thread 的 ID。

    Returns:
        uploads 目录的 :class:`Path` 对象。

    Raises:
        ValueError: 当 ``thread_id`` 非法时。
    """
    validate_thread_id(thread_id)
    return get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())


def ensure_uploads_dir(thread_id: str) -> Path:
    """返回指定 thread 的 uploads 目录，必要时自动创建。

    Args:
        thread_id: 当前 thread 的 ID。

    Returns:
        uploads 目录的 :class:`Path` 对象（保证目录已存在）。

    Raises:
        ValueError: 当 ``thread_id`` 非法时。
        OSError: 当底层 ``mkdir`` 失败时。
    """
    base = get_uploads_dir(thread_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def normalize_filename(filename: str) -> str:
    """通过提取 basename 来清洗文件名。

    会剥离目录成分并拒绝穿越模式。

    Args:
        filename: 来自用户输入的原始文件名（可能含路径成分）。

    Returns:
        安全的文件名（仅 basename）。

    Raises:
        ValueError: 当 ``filename`` 为空或最终为穿越模式时。
    """
    if not filename:
        raise ValueError("Filename is empty")
    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise ValueError(f"Filename is unsafe: {filename!r}")
    # Reject backslashes — on Linux Path.name keeps them as literal chars,
    # but they indicate a Windows-style path that should be stripped or rejected.
    if "\\" in safe:
        raise ValueError(f"Filename contains backslash: {filename!r}")
    if len(safe.encode("utf-8")) > 255:
        raise ValueError(f"Filename too long: {len(safe)} chars")
    return safe


def claim_unique_filename(name: str, seen: set[str]) -> str:
    """在发生冲突时通过追加 ``_N`` 后缀生成一个唯一的文件名。

    返回的最终名称会自动加入 ``seen``，调用方无需再自行维护。

    Args:
        name: 候选文件名。
        seen: 已被占用的文件名集合（会被原地修改）。

    Returns:
        一个不在 ``seen`` 中的新文件名（已加入 ``seen``）。
    """
    if name not in seen:
        seen.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    candidate = f"{stem}_{counter}{suffix}"
    while candidate in seen:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"
    seen.add(candidate)
    return candidate


def validate_path_traversal(path: Path, base: Path) -> None:
    """校验 ``path`` 是否位于 ``base`` 之内。

    Args:
        path: 待校验的路径。
        base: 允许的根目录。

    Raises:
        PathTraversalError: 当检测到路径穿越时。
    """
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise PathTraversalError("Path traversal detected") from None


def open_upload_file_no_symlink(base_dir: Path, filename: str) -> tuple[Path, object]:
    """打开一个上传目标文件以进行安全的流式写入。

    上传目录可能被挂载到本地 sandbox 中，沙箱进程因此可能在未来的上传
    文件名上留下符号链接。普通的 ``Path.write_bytes`` 会跟随该链接并
    以 gateway 权限覆写 uploads 目录之外的文件，存在严重安全风险。

    - 在 POSIX 上，本函数使用 ``O_NOFOLLOW`` 拒绝符号链接目标；
    - 在没有 ``O_NOFOLLOW`` 的 Windows 上，使用双重 ``lstat`` 加
      ``open()`` 之后的 ``fstat`` 校验，尽量缩短 TOCTOU 窗口；虽然
      不能完全消除竞态，但显著提高了利用难度。
    - 在两种平台上都会执行路径穿越校验，防止逃出 ``base_dir``。

    Args:
        base_dir: 上传目录。
        filename: 用户输入的文件名（会被 :func:`normalize_filename` 规范化）。

    Returns:
        ``(目标文件路径, 打开的文件对象)`` 元组。调用方负责关闭文件对象。

    Raises:
        UnsafeUploadPathError: 当目标不是常规文件、存在链接或包含不安全字符时。
        PathTraversalError: 当目标逃出 ``base_dir`` 时。
        OSError: 其他底层 I/O 错误。
    """
    safe_name = normalize_filename(filename)
    dest = base_dir / safe_name

    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        st = None

    if st is not None and not stat.S_ISREG(st.st_mode):
        raise UnsafeUploadPathError(f"Upload destination is not a regular file: {safe_name}")

    validate_path_traversal(dest, base_dir)

    has_nofollow = hasattr(os, "O_NOFOLLOW")

    if has_nofollow:
        # POSIX: O_NOFOLLOW makes open() fail with ELOOP if dest is a symlink.
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK

        try:
            fd = os.open(dest, flags, 0o600)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
                raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
            raise

        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
            os.ftruncate(fd, 0)
            fh = os.fdopen(fd, "wb")
            fd = -1
        finally:
            if fd >= 0:
                os.close(fd)
        return dest, fh

    # Windows: no O_NOFOLLOW available. Uses a second lstat immediately before open()
    # to narrow the TOCTOU window, then fstat after open() as a further defence.
    # Note: a narrow race window remains between the pre-open lstat and open(); the
    # path-traversal check mitigates escapes from base_dir but cannot prevent an
    # attacker who can atomically replace dest with a symlink after the check.
    if st is not None and st.st_nlink > 1:
        raise UnsafeUploadPathError(f"Upload destination has multiple links: {safe_name}")

    flags = os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    try:
        pre_open_st = os.lstat(dest)
    except FileNotFoundError:
        pre_open_st = None

    if pre_open_st is not None and not stat.S_ISREG(pre_open_st.st_mode):
        raise UnsafeUploadPathError(f"Upload destination is not a regular file: {safe_name}")
    if pre_open_st is not None and pre_open_st.st_nlink > 1:
        raise UnsafeUploadPathError(f"Upload destination has multiple links: {safe_name}")

    try:
        fd = os.open(dest, flags, 0o600)
    except OSError as exc:
        if exc.errno in {errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
            raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
        raise

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink > 1:
            raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
        os.ftruncate(fd, 0)
        fh = os.fdopen(fd, "wb")
        fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    return dest, fh


def write_upload_file_no_symlink(base_dir: Path, filename: str, data: bytes) -> Path:
    """以不跟随目标符号链接的方式写入上传字节。

    实际行为由 :func:`open_upload_file_no_symlink` 保证，参见其说明。

    Args:
        base_dir: 上传目录。
        filename: 目标文件名。
        data: 待写入的字节内容。

    Returns:
        写入后的目标文件路径。

    Raises:
        UnsafeUploadPathError: 当目标不安全时。
        PathTraversalError: 当目标逃出 ``base_dir`` 时。
        OSError: 其他底层 I/O 错误。
    """
    dest, fh = open_upload_file_no_symlink(base_dir, filename)
    with fh:
        fh.write(data)
    return dest


def list_files_in_dir(directory: Path) -> dict:
    """列出目录中的文件（不含子目录）。

    Args:
        directory: 待扫描的目录。

    Returns:
        形如 ``{"files": [...], "count": int}`` 的字典，``files`` 按名称
        升序排列，每条记录包含 ``size``（int，字节）等字段。调用
        :func:`enrich_file_listing` 可以进一步添加 virtual / artifact URL。
    """
    if not directory.is_dir():
        return {"files": [], "count": 0}

    files = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
            files.append(
                {
                    "filename": entry.name,
                    "size": st.st_size,
                    "path": entry.path,
                    "extension": Path(entry.name).suffix,
                    "modified": st.st_mtime,
                }
            )
    return {"files": files, "count": len(files)}


def delete_file_safe(base_dir: Path, filename: str, *, convertible_extensions: set[str] | None = None) -> dict:
    """在路径穿越校验通过后删除 ``base_dir`` 中的文件。

    当传入 ``convertible_extensions`` 且文件后缀匹配时，对应的 ``.md``
    副产物（若存在）也会被一并清理。

    Args:
        base_dir: 包含待删文件的目录。
        filename: 待删除的文件名。
        convertible_extensions: 小写后缀集合（如 ``{".pdf", ".docx"}``），
            这些文件的同 ``.md`` 副产物会一并删除。

    Returns:
        包含 ``success`` 与 ``message`` 的字典。

    Raises:
        FileNotFoundError: 当文件不存在时。
        PathTraversalError: 当检测到路径穿越时。
    """
    file_path = (base_dir / filename).resolve()
    validate_path_traversal(file_path, base_dir)

    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    file_path.unlink()

    # Clean up companion markdown generated during upload conversion.
    if convertible_extensions and file_path.suffix.lower() in convertible_extensions:
        file_path.with_suffix(".md").unlink(missing_ok=True)

    return {"success": True, "message": f"Deleted {filename}"}


def upload_artifact_url(thread_id: str, filename: str) -> str:
    """为 thread uploads 目录中的文件构造 artifact URL。

    ``filename`` 会做 percent-encode，保证空格、``#``、``?`` 等字符在 URL
    中安全。

    Args:
        thread_id: 当前 thread 的 ID。
        filename: 文件名。

    Returns:
        形如 ``/api/threads/<thread_id>/artifacts/.../uploads/<filename>`` 的 URL。
    """
    return f"/api/threads/{thread_id}/artifacts{VIRTUAL_PATH_PREFIX}/uploads/{quote(filename, safe='')}"


def upload_virtual_path(filename: str) -> str:
    """为 uploads 目录中的文件构造虚拟路径。

    Args:
        filename: 文件名。

    Returns:
        形如 ``<VIRTUAL_PATH_PREFIX>/uploads/<filename>`` 的虚拟路径。
    """
    return f"{VIRTUAL_PATH_PREFIX}/uploads/{filename}"


def enrich_file_listing(result: dict, thread_id: str) -> dict:
    """为文件列表结果补充虚拟路径和 artifact URL。

    会原地修改 ``result`` 并将其返回，便于链式调用。

    Args:
        result: :func:`list_files_in_dir` 输出的字典。
        thread_id: 当前 thread 的 ID。

    Returns:
        每条 ``files`` 记录新增 ``virtual_path`` 与 ``artifact_url`` 字段的字典。
    """
    for f in result["files"]:
        filename = f["filename"]
        f["virtual_path"] = upload_virtual_path(filename)
        f["artifact_url"] = upload_artifact_url(thread_id, filename)
    return result
