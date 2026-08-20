from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.sync_skills_to_minio import (
    collect_local_skills,
    list_remote_keys,
    sync_skills,
    verify_skills_sync,
)

BUCKET = "test-bucket"


def _make_client(remote_keys: list[str]) -> Mock:
    client = Mock()

    def paginate(Bucket: str, Prefix: str, **kwargs: object) -> list[dict[str, list]]:
        return [
            {"Contents": [{"Key": key} for key in remote_keys if key.startswith(Prefix)]}
        ]

    paginator = Mock()
    paginator.paginate.side_effect = paginate
    client.get_paginator.return_value = paginator
    return client


def _write_skills_tree(skills_root: Path) -> None:
    (skills_root / "pptx" / "scripts" / "office" / "schemas").mkdir(parents=True)
    (skills_root / "pptx" / "SKILL.md").write_text("skill")
    (skills_root / "pptx" / "LICENSE.txt").write_text("license")
    (skills_root / "pptx" / "scripts" / "office" / "validate.py").write_text("# v")
    (skills_root / "pptx" / "scripts" / "office" / "schemas" / "a.xsd").write_text("s")
    (skills_root / "pptx" / "scripts" / "thumbnail.py").write_text("# t")
    pycache = skills_root / "pptx" / "scripts" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "c.pyc").write_bytes(b"pyc")
    (skills_root / "pptx" / ".DS_Store").write_bytes(b"ds")


def test_collect_local_skills_maps_keys_and_skips_exclusions(
    tmp_path: Path,
) -> None:
    """Catches uploading junk files or wrong object keys for the Skill tree."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)

    files = collect_local_skills(skills_root)

    assert set(files) == {
        "skills/pptx/SKILL.md",
        "skills/pptx/LICENSE.txt",
        "skills/pptx/scripts/office/validate.py",
        "skills/pptx/scripts/office/schemas/a.xsd",
        "skills/pptx/scripts/thumbnail.py",
    }
    assert files["skills/pptx/SKILL.md"] == skills_root / "pptx" / "SKILL.md"


def test_collect_local_skills_rejects_missing_or_empty_root(tmp_path: Path) -> None:
    """Catches a silent no-op sync deleting the remote Skill tree."""
    with pytest.raises(RuntimeError, match="does not exist"):
        collect_local_skills(tmp_path / "missing")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="No skill files"):
        collect_local_skills(empty)


def test_sync_skills_uploads_every_local_file(tmp_path: Path) -> None:
    """Catches partial uploads leaving the mounted /skills incomplete."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client([])

    result = sync_skills(client, BUCKET, files)

    assert result.uploaded == tuple(sorted(files))
    assert result.deleted == ()
    assert client.put_object.call_count == len(files)
    uploaded_keys = {call.kwargs["Key"] for call in client.put_object.call_args_list}
    assert uploaded_keys == set(files)
    for call in client.put_object.call_args_list:
        assert call.kwargs["Bucket"] == BUCKET
        assert call.kwargs["Body"] == files[call.kwargs["Key"]].read_bytes()
    client.delete_object.assert_not_called()


def test_sync_skills_keeps_stale_remote_objects_without_delete_flag(
    tmp_path: Path,
) -> None:
    """Catches the default sync run removing remote files unexpectedly."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client(sorted(files) + ["skills/pptx/OLD.md"])

    result = sync_skills(client, BUCKET, files)

    assert result.deleted == ()
    client.delete_object.assert_not_called()


def test_sync_skills_with_delete_removes_only_stale_skills_prefix_objects(
    tmp_path: Path,
) -> None:
    """Catches deleting stale objects outside the skills/ prefix."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client(
        sorted(files) + ["skills/pptx/OLD.md", "threads/other/deck.pptx"]
    )

    result = sync_skills(client, BUCKET, files, delete=True)

    assert result.deleted == ("skills/pptx/OLD.md",)
    client.delete_object.assert_called_once_with(
        Bucket=BUCKET, Key="skills/pptx/OLD.md"
    )


def test_sync_skills_dry_run_touches_nothing(tmp_path: Path) -> None:
    """Catches a dry run performing real uploads or deletions."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client(sorted(files) + ["skills/pptx/OLD.md"])

    result = sync_skills(client, BUCKET, files, delete=True, dry_run=True)

    assert result.uploaded == tuple(sorted(files))
    assert result.deleted == ("skills/pptx/OLD.md",)
    client.put_object.assert_not_called()
    client.delete_object.assert_not_called()


def test_verify_skills_sync_fails_on_missing_or_extra_remote_objects(
    tmp_path: Path,
) -> None:
    """Catches a broken sync being reported as successful."""
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client(sorted(files)[:-1] + ["skills/pptx/EXTRA.md"])

    with pytest.raises(RuntimeError, match="verification failed"):
        verify_skills_sync(client, BUCKET, set(files))


def test_verify_skills_sync_fails_when_local_anchors_missing(tmp_path: Path) -> None:
    """Catches deploying a local Skill tree without required anchor files."""
    skills_root = tmp_path / "skills"
    (skills_root / "pptx").mkdir(parents=True)
    (skills_root / "pptx" / "SKILL.md").write_text("skill")
    files = collect_local_skills(skills_root)
    client = _make_client(sorted(files))

    with pytest.raises(RuntimeError, match="anchor file"):
        verify_skills_sync(client, BUCKET, set(files))


def test_verify_skills_sync_passes_for_complete_tree(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skills_tree(skills_root)
    files = collect_local_skills(skills_root)
    client = _make_client(sorted(files))

    verify_skills_sync(client, BUCKET, set(files))


def test_list_remote_keys_reads_all_pages() -> None:
    """Catches pagination truncation hiding stale remote objects."""
    client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "skills/a"}, {"Key": "skills/b"}]},
        {"Contents": [{"Key": "skills/c"}]},
    ]
    client.get_paginator.return_value = paginator

    assert list_remote_keys(client, BUCKET, "skills/") == {
        "skills/a",
        "skills/b",
        "skills/c",
    }


def test_settings_read_minio_env_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches the CLI reading credentials from unrelated env variables."""
    from scripts.sync_skills_to_minio import _SyncCliSettings

    env = {
        "MINIO_ENDPOINT_URL": "https://minio.example.com",
        "MINIO_BUCKET": "bucket",
        "MINIO_ACCESS_KEY": "ak",
        "MINIO_SECRET_KEY": "sk",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = _SyncCliSettings(_env_file=None)

    assert settings.endpoint_url == "https://minio.example.com"
    assert settings.bucket == "bucket"
    assert settings.access_key == "ak"
    assert settings.secret_key == "sk"
    assert settings.path_style is True
