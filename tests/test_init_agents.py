"""Tests for devteam init copying agent templates to ~/.devteam/agents/."""

from devteam.agents.template_manager import copy_agent_templates, get_bundled_templates_dir


class TestGetBundledTemplatesDir:
    def test_returns_path_to_templates(self):
        templates_dir = get_bundled_templates_dir()
        assert templates_dir.is_dir()
        assert (templates_dir / "ceo.md").exists()
        assert len(list(templates_dir.glob("*.md"))) == 16


class TestCopyAgentTemplates:
    def test_copies_all_templates(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        assert dest.is_dir()
        md_files = list(dest.glob("*.md"))
        assert len(md_files) == 16

    def test_creates_destination_directory(self, tmp_path):
        dest = tmp_path / "nested" / "agents"
        copy_agent_templates(dest)
        assert dest.is_dir()

    def test_preserves_existing_customizations(self, tmp_path):
        dest = tmp_path / "agents"
        dest.mkdir()
        custom_file = dest / "ceo.md"
        custom_file.write_text("custom CEO content")

        copy_agent_templates(dest, overwrite=False)
        assert custom_file.read_text() == "custom CEO content"
        # But missing agents should be copied
        assert (dest / "backend_engineer.md").exists()

    def test_overwrite_replaces_existing(self, tmp_path):
        dest = tmp_path / "agents"
        dest.mkdir()
        custom_file = dest / "ceo.md"
        custom_file.write_text("custom CEO content")

        copy_agent_templates(dest, overwrite=True)
        assert custom_file.read_text() != "custom CEO content"
        assert "model: opus" in custom_file.read_text()

    def test_copies_content_correctly(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        ceo_content = (dest / "ceo.md").read_text()
        assert "model: opus" in ceo_content
        assert "You are the CEO" in ceo_content
