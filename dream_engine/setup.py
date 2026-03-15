"""Interactive first-run setup wizard for Dream Engine."""

import os
from pathlib import Path


def _count_dirs(path: Path) -> tuple[int, int]:
    """Count total dirs and dirs with memory-bank/ in a path."""
    if not path.is_dir():
        return 0, 0
    total = 0
    with_mb = 0
    for entry in path.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            total += 1
            if (entry / "memory-bank").is_dir():
                with_mb += 1
    return total, with_mb


def _discover_candidate_paths() -> list[dict]:
    """Find common project directories on the system."""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Projects",
        home / "projects",
        home / "code",
        home / "Code",
        home / "src",
        home / "dev",
        home / "repos",
        home / "workspace",
        home / "Documents" / "Projects",
    ]
    results = []
    for path in candidates:
        if path.is_dir():
            total, with_mb = _count_dirs(path)
            if total > 0:
                results.append({
                    "path": str(path),
                    "total": total,
                    "with_mb": with_mb,
                })
    return results


def run_setup() -> dict:
    """Run interactive setup, return config dict."""
    print()
    print("  Welcome to Dream Engine!")
    print("  ========================")
    print()
    print("  No configuration found. Let's set up.\n")

    # Scan for project directories
    print("  Scanning for project directories...\n")
    candidates = _discover_candidate_paths()

    if not candidates:
        print("  No common project directories found.")
        print("  Enter a path to scan (e.g. ~/Desktop):")
        custom = input("  > ").strip()
        if custom:
            path = Path(custom).expanduser().resolve()
            total, with_mb = _count_dirs(path)
            candidates = [{"path": str(path), "total": total, "with_mb": with_mb}]

    # Let user pick paths
    selected_paths = []
    if candidates:
        print("  Found these directories:\n")
        for i, c in enumerate(candidates, 1):
            mb_note = f", {c['with_mb']} with memory-banks" if c["with_mb"] > 0 else ""
            print(f"    {i}. {c['path']} ({c['total']} projects{mb_note})")

        print(f"\n  Select paths to scan (comma-separated numbers, e.g. 1,2):")
        print(f"  Or press Enter to select all.")
        choice = input("  > ").strip()

        if not choice:
            selected_paths = [c["path"] for c in candidates]
        else:
            for num in choice.split(","):
                num = num.strip()
                if num.isdigit():
                    idx = int(num) - 1
                    if 0 <= idx < len(candidates):
                        selected_paths.append(candidates[idx]["path"])

    # Custom path
    print("\n  Add another path? (Enter to skip)")
    custom = input("  > ").strip()
    if custom:
        path = Path(custom).expanduser().resolve()
        if path.is_dir():
            selected_paths.append(str(path))

    if not selected_paths:
        print("\n  No paths selected. You can edit .env manually later.")
        selected_paths = [str(Path.home() / "Desktop")]

    # Claude model
    print("\n  Claude Code model for deep analysis:")
    print("    1. opus   — deepest, most thorough (recommended)")
    print("    2. sonnet — good balance of depth and speed")
    print("    3. haiku  — fastest, cheapest")
    model_choice = input("  > [1] ").strip()
    model_map = {"1": "opus", "2": "sonnet", "3": "haiku", "": "opus"}
    claude_model = model_map.get(model_choice, "opus")

    # Wiki.js
    print("\n  Optional: Wiki.js integration")
    print("  Push project pages to a Wiki.js instance?")
    wiki_enabled = input("  Enable? [y/N] ").strip().lower() == "y"
    wiki_update_script = ""
    wiki_export_script = ""
    if wiki_enabled:
        wiki_update_script = input("  Wiki update script path [~/bin/wiki-update.sh]: ").strip()
        if not wiki_update_script:
            wiki_update_script = "~/bin/wiki-update.sh"
        wiki_export_script = input("  Wiki export script path [~/bin/wiki-export-for-claude.sh]: ").strip()
        if not wiki_export_script:
            wiki_export_script = "~/bin/wiki-export-for-claude.sh"

    # ElizaOS/Milady
    print("\n  Optional: ElizaOS/Milady integration")
    print("  Inject knowledge into an ElizaOS agent's memory?")
    milady_enabled = input("  Enable? [y/N] ").strip().lower() == "y"
    milady_api_url = "http://localhost:2138"
    if milady_enabled:
        url = input("  Milady API URL [http://localhost:2138]: ").strip()
        if url:
            milady_api_url = url

    config = {
        "DREAM_SCAN_PATHS": ",".join(selected_paths),
        "DREAM_CLAUDE_MODEL": claude_model,
        "DREAM_WIKI_ENABLED": str(wiki_enabled).lower(),
        "DREAM_WIKI_UPDATE_SCRIPT": wiki_update_script,
        "DREAM_WIKI_EXPORT_SCRIPT": wiki_export_script,
        "DREAM_MILADY_ENABLED": str(milady_enabled).lower(),
        "DREAM_MILADY_API_URL": milady_api_url,
    }

    # Write .env
    env_path = Path.cwd() / ".env"
    lines = []
    for key, value in config.items():
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")

    print(f"\n  Configuration saved to {env_path}")
    print(f"  Scanning: {', '.join(selected_paths)}")
    print(f"  Model: {claude_model}")
    print(f"  Wiki.js: {'enabled' if wiki_enabled else 'disabled'}")
    print(f"  ElizaOS: {'enabled' if milady_enabled else 'disabled'}")
    print()

    return config
