"""One-shot patcher: settings route, nav item, poll interval wiring."""

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


# ---- router ------------------------------------------------------------
patch(
    FE / "router.tsx",
    [
        (
            '''  {
    path: "/machines",''',
            '''  {
    path: "/settings",
    ...commonLayoutRoute("/settings", <SettingsPage />),
  },
  {
    path: "/machines",''',
            "route entry",
        ),
    ],
    "router",
)

print(f"DONE ({edits})")
