"""One-shot patcher: the settings page, route, nav item and poll interval."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
FE = ROOT / "frontend" / "src"

edits = 0


def patch(path: pathlib.Path, pairs, label: str) -> None:
    global edits
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            edits += 1
            print(f"  {label}.{name} OK")
        elif new in source:
            print(f"  {label}.{name} already applied")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---- types -------------------------------------------------------------
patch(
    FE / "api" / "types.ts",
    [
        (
            '''export interface Engine {''',
            '''export interface PlatformSettings {
  sync_interval_seconds: number;
  auto_root: boolean;
  auto_sync_seconds: number;
}

export interface Engine {''',
            "type",
        ),
    ],
    "types",
)

# ---- api ---------------------------------------------------------------
patch(
    FE / "api" / "index.ts",
    [
        (
            '''/** Switch the root mode of several devices at once. */''',
            '''/** Read the platform settings. */
export const getSettings = () =>
  unwrap<PlatformSettings>(request.get("/settings"));

/** Replace the platform settings. */
export const updateSettings = (payload: PlatformSettings) =>
  unwrap<PlatformSettings>(request.put("/settings", payload));

/** Switch the root mode of several devices at once. */''',
            "api fns",
        ),
    ],
    "api",
)

print(f"DONE ({edits})")
