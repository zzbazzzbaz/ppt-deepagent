"""工作目录（/workspace/work）的共享操作：文件列举与符号链接防护。

sync 与 save_output 两个工具共用，避免重复实现。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.tools import ToolException

REMOTE_WORK = PurePosixPath("/workspace/work")


async def list_work_files(
    backend: SandboxBackendProtocol,
    *,
    only_suffixes: set[str] | None = None,
) -> list[PurePosixPath]:
    """列出工作目录中的全部文件，返回相对路径；可只保留指定后缀的文件。"""
    result = await backend.aglob("**/*", str(REMOTE_WORK))
    if result.error is not None:
        raise ToolException(f"列出工作目录失败：{result.error}")
    if result.truncated:
        raise ToolException("工作目录列表被截断。")

    files: list[PurePosixPath] = []
    folded_paths: set[str] = set()
    for entry in result.matches or []:
        if entry.get("is_dir", False):
            continue
        listed_path = PurePosixPath(entry["path"])
        remote_path = (
            listed_path
            if listed_path.is_absolute()
            else REMOTE_WORK.joinpath(listed_path)
        )
        try:
            relative_path = remote_path.relative_to(REMOTE_WORK)
        except ValueError as exc:
            raise ToolException(f"文件路径超出工作目录：{remote_path}") from exc
        if not relative_path.parts or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ToolException(f"无效的工作目录路径：{remote_path}")

        if (
            only_suffixes is not None
            and relative_path.suffix.lower() not in only_suffixes
        ):
            continue
        folded_path = relative_path.as_posix().casefold()
        if folded_path in folded_paths:
            raise ToolException(f"工作目录存在仅大小写不同的重复路径：{remote_path}")
        folded_paths.add(folded_path)
        files.append(relative_path)

    return sorted(files)


async def reject_work_symlinks(backend: SandboxBackendProtocol) -> None:
    """拒绝工作目录中的任何符号链接，防止文件越界读取。"""
    result = await backend.aexecute("find /workspace/work -type l -print -quit")
    if result.exit_code != 0:
        raise ToolException(f"检查工作目录失败：{result.output}")
    if result.output.strip():
        raise ToolException(f"工作目录包含符号链接：{result.output}")
