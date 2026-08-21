from __future__ import annotations

from langsmith.sandbox import SandboxClient, Snapshot


def find_ready_snapshot(client: SandboxClient, name: str) -> Snapshot:
    matches = [
        snapshot
        for snapshot in client.list_snapshots(name_contains=name)
        if snapshot.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one snapshot named {name!r}, found {len(matches)}"
        )
    snapshot = matches[0]
    if snapshot.status != "ready":
        raise RuntimeError(f"Snapshot {name!r} is not ready: {snapshot.status}")
    return snapshot
