"""One-shot patcher: add DeviceRegistry.delete_engine.

The machine management page needs to remove a machine. Deleting a machine
must also remove the device rows that belong to it, otherwise the registry
keeps devices whose machine is gone.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "services" / "registry.py"

ANCHOR = '''    # ------------------------------------------------------------------ devices
'''

NEW = '''    async def delete_engine(self, engine_id: int) -> bool:
        """Remove a machine and every device row that belongs to it.

        Returns ``True`` when a machine was removed.
        """
        engine = await self.db.get(EngineRecord, engine_id)
        if engine is None:
            return False

        result = await self.db.execute(
            select(DeviceRecord).where(DeviceRecord.engine_id == engine_id)
        )
        for device in result.scalars().all():
            await self.db.delete(device)

        await self.db.delete(engine)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ devices
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "async def delete_engine" in source:
        print("ALREADY_PATCHED")
        return 0
    if ANCHOR not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(ANCHOR, NEW, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
