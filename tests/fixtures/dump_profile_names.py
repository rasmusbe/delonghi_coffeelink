"""Print the raw profile-name blobs (family a4f0) of every Coffee Link machine.

Usage (credentials never leave your shell):

    DELONGHI_EMAIL=you@example.com DELONGHI_PASSWORD='...' \
        .venv/bin/python tests/fixtures/dump_profile_names.py

This is how the 21-byte name-cell layout in catalog.decode_slot_cells was read
off the reference Soul on 2026-09-08. Kept next to anonymise_dump.py so the
next protocol question can be answered from real bytes rather than guessed.

Prints, per machine: every a4f0 / aaf0 / baf0 blob as hex (these carry the
names the household typed in - do not paste them into public issues), plus
data_request / data_response for reference. Sends nothing to the machine.
"""
import asyncio
import base64
import importlib.util
import os
import sys
import types
from pathlib import Path

import aiohttp

PKG_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "delonghi_coffeelink"
PKG = "delonghi_coffeelink_dump"
package = types.ModuleType(PKG)
package.__path__ = [str(PKG_DIR)]
sys.modules[PKG] = package


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", PKG_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load("const")
ayla = _load("ayla_client")

FAMILIES = {b"\xa4\xf0": "profile names", b"\xaa\xf0": "custom names", b"\xba\xf0": "bean names"}
EXTRA = ("data_request", "data_response", "d745_profile_set")


async def main() -> None:
    email = os.environ.get("DELONGHI_EMAIL")
    password = os.environ.get("DELONGHI_PASSWORD")
    if not email or not password:
        sys.exit("set DELONGHI_EMAIL and DELONGHI_PASSWORD in the environment")
    async with aiohttp.ClientSession() as session:
        client = ayla.DelonghiAylaClient(session, email, password)
        for device in await client.async_get_devices():
            print(f"== {device.name} dsn={device.dsn} oem_model={device.oem_model}")
            props = await client.async_get_properties(device.dsn)
            for name in sorted(props):
                value = props[name].get("value")
                if not isinstance(value, str):
                    continue
                try:
                    raw = base64.b64decode("".join(value.split()), validate=True)
                except Exception:
                    continue
                label = FAMILIES.get(raw[2:4]) if len(raw) >= 4 else None
                if label or name in EXTRA:
                    declared = raw[1] + 1 if len(raw) >= 2 else None
                    print(f"{name} [{label or 'channel'}] declared={declared} got={len(raw)}")
                    print(f"    {raw.hex(' ')}")
            for name in EXTRA:
                value = props.get(name, {}).get("value")
                if not isinstance(value, str):
                    print(f"{name} = {value!r}")


asyncio.run(main())
