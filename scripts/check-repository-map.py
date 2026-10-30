"""Validate the maintained repository map and revision changes without network access."""
import argparse
from pathlib import Path
import subprocess

from map_data import decode_mapping, read_mapping

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Compare the map with this Git commit or ref")
    args = parser.parse_args()
    current = read_mapping(ROOT / "data/repositories.json")
    if args.base_ref and set(args.base_ref) != {"0"}:
        def git(*arguments):
            return subprocess.run(["git", *arguments], cwd=ROOT, check=True, capture_output=True, timeout=10).stdout
        if git("ls-tree", args.base_ref, "--", "data/repositories.json"):
            previous = decode_mapping(git("show", f"{args.base_ref}:data/repositories.json"))
            if current != previous and current["revision"] <= previous["revision"]:
                raise SystemExit("Repository map changes require a higher revision")
    print(f"Repository map revision {current['revision']}: {len(current['repositories'])} verified-name entries")


if __name__ == "__main__":
    main()
