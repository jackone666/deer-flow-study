"""从 Claude Code CLI 与 Codex CLI 自动加载凭据。

实现两种凭据获取策略：
  1. Claude Code OAuth token（来自显式环境变量或导出的凭据文件）
     - 使用 ``Authorization: Bearer`` 头（不是 ``x-api-key``）
     - 需要 ``anthropic-beta: oauth-2025-04-20,claude-code-20250219``
     - 支持 ``$CLAUDE_CODE_OAUTH_TOKEN``、``$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`` 与 ``$ANTHROPIC_AUTH_TOKEN``
     - 通过 ``$CLAUDE_CODE_CREDENTIALS_PATH`` 覆盖路径
  2. Codex CLI token（来自 ``~/.codex/auth.json``）
     - 走 ``chatgpt.com/backend-api/codex/responses`` 端点
     - 同时支持旧版顶层 token 与当前的嵌套 tokens 结构
     - 通过 ``$CODEX_AUTH_PATH`` 覆盖路径
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Claude Code OAuth token 必需的 beta headers
OAUTH_ANTHROPIC_BETAS = "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14"


def is_oauth_token(token: str) -> bool:
    """判断一个 token 是否为 Claude Code OAuth token（而非普通 API key）。

    Args:
        token: 待检测的 token 字符串。

    Returns:
        bool: 是字符串且包含 ``sk-ant-oat`` 特征时为 ``True``。
    """
    return isinstance(token, str) and "sk-ant-oat" in token


@dataclass
class ClaudeCodeCredential:
    """Claude Code CLI OAuth 凭据。"""

    access_token: str
    refresh_token: str = ""
    expires_at: int = 0
    source: str = ""

    @property
    def is_expired(self) -> bool:
        """``expires_at`` 已超过当前时间 1 分钟时返回 ``True``。"""
        if self.expires_at <= 0:
            return False
        return time.time() * 1000 > self.expires_at - 60_000  # 1 分钟缓冲


@dataclass
class CodexCliCredential:
    """Codex CLI 凭据。"""

    access_token: str
    account_id: str = ""
    source: str = ""


def _resolve_credential_path(env_var: str, default_relative_path: str) -> Path:
    """按 ``env_var``/家目录默认相对路径解析凭据文件路径。"""
    configured_path = os.getenv(env_var)
    if configured_path:
        return Path(configured_path).expanduser()
    return _home_dir() / default_relative_path


def _home_dir() -> Path:
    """解析家目录，优先使用 ``$HOME``，否则 ``Path.home()``。"""
    home = os.getenv("HOME")
    if home:
        return Path(home).expanduser()
    return Path.home()


def _load_json_file(path: Path, label: str) -> dict[str, Any] | None:
    """读取 JSON 凭据文件，失败时记录告警并返回 ``None``。"""
    if not path.exists():
        logger.debug(f"{label} not found: {path}")
        return None
    if path.is_dir():
        logger.warning(f"{label} path is a directory, expected a file: {path}")
        return None

    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {label}: {e}")
        return None


def _read_secret_from_file_descriptor(env_var: str) -> str | None:
    """从 ``$env_var`` 指向的文件描述符中读取秘密。"""
    fd_value = os.getenv(env_var)
    if not fd_value:
        return None

    try:
        fd = int(fd_value)
    except ValueError:
        logger.warning(f"{env_var} must be an integer file descriptor, got: {fd_value}")
        return None

    try:
        secret = os.read(fd, 1024 * 1024).decode().strip()
    except OSError as e:
        logger.warning(f"Failed to read {env_var}: {e}")
        return None

    return secret or None


def _credential_from_direct_token(access_token: str, source: str) -> ClaudeCodeCredential | None:
    """从直接提供的 token 字符串构造凭据对象。"""
    token = access_token.strip()
    if not token:
        return None
    return ClaudeCodeCredential(access_token=token, source=source)


def _iter_claude_code_credential_paths() -> list[Path]:
    """列出 Claude Code 凭据文件的候选路径（覆盖 + 默认）。"""
    paths: list[Path] = []
    override_path = os.getenv("CLAUDE_CODE_CREDENTIALS_PATH")
    if override_path:
        paths.append(Path(override_path).expanduser())

    default_path = _home_dir() / ".claude/.credentials.json"
    if not paths or paths[-1] != default_path:
        paths.append(default_path)

    return paths


def _extract_claude_code_credential(data: dict[str, Any], source: str) -> ClaudeCodeCredential | None:
    """从已解析的 JSON 中抽取 Claude Code 凭据。"""
    oauth = data.get("claudeAiOauth", {})
    access_token = oauth.get("accessToken", "")
    if not access_token:
        logger.debug("Claude Code credentials container exists but no accessToken found")
        return None

    cred = ClaudeCodeCredential(
        access_token=access_token,
        refresh_token=oauth.get("refreshToken", ""),
        expires_at=oauth.get("expiresAt", 0),
        source=source,
    )

    if cred.is_expired:
        logger.warning("Claude Code OAuth token is expired. Run 'claude' to refresh.")
        return None

    return cred


def load_claude_code_credential() -> ClaudeCodeCredential | None:
    """从显式 Claude Code 交接源加载 OAuth 凭据。

    查找顺序：
      1. ``$CLAUDE_CODE_OAUTH_TOKEN`` 或 ``$ANTHROPIC_AUTH_TOKEN``
      2. ``$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR``
      3. ``$CLAUDE_CODE_CREDENTIALS_PATH``
      4. ``~/.claude/.credentials.json``

    导出的凭据文件内容形如：
    ::
        {
          "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-...",
            "refreshToken": "sk-ant-ort01-...",
            "expiresAt": 1773430695128,
            "scopes": ["user:inference", ...],
            ...
          }
        }

    Returns:
        ClaudeCodeCredential | None: 成功时返回凭据对象；找不到或已过期则返回 ``None``。
    """
    direct_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    if direct_token:
        cred = _credential_from_direct_token(direct_token, "claude-cli-env")
        if cred:
            logger.info("Loaded Claude Code OAuth credential from environment")
        return cred

    fd_token = _read_secret_from_file_descriptor("CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR")
    if fd_token:
        cred = _credential_from_direct_token(fd_token, "claude-cli-fd")
        if cred:
            logger.info("Loaded Claude Code OAuth credential from file descriptor")
        return cred

    override_path = os.getenv("CLAUDE_CODE_CREDENTIALS_PATH")
    override_path_obj = Path(override_path).expanduser() if override_path else None
    for cred_path in _iter_claude_code_credential_paths():
        data = _load_json_file(cred_path, "Claude Code credentials")
        if data is None:
            continue
        cred = _extract_claude_code_credential(data, "claude-cli-file")
        if cred:
            source_label = "override path" if override_path_obj is not None and cred_path == override_path_obj else "plaintext file"
            logger.info(f"Loaded Claude Code OAuth credential from {source_label} (expires_at={cred.expires_at})")
            return cred

    return None


def load_codex_cli_credential() -> CodexCliCredential | None:
    """从 Codex CLI（``~/.codex/auth.json``）加载凭据。

    Returns:
        CodexCliCredential | None: 成功时返回凭据对象；找不到或文件无 token 时返回 ``None``。
    """
    cred_path = _resolve_credential_path("CODEX_AUTH_PATH", ".codex/auth.json")
    data = _load_json_file(cred_path, "Codex CLI credentials")
    if data is None:
        return None
    tokens = data.get("tokens", {})
    if not isinstance(tokens, dict):
        tokens = {}

    access_token = data.get("access_token") or data.get("token") or tokens.get("access_token", "")
    account_id = data.get("account_id") or tokens.get("account_id", "")
    if not access_token:
        logger.debug("Codex CLI credentials file exists but no token found")
        return None

    logger.info("Loaded Codex CLI credential")
    return CodexCliCredential(
        access_token=access_token,
        account_id=account_id,
        source="codex-cli",
    )
