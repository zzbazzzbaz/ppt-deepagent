from pathlib import Path

WORKFLOW = Path(".github/workflows/build-sandbox-image.yml")


def test_workflow_only_auto_runs_for_sandbox_context_changes() -> None:
    """Catches rebuilding the image for unrelated application code changes."""
    text = WORKFLOW.read_text()
    for required in (
        "push:",
        "branches: [main]",
        "sandbox/**",
        "agent/skills/pptx/**",
        ".github/workflows/build-sandbox-image.yml",
        "scripts/sandbox_snapshot.py",
        "workflow_dispatch:",
    ):
        assert required in text


def test_workflow_build_context_bundles_the_pptx_skill() -> None:
    """Catches an image built without the baked-in PPTX skill."""
    text = WORKFLOW.read_text()
    assert "cp -R agent/skills/pptx /tmp/sandbox-context/agent/skills/pptx" in text
    assert "COPY agent/skills/pptx /skills/pptx" in Path("sandbox/Dockerfile").read_text()


def test_workflow_serializes_latest_snapshot_updates() -> None:
    """Catches concurrent pipelines racing to replace the latest snapshot."""
    text = WORKFLOW.read_text()
    assert "group: ppt-deepagent-sandbox-snapshot-sync" in text
    assert "cancel-in-progress: false" in text


def test_workflow_pushes_dual_tags_and_syncs_digest() -> None:
    """Catches mutable tags or missing digest breaking skip and rollback logic."""
    text = WORKFLOW.read_text()
    for required in (
        "ppt-deepagent-sandbox-${{ steps.metadata.outputs.timestamp }}",
        "ppt-deepagent-sandbox-latest",
        "id: build",
        "${{ steps.build.outputs.digest }}",
        "python -m scripts.sandbox_snapshot sync-image",
        "--name ppt-deepagent-sandbox-latest",
        "--candidate-name ppt-deepagent-sandbox-candidate-${{ steps.metadata.outputs.timestamp }}",
        "LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}",
    ):
        assert required in text
