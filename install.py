#!/usr/bin/env python3
"""Install the self-contained skill without changing agent configuration."""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parent / "skills" / "avito-search"


def skill_files():
    files = []
    for path in SOURCE.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in the skill package")
        if path.is_file() and (path.name in {"SKILL.md", "LICENSE"} or path.suffix in {".py", ".md", ".yaml"}):
            files.append(path.relative_to(SOURCE))
    if Path("SKILL.md") not in files:
        raise ValueError("The package has no SKILL.md")
    return sorted(files)


def install(project, host="standard"):
    project = Path(project).expanduser().resolve()
    base = ".claude" if host == "claude" else ".agents"
    parent = project / base / "skills"
    destination = parent / "avito-search"
    files = skill_files()
    if not parent.resolve().is_relative_to(project):
        raise ValueError("The skill destination must stay inside the selected project")
    if destination.is_symlink():
        raise ValueError("Refusing to replace an existing skill symlink")
    if destination.exists():
        actual = sorted(p.relative_to(destination) for p in destination.rglob("*") if p.is_file())
        if actual == files and all(not (destination / f).is_symlink() and (destination / f).read_bytes() == (SOURCE / f).read_bytes() for f in files):
            return destination
        raise ValueError(f"Skill already exists: {destination}. Keep a backup and choose an empty project; nothing was overwritten.")
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.resolve().is_relative_to(project):
        raise ValueError("The skill destination changed during installation")
    with tempfile.TemporaryDirectory(prefix=".avito-search-install-", dir=parent) as temp:
        stage = Path(temp) / "avito-search"
        for relative in files:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / relative, target)
        stage.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project that should discover the skill (default: current directory)")
    parser.add_argument("--host", choices=["standard", "claude"], default="standard", help="standard: Codex/OpenCode/Antigravity; claude: Claude Code")
    args = parser.parse_args()
    destination = install(args.project, args.host)
    print(f"Installed: {destination}")
    print("Open that project in your agent and ask it to use avito-search.")
    print("Browser dependencies are installed separately by setup_browser.py.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

