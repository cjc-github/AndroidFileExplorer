"""One-shot patcher: fix the `ls -l` row parser.

The device `ls -l` row layout is::

    <perms> <links> <owner> <group> <size> <date> <time> <name...>

The date is TWO whitespace separated fields (``2024-01-01`` and ``12:00``),
so the name starts at index 8, not 6. The previous implementation sliced at
index 8 only when 9+ fields existed, which produced garbage names for the
common 8-field layout. It also mis-detected the name column for filenames
that contain spaces.

This patch rewrites the parser to locate the name column by skipping the
fixed ``perms links owner group size date time`` prefix (7 fields).
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "base.py"

OLD_BLOCK = '''        # Layout: perms links owner group size date time name...
        size: int | None = None
        try:
            size = int(parts[4])
        except (ValueError, IndexError):
            size = None

        owner = parts[2] if len(parts) > 2 else None
        group = parts[3] if len(parts) > 3 else None

        name_parts = parts[8:] if len(parts) >= 9 else parts[5:]
        if not name_parts:
            continue
        name = " ".join(name_parts)
'''

NEW_BLOCK = '''        # Layout: perms links owner group size date time name...
        # The name column starts after 7 fixed fields (perms, links, owner,
        # group, size, date, time); everything after it belongs to the name.
        size: int | None = None
        try:
            size = int(parts[4])
        except (ValueError, IndexError):
            size = None

        owner = parts[2] if len(parts) > 2 else None
        group = parts[3] if len(parts) > 3 else None

        NAME_COLUMN = 7
        if len(parts) <= NAME_COLUMN:
            continue
        name_parts = parts[NAME_COLUMN:]
        if not name_parts:
            continue
        name = " ".join(name_parts)
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "NAME_COLUMN" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_BLOCK not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(OLD_BLOCK, NEW_BLOCK, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
