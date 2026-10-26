"""Extract one English-first bilingual CHANGELOG section for GitHub Releases."""

import argparse
import json
from pathlib import Path
import re
import sys


def extract_notes(changelog: str, version: str) -> str:
    lines = changelog.splitlines()
    heading = re.compile(rf"^## \[{re.escape(version)}\](?: - .+)?$")
    matches = [i for i, line in enumerate(lines) if heading.fullmatch(line)]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one CHANGELOG section for {version}; found {len(matches)}")
    start = matches[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = lines[start:end]
    languages = [(i, line) for i, line in enumerate(section) if line.startswith("### ")]
    if [line for _, line in languages] != ["### English", "### 中文"]:
        raise ValueError(f"CHANGELOG section {version} must have English first, then Chinese")
    for index, (begin, language) in enumerate(languages):
        stop = languages[index + 1][0] if index + 1 < len(languages) else len(section)
        if not any(line.strip() and not line.startswith("#") for line in section[begin + 1:stop]):
            raise ValueError(f"CHANGELOG section {version} has no content under {language}")
    return "\n".join(section).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="Version or v-prefixed tag; defaults to package.json")
    parser.add_argument("--output", type=Path, help="Write notes to this file instead of stdout")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        version = args.version or json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
        notes = extract_notes((root / "CHANGELOG.md").read_text(encoding="utf-8"), version.removeprefix("v"))
        if args.output:
            args.output.write_text(notes, encoding="utf-8")
        else:
            sys.stdout.write(notes)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
