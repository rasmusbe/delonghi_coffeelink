"""Coordinator wiring: cloud-session identity (#15), reachability, monitor (#14).

The pure helpers are covered in ``test_eletta_session`` and ``test_monitor``;
what matters here is that the coordinator actually *uses* them - the original
bug was precisely a correct-looking session that carried the wrong identity.
Home Assistant is not installed in this suite, so the handful of symbols the
coordinator imports from it are stubbed with the minimum surface these tests
exercise. This is the only module that loads ``coordinator.py``, which is why
new coordinator cases belong here: a second module installing its own stubs
would race this one for the cached ``sys.modules`` entries.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
import time
import types
from pathlib import Path

import pytest

if importlib.util.find_spec("homeassistant") is not None:  # pragma: no cover
    # A real Home Assistant is installed (CI with pytest-homeassistant-custom-
    # component): the stubs below would shadow it for every later import, so this
    # module steps aside - port these cases to the HA fixtures instead.
    pytest.skip(
        "real Home Assistant present; stub-based coordinator tests skipped",
        allow_module_level=True,
    )

PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "delonghi_coffeelink"


# --- minimal Home Assistant stubs ------------------------------------------

class _StubStore:
    """helpers.storage.Store: in-memory, records delayed saves."""

    def __init__(self, hass=None, version=None, key=None) -> None:
        self.data: dict | None = None
        self.saves = 0

    async def async_load(self) -> dict | None:
        return self.data

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.saves += 1
        self.data = data_func()


class _StubCoordinator:
    """helpers.update_coordinator.DataUpdateCoordinator."""

    def __init__(self, hass, logger, name=None, update_interval=None) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: dict | None = None
        # What CoordinatorEntity.available reads; a failed poll clears it.
        self.last_update_success = True

    def __class_getitem__(cls, item):  # DataUpdateCoordinator[dict[str, Any]]
        return cls

    async def async_shutdown(self) -> None:
        return None

    async def async_request_refresh(self) -> None:
        return None


class _StubHomeAssistantError(Exception):
    """exceptions.HomeAssistantError, with the translation kwargs HA accepts."""

    def __init__(self, *args, translation_domain=None, translation_key=None,
                 translation_placeholders=None) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class _StubCoordinatorEntity:
    """helpers.update_coordinator.CoordinatorEntity.

    Only the part under test is reproduced: HA's own class ties `available` to
    the coordinator's last poll, which is exactly what select.py overrides.
    """

    def __init__(self, coordinator, context=None) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):  # CoordinatorEntity[DelonghiCoordinator]
        return cls

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class _StubSelectEntity:
    """components.select.SelectEntity - a bare base; options/current_option are the
    subclass's own properties, and the state write after a selection is a no-op."""

    _attr_has_entity_name = False
    name = None

    def async_write_ha_state(self) -> None:
        return None


def _install_stubs() -> None:
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = _StubHomeAssistantError
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = _StubStore
    upd = types.ModuleType("homeassistant.helpers.update_coordinator")
    upd.DataUpdateCoordinator = _StubCoordinator
    upd.CoordinatorEntity = _StubCoordinatorEntity
    upd.UpdateFailed = type("UpdateFailed", (Exception,), {})
    # The entity platform: select.py is loaded here too, so its imports resolve.
    select_mod = types.ModuleType("homeassistant.components.select")
    select_mod.SelectEntity = _StubSelectEntity
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic", CONFIG="config")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceInfo = dict
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    for name, mod in (
        ("homeassistant", types.ModuleType("homeassistant")),
        ("homeassistant.components", types.ModuleType("homeassistant.components")),
        ("homeassistant.components.select", select_mod),
        ("homeassistant.config_entries", config_entries),
        ("homeassistant.const", ha_const),
        ("homeassistant.core", core),
        ("homeassistant.exceptions", exceptions),
        ("homeassistant.helpers", types.ModuleType("homeassistant.helpers")),
        ("homeassistant.helpers.device_registry", device_registry),
        ("homeassistant.helpers.entity_platform", entity_platform),
        ("homeassistant.helpers.storage", storage),
        ("homeassistant.helpers.update_coordinator", upd),
    ):
        sys.modules[name] = mod


def _load(modname: str, filename: str):
    full = f"delonghi_coffeelink.{modname}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PKG_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


if "delonghi_coffeelink" not in sys.modules:
    _pkg = types.ModuleType("delonghi_coffeelink")
    _pkg.__path__ = [str(PKG_DIR)]
    sys.modules["delonghi_coffeelink"] = _pkg

_install_stubs()
const = _load("const", "const.py")
cb = _load("command_builder", "command_builder.py")
ac = _load("ayla_client", "ayla_client.py")
coordinator = _load("coordinator", "coordinator.py")
select = _load("select", "select.py")

COFFEE_FRAME = "DQ+D8AIDAQBuAgMnAQa/qWp4qtoAxYYh"
COFFEE_SIGNATURE = bytes.fromhex("00c58621")
COFFEE_APP_ID = 12944929
DEFAULT_APP_ID = ac.normalize_signed_app_id(const.INTEGRATION_CLOUD_APP_ID)
SOUL_HOT_WATER = "DQ2D8BABDwD6GwEGgSRqILPb"


class _RecordingClient:
    """The slice of DelonghiAylaClient the send paths touch."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def async_set_property_value(self, dsn: str, prop: str, value: str) -> dict:
        self.writes.append((dsn, prop, value))
        return {}


class _PollingClient(_RecordingClient):
    """Also answers a full poll, so _async_update_data can really run."""

    def __init__(self, connection_status: str = "Online", props: dict | None = None) -> None:
        super().__init__()
        self.connection_status = connection_status
        self.connected_at = "2026-08-12T04:01:46Z"
        self.props = props if props is not None else {}

    async def async_get_properties(self, dsn: str) -> dict:
        return self.props

    async def async_get_devices(self) -> list:
        return [
            ac.AylaDevice(
                dsn="AC000W046513715",
                name="Coffee Maker",
                oem_model="DL-millcore",
                model="ECAM450.65.S",
                sw_version="1.0",
                lan_ip="192.168.1.10",
                connection_status=self.connection_status,
                connected_at=self.connected_at,
            )
        ]


def _coord(oem_model: str, connection_status: str = "Online", client=None):
    device = ac.AylaDevice(
        dsn="AC000W046513715",
        name="Coffee Maker",
        oem_model=oem_model,
        model="ECAM450.65.S",
        sw_version="1.0",
        lan_ip="192.168.1.10",
        connection_status=connection_status,
        connected_at="2026-08-12T04:01:46Z",
    )
    coord = coordinator.DelonghiCoordinator(object(), client, device)
    coord.command_property = "data_request"
    return coord


def _decoded(frame: str) -> dict:
    decoded = cb.decode_command(frame)
    decoded["origin"] = "app"
    return decoded


# --- Eletta -----------------------------------------------------------------

def test_eletta_starts_on_the_fallback_constant():
    coord = _coord("DL-striker-cb")
    assert coord.own_cloud_app_id == DEFAULT_APP_ID
    assert coord.uses_device_cloud_app_id is False


def test_learning_a_frame_switches_the_session_to_the_machine_id():
    coord = _coord("DL-striker-cb")
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    assert coord.learned_start_frames == {0x02: COFFEE_FRAME}
    assert coord.own_cloud_app_id == COFFEE_APP_ID
    assert coord.uses_device_cloud_app_id is True
    # the id actually used for the session POST follows
    assert coord._integration_app_id == COFFEE_APP_ID
    # and a session confirmed under the old id must be re-established
    assert coord._session_confirmed is False
    assert coord._last_connect_at == 0


def test_restart_restores_the_machine_id_before_any_new_capture():
    coord = _coord("DL-striker-cb")
    coord._store.data = cb.serialize_learned_frames({0x02: COFFEE_FRAME}, {}, None)
    asyncio.run(coord.async_load_learned())
    assert coord.learned_start_frames == {0x02: COFFEE_FRAME}
    assert coord.own_cloud_app_id == COFFEE_APP_ID
    assert coord._integration_app_id == COFFEE_APP_ID


def test_adopted_foreign_session_is_not_rebound_by_a_new_capture():
    """A foreign session must keep riding the app's id until it is released."""
    coord = _coord("DL-striker-cb")
    coord._integration_app_id = 777
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    assert coord.own_cloud_app_id == COFFEE_APP_ID
    assert coord._integration_app_id == 777
    # once the machine reports a free slot, we revert to the derived id
    coord._update_session_from_props({const.APP_ID_PROPERTY: {"value": 0}})
    assert coord._integration_app_id == COFFEE_APP_ID


def test_session_holder_is_ours_once_the_machine_id_is_known():
    coord = _coord("DL-striker-cb")
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    coord._update_session_from_props(
        {const.APP_ID_PROPERTY: {"value": str(COFFEE_APP_ID)}}
    )
    assert coord._session_confirmed is True


def test_wake_and_standby_carry_the_machine_signature():
    coord = _coord("DL-striker-cb")
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    for value in (coord._wake_command_value(), coord._standby_command_value()):
        assert base64.b64decode(value)[-4:] == COFFEE_SIGNATURE


def test_unlearnable_frame_leaves_the_session_id_alone():
    coord = _coord("DL-striker-cb")
    raw = bytearray(base64.b64decode(COFFEE_FRAME))
    raw[8] ^= 0xFF  # corrupt the bytes the CRC covers
    coord._maybe_learn_frame(_decoded(base64.b64encode(bytes(raw)).decode()))
    assert coord.learned_start_frames == {}
    assert coord.own_cloud_app_id == DEFAULT_APP_ID


def _capture(coord, frame: str, prop: str = "app_data_request") -> None:
    """Drive a frame through the sniffer the way a poll would."""
    coord.command_property = prop
    coord._capture_channel(
        {prop: {"value": "AA==", "data_updated_at": "t0"}}, prop, channel="command"
    )
    coord._capture_channel(
        {prop: {"value": frame, "data_updated_at": "t1"}}, prop, channel="command"
    )


def test_sniffer_learns_the_coffee_frame_end_to_end():
    """Issue #15 bug 2, through the real capture path: Coffee used to be dropped."""
    coord = _coord("DL-striker-cb")
    _capture(coord, COFFEE_FRAME)
    assert coord.learned_start_frames == {0x02: COFFEE_FRAME}
    assert coord.own_cloud_app_id == COFFEE_APP_ID


def test_replayed_learned_frame_keeps_crc_and_signature():
    coord = _coord("DL-striker-cb")
    _capture(coord, COFFEE_FRAME)
    replayed = coord.profile.beverage_value(
        0x02, const.ACTION_START, coord.learned_start_frames[0x02]
    )
    decoded = cb.decode_command(replayed)
    assert decoded["crc_valid"] is True
    assert decoded["recipe"] == "01 00 6e 02 03 27 01 06"
    assert base64.b64decode(replayed)[-4:] == COFFEE_SIGNATURE
    assert replayed != COFFEE_FRAME  # only the timestamp moved


def test_sniffer_ignores_our_own_echoed_command():
    coord = _coord("DL-striker-cb")
    coord._record_sent(COFFEE_FRAME)
    _capture(coord, COFFEE_FRAME)
    assert coord.learned_start_frames == {}


def test_sniffer_does_not_learn_our_own_fallback_frame():
    """A Soul-shaped fallback seen on the wire is ours, not something to learn."""
    coord = _coord("DL-striker-cb")
    _capture(coord, SOUL_HOT_WATER)
    assert coord.last_captured_command["matches_integration"] is True
    assert coord.learned_start_frames == {}
    assert coord.own_cloud_app_id == DEFAULT_APP_ID


# --- Soul (reference machine) ----------------------------------------------

def test_soul_never_learns_and_keeps_the_default_id():
    coord = _coord("DL-millcore")
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    coord._maybe_learn_frame(_decoded(SOUL_HOT_WATER))
    assert coord.learned_start_frames == {}
    assert coord.learned_stop_frames == {}
    assert coord.own_cloud_app_id == DEFAULT_APP_ID
    assert coord.uses_device_cloud_app_id is False


def test_soul_command_bytes_are_unchanged():
    """The Soul path synthesizes its frame and never touches the session id."""
    coord = _coord("DL-millcore")
    assert coord.profile.uses_cloud_session is False
    built = coord.profile.beverage_value(0x10, const.ACTION_START, None)
    assert base64.b64decode(built)[:12].hex(" ") == "0d 0d 83 f0 10 01 0f 00 fa 1b 01 06"


# --- reachability preflight -------------------------------------------------
#
# An offline machine still gets HTTP 200/201 from Ayla for every datapoint
# write, so nothing anywhere reports a failure: the reference Soul sat off the
# network for ten days while Home Assistant kept "sending" wakes. These cases
# pin the guard AND its narrowness - only an explicit Offline may block.

SEND_CALLS = (
    ("wake", lambda coord: coord.async_send_wake()),
    ("standby", lambda coord: coord.async_send_standby()),
    ("beverage start", lambda coord: coord.async_send_beverage(0x01, const.ACTION_START)),
    ("beverage stop", lambda coord: coord.async_send_beverage(0x10, const.ACTION_STOP)),
    ("profile", lambda coord: coord.async_send_profile(1)),
)
# Both families: the Soul synthesizes its frames, the Eletta replays learned ones
# behind a cloud session. Neither may reach the cloud while the machine is gone.
GUARDED_MODELS = ("DL-millcore", "DL-striker-cb")


@pytest.mark.parametrize("model", GUARDED_MODELS)
@pytest.mark.parametrize("label,call", SEND_CALLS, ids=[case[0] for case in SEND_CALLS])
def test_offline_machine_refuses_to_send(label, call, model):
    client = _RecordingClient()
    coord = _coord(model, connection_status="Offline", client=client)

    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(call(coord))

    assert err.value.translation_key == "machine_offline"
    assert err.value.translation_domain == const.DOMAIN
    assert err.value.translation_placeholders == {"name": "Coffee Maker"}
    assert client.writes == [], f"{label} on {model} reached the cloud despite Offline"


def test_the_raw_channel_is_never_refused():
    """`send_raw_command` is the field-instrumentation escape hatch.

    Refusing it would remove the only way to poke a machine when what is wrong
    is the integration's own idea of its state - the instrumentation this repo
    diagnoses with must survive its own guards.
    """
    client = _RecordingClient()
    coord = _coord("DL-millcore", connection_status="Offline", client=client)

    asyncio.run(coord.async_send_raw("DQeEDwIBVRJqiYFO"))

    assert [value for _dsn, _prop, value in client.writes] == ["DQeEDwIBVRJqiYFO"]


def test_online_machine_still_sends():
    client = _RecordingClient()
    coord = _coord("DL-millcore", connection_status="Online", client=client)

    asyncio.run(coord.async_send_wake())

    assert len(client.writes) == 1
    dsn, prop, value = client.writes[0]
    assert (dsn, prop) == (coord.device.dsn, "data_request")
    assert base64.b64decode(value)[:6].hex(" ") == "0d 07 84 0f 02 01"


def test_deep_standby_machine_is_never_blocked():
    """A machine in standby is Online, and must stay wakeable (#1).

    Issue #1 is an Eletta (DL-striker-cb), the profile where standby actually
    changes the send sequence: the guard reads connection_status, never the
    monitor, so a standby machine is not blocked - it gets the deep-standby
    nudge and then the wake.
    """
    client = _RecordingClient()
    coord = _coord("DL-striker-cb", connection_status="Online", client=client)
    coord.monitor = {"status": 0, "status_name": "standby"}

    asyncio.run(coord.async_send_wake())

    assert coord.machine_is_offline is False
    families = [
        base64.b64decode(value)[2:4].hex(" ") for _dsn, _prop, value in client.writes
    ]
    assert families == ["84 0f", "84 0f"]  # session refresh, then wake
    params = [
        base64.b64decode(value)[4:6].hex(" ") for _dsn, _prop, value in client.writes
    ]
    assert params == ["03 02", "02 01"]


def test_an_awake_machine_gets_no_standby_nudge():
    """Pins the other side of the monitor gate: no nudge unless status is 0.

    Resolving the monitor datapoint (issue #14) revives this branch on machines
    where self.monitor used to stay empty forever, so both sides need a test.
    """
    client = _RecordingClient()
    coord = _coord("DL-striker-cb", connection_status="Online", client=client)
    coord.monitor = {"status": 7, "status_name": "ready"}

    asyncio.run(coord.async_send_wake())

    assert len(client.writes) == 1
    assert base64.b64decode(client.writes[0][2])[4:6].hex(" ") == "02 01"


def test_a_stale_offline_status_fails_open():
    """A frozen status must not keep blaming the machine.

    connection_status only moves on a successful poll. If the cloud (or the poll
    loop) breaks while it last said Offline, refusing forever would turn a cloud
    outage into a permanently dead integration - so past REACHABILITY_MAX_AGE
    the preflight steps aside.
    """
    client = _RecordingClient()
    coord = _coord("DL-millcore", connection_status="Offline", client=client)
    coord._device_seen_at = time.time() - const.REACHABILITY_MAX_AGE - 1

    assert coord.reachability_is_current is False
    asyncio.run(coord.async_send_wake())
    assert len(client.writes) == 1


def test_a_fresh_offline_status_still_blocks():
    coord = _coord("DL-millcore", connection_status="Offline", client=_RecordingClient())

    assert coord.reachability_is_current is True
    with pytest.raises(_StubHomeAssistantError):
        asyncio.run(coord.async_send_wake())


@pytest.mark.parametrize("status", ["Unknown", "", "Weird New Value", None])
def test_only_an_explicit_offline_blocks(status):
    client = _RecordingClient()
    coord = _coord("DL-millcore", connection_status=status, client=client)

    assert coord.machine_is_offline is False
    asyncio.run(coord.async_send_wake())
    assert len(client.writes) == 1


def test_offline_detection_is_case_insensitive():
    assert _coord("DL-millcore", connection_status="offline").machine_is_offline
    assert _coord("DL-millcore", connection_status="OFFLINE").machine_is_offline


def test_a_poll_refreshes_the_status_the_guard_reads():
    """End-to-end: the machine comes back and the next poll unblocks commands.

    The whole guard rests on _async_update_data re-listing the device, so that
    is what this drives - not a hand-assigned coord.device, which would pass
    even if the poll never refreshed anything.
    """
    client = _PollingClient(connection_status="Offline")
    coord = _coord("DL-millcore", connection_status="Offline", client=client)
    with pytest.raises(_StubHomeAssistantError):
        asyncio.run(coord.async_send_wake())

    client.connection_status = "Online"
    client.connected_at = "2026-08-22T09:15:00Z"
    asyncio.run(coord._async_update_data())

    assert coord.device.connection_status == "Online"
    assert coord.device.connected_at == "2026-08-22T09:15:00Z"
    asyncio.run(coord.async_send_wake())
    assert len(client.writes) == 1


def test_a_poll_that_never_succeeds_leaves_the_status_stale():
    """The mirror case: no refresh means the guard must eventually fail open."""
    coord = _coord("DL-millcore", connection_status="Offline", client=_RecordingClient())
    coord._device_seen_at = time.time() - const.REACHABILITY_MAX_AGE - 1

    assert coord.machine_is_offline is True
    assert coord.reachability_is_current is False


# --- monitor datapoint resolution (issue #14) -------------------------------
#
# SOUL_MONITOR_BLOB is a real d302_monitor value read from the reference
# PrimaDonna Soul (ECAM 612.55.SB, dsn AC000W019023280) on 2026-08-22: this
# machine publishes d302_monitor, while MONITOR_PROPERTY used to be hard-wired
# to d302_monitor_machine - which is why Machine Status stayed unknown forever
# on that family. The blob itself decodes fine with the existing parser.

SOUL_MONITOR_BLOB = "0BJ1DwRAAgUAAAJkAAAAAACSz2p8UbM="


def _monitor_props(**candidates):
    return {name: {"value": value} for name, value in candidates.items()}


def _build_monitor_blob(contents: bytes) -> str:
    """A valid MonitorV2 packet around a contents block (same envelope as the real ones).

    Mirrors the builder in test_monitor.py: <0x0d> <length> <data> <crc16> <ts>,
    data = [0x75, subid, *contents], CRC over raw[0:length-1].
    """
    data = bytes([0x75, 0x00]) + contents
    body = bytes([0x0D, len(data) + 3]) + data
    raw = body + cb.crc16_aug_ccitt(body).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    return base64.b64encode(raw).decode()


# Synthetic, not a field capture: no real Eletta d302_monitor_machine value has
# been contributed. Built through the same envelope the machine uses so it
# exercises the real decode path - ready, 42% through, switches 0x0201.
ELETTA_MONITOR_BLOB = _build_monitor_blob(
    bytes([0x01, 0x01, 0x02, 0x00, 0x00, 0x07, 0x00, 42, 0x00, 0x00, 0x00, 0x00, 0x00])
)


def test_monitor_resolves_the_datapoint_this_model_publishes():
    coord = _coord("DL-millcore")

    coord._update_monitor(_monitor_props(d302_monitor=SOUL_MONITOR_BLOB))

    assert coord.monitor_property == "d302_monitor"
    assert coord.monitor["status_name"] == "standby"
    assert coord.monitor["progress"] == 100


def test_a_present_but_empty_candidate_does_not_win():
    """Exposing a datapoint proves nothing - the Soul carries an always-null one."""
    coord = _coord("DL-millcore")

    coord._update_monitor(
        {
            "d302_monitor_machine": {"value": None},
            "d302_monitor": {"value": SOUL_MONITOR_BLOB},
        }
    )

    assert coord.monitor_property == "d302_monitor"
    assert coord.monitor["status_name"] == "standby"


def test_candidate_priority_is_respected_when_both_carry_values():
    coord = _coord("DL-striker-cb")

    coord._update_monitor(
        _monitor_props(
            d302_monitor_machine=SOUL_MONITOR_BLOB, d302_monitor=SOUL_MONITOR_BLOB
        )
    )

    assert coord.monitor_property == "d302_monitor_machine"


def test_resolution_is_retried_until_a_value_shows_up():
    coord = _coord("DL-millcore")

    coord._update_monitor(_monitor_props(d302_monitor="   "))
    assert coord.monitor_property is None
    assert coord.monitor == {}

    coord._update_monitor(_monitor_props(d302_monitor=SOUL_MONITOR_BLOB))
    assert coord.monitor_property == "d302_monitor"


def test_the_resolved_name_is_remembered_when_a_poll_brings_nothing():
    coord = _coord("DL-millcore")
    coord._update_monitor(_monitor_props(d302_monitor=SOUL_MONITOR_BLOB))

    coord._update_monitor(_monitor_props(d302_monitor=""))

    assert coord.monitor_property == "d302_monitor"
    assert coord.monitor == {}


def test_a_candidate_that_carries_junk_never_wins_over_one_that_decodes():
    """Carrying bytes proves no more than being listed does.

    A stale or truncated blob on the priority candidate would otherwise lock the
    poll onto a datapoint that can never yield a status - Machine Status stuck
    on unknown, which is the very symptom of issue #14.
    """
    coord = _coord("DL-millcore")

    coord._update_monitor(
        _monitor_props(
            d302_monitor_machine="bm90IGEgbW9uaXRvciBibG9i",  # decodes, isn't MonitorV2
            d302_monitor=SOUL_MONITOR_BLOB,
        )
    )

    assert coord.monitor_property == "d302_monitor"
    assert coord.monitor["status_name"] == "standby"


def test_the_datapoint_moving_after_a_firmware_update_is_followed():
    coord = _coord("DL-striker-cb")
    coord._update_monitor(_monitor_props(d302_monitor_machine=ELETTA_MONITOR_BLOB))
    assert coord.monitor_property == "d302_monitor_machine"

    coord._update_monitor(
        _monitor_props(d302_monitor_machine=None, d302_monitor=SOUL_MONITOR_BLOB)
    )

    assert coord.monitor_property == "d302_monitor"
    assert coord.monitor["status_name"] == "standby"


def test_an_eletta_blob_on_its_own_datapoint_decodes_as_before():
    """Non-regression for the family that already had a working Machine Status."""
    coord = _coord("DL-striker-cb")

    coord._update_monitor(_monitor_props(d302_monitor_machine=ELETTA_MONITOR_BLOB))

    assert coord.monitor_property == "d302_monitor_machine"
    assert coord.monitor["status_name"] == "ready"
    assert coord.monitor["progress"] == 42
    assert coord.monitor["switches"] == 0x0201


def test_no_monitor_datapoint_at_all_is_harmless():
    coord = _coord("DL-millcore")

    coord._update_monitor({"software_version": {"value": "Millcore_demo 2.0.0"}})

    assert coord.monitor_property is None
    assert coord.monitor == {}


def test_a_corrupt_blob_never_breaks_the_poll():
    coord = _coord("DL-millcore")

    coord._update_monitor(_monitor_props(d302_monitor="not base64 at all !!"))

    assert "error" in coord.monitor


# --- the cloud's own view of the machine (Last Connected) -------------------


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeSession:
    """aiohttp.ClientSession.get(), enough for async_get_devices.

    ``timeout`` is accepted and recorded rather than ignored: every Ayla call is
    supposed to carry one, and a stub that silently swallowed the argument would
    let that guarantee rot.
    """

    def __init__(self, payload) -> None:
        self._payload = payload
        self.timeouts: list = []

    def get(self, url, headers=None, timeout=None):
        self.timeouts.append(timeout)
        return _FakeResponse(self._payload)

    def post(self, url, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        return _FakeResponse(self._payload)


def _authenticated_client(payload):
    client = ac.DelonghiAylaClient(_FakeSession(payload), "user@example.com", "secret")
    client._access_token = "token"
    client._expires_at = time.time() + 3600
    return client


def test_device_carries_the_cloud_connected_at():
    """AylaDevice must transport connected_at: it is the only honest last-seen.

    The device_connected datapoint the sensor used to read was two months stale
    on the reference machine while the cloud knew the real answer.
    """
    client = _authenticated_client(
        [
            {
                "device": {
                    "dsn": "AC000W019023280",
                    "product_name": "AC000W019023280",
                    "oem_model": "DL-millcore",
                    "model": "AY008ESP1",
                    "sw_version": "ADA 1.5.3",
                    "lan_ip": "192.168.130.63",
                    "connection_status": "Offline",
                    "connected_at": "2026-08-12T04:01:46Z",
                }
            }
        ]
    )

    devices = asyncio.run(client.async_get_devices())

    assert devices[0].connected_at == "2026-08-12T04:01:46Z"
    assert devices[0].connection_status == "Offline"


def test_a_device_record_without_connected_at_yields_empty_not_junk():
    client = _authenticated_client(
        [{"device": {"dsn": "X", "connection_status": "Online"}}]
    )

    assert asyncio.run(client.async_get_devices())[0].connected_at == ""


def test_last_connected_is_no_longer_wired_to_a_datapoint():
    """Guard against the row coming back: it is what made the sensor lie."""
    keys = {key for _candidates, key, _friendly, _icon in const.INFO_SENSORS}
    assert "last_connected" not in keys
    named = [name for candidates, *_rest in const.INFO_SENSORS for name in candidates]
    assert "device_connected" not in named
    assert "app_device_connected" not in named


# --- fan-out across the machines of one config entry ------------------------
#
# A service call reaches every machine of the entry. The failure that started
# this story was silent, but the ones that come back from Ayla are not: a 5xx or
# an expired token raises CloudError/AuthError, plain Exception subclasses. One
# machine failing - for any reason - must not cost the others their command.


def _failing_coord(exc: Exception):
    coord = _coord("DL-millcore")

    async def _boom(_self=None) -> None:
        raise exc

    coord.async_send_wake = _boom  # type: ignore[method-assign]
    return coord


def test_every_machine_is_attempted_before_the_error_surfaces():
    ok_a, ok_b = _coord("DL-millcore", client=_RecordingClient()), _coord(
        "DL-millcore", client=_RecordingClient()
    )
    boom = _failing_coord(ac.CloudError("Ayla 502"))

    with pytest.raises(ac.CloudError):
        asyncio.run(
            coordinator.async_send_to_all(
                [boom, ok_a, ok_b], lambda coord: coord.async_send_wake()
            )
        )

    assert len(ok_a.client.writes) == 1
    assert len(ok_b.client.writes) == 1


def test_a_cloud_error_is_not_swallowed():
    """The first error must reach the caller, whatever its class."""
    boom = _failing_coord(ac.AuthError("token expired"))

    with pytest.raises(ac.AuthError):
        asyncio.run(
            coordinator.async_send_to_all([boom], lambda coord: coord.async_send_wake())
        )


def test_the_offline_refusal_still_surfaces_through_the_fan_out():
    offline = _coord("DL-millcore", connection_status="Offline", client=_RecordingClient())
    online = _coord("DL-millcore", client=_RecordingClient())

    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(
            coordinator.async_send_to_all(
                [offline, online], lambda coord: coord.async_send_wake()
            )
        )

    assert err.value.translation_key == "machine_offline"
    assert len(online.client.writes) == 1
    assert offline.client.writes == []


def test_nothing_is_raised_when_every_machine_takes_it():
    coords = [_coord("DL-millcore", client=_RecordingClient()) for _ in range(3)]

    asyncio.run(
        coordinator.async_send_to_all(coords, lambda coord: coord.async_send_wake())
    )

    assert [len(coord.client.writes) for coord in coords] == [1, 1, 1]


def test_the_last_connected_sensor_is_registered_on_every_machine():
    """The const guard above only proves the datapoint row is gone.

    This pins the other half - that a sensor reading the cloud record replaced
    it - without needing a Home Assistant install to build the entity.
    """
    source = (PKG_DIR / "sensor.py").read_text(encoding="utf-8")

    assert "entities.append(DelonghiLastConnectedSensor(coord))" in source
    assert 'super().__init__(coord, "last_connected", "mdi:clock-outline")' in source
    assert "self.coordinator.device.connected_at" in source


# --- machine beverage catalogue (catalog.py, wired into the poll) ------------

catalog = _load("catalog", "catalog.py")


def _blob(family: bytes, payload: bytes) -> str:
    """A well-formed machine blob: d0 <len> <family> <payload> <crc16>."""
    body = family + payload
    frame = bytes([const.CMD_RESPONSE_PREFIX, len(body) + 3]) + body
    frame += cb.crc16_aug_ccitt(frame).to_bytes(2, "big")
    return base64.b64encode(frame).decode("ascii")


#: A machine declaring one custom slot (0xe6) with a per-profile recipe for it.
SLOT_PROPS = {
    "d028_rec_custom_1": {
        "value": _blob(
            catalog.FAMILY_DESCRIPTOR,
            bytes([0xE6, catalog.TAG_COFFEE_ML, 0, 20, 0, 80, 0, 120]),
        )
    },
    "d200_1_cstm_recipe_01": {
        "value": _blob(
            catalog.FAMILY_PROFILE_RECIPE,
            bytes([1, 0xE6, catalog.TAG_COFFEE_ML, 0x00, 0x50, catalog.TAG_INTENSITY, 3]),
        )
    },
}


def _slot_frame(bev_id: int = 0xE6) -> str:
    """A frame the *app* would send for a saved recipe: its own recipe bytes.

    Not ``build_beverage_command`` defaults - those are bytes this integration
    could have produced itself, which the sniffer refuses to learn on purpose
    (it would replace the "teach me from the app" prompt with a frame the
    machine ignores).
    """
    return cb.encode_command(
        cb.build_eletta_beverage_command(bev_id, 0x03, bytes.fromhex("01 00 50 02 03"))
    )


def test_a_poll_builds_the_catalogue_from_the_properties_it_already_has():
    """No extra request: discovery is a pure function of the poll's own result."""
    client = _PollingClient(props=dict(SLOT_PROPS))
    coord = _coord("DL-millcore", client=client)
    assert coord.catalog is None

    asyncio.run(coord._async_update_data())

    assert coord.catalog is not None
    assert 0xE6 in coord.catalog["beverages"]
    assert coord.catalog["beverages"][0xE6]["defined"] is True
    assert client.writes == [], "reading the catalogue must never write to the machine"


def test_an_unchanged_poll_does_not_rebuild_the_catalogue():
    """The fingerprint is what keeps the parse off the polls where nothing moved."""
    client = _PollingClient(props=dict(SLOT_PROPS))
    coord = _coord("DL-millcore", client=client)
    asyncio.run(coord._async_update_data())
    first = coord.catalog

    asyncio.run(coord._async_update_data())
    assert coord.catalog is first, "same bytes must not re-parse"

    # A recipe edited on the machine must be picked up, though - a cache that
    # cannot see a change is the failure mode this project keeps being bitten by.
    client.props = dict(SLOT_PROPS)
    client.props["d200_1_cstm_recipe_01"] = {
        "value": _blob(
            catalog.FAMILY_PROFILE_RECIPE,
            bytes([1, 0xE6, catalog.TAG_COFFEE_ML, 0x00, 0x64, catalog.TAG_INTENSITY, 3]),
        )
    }
    asyncio.run(coord._async_update_data())
    assert coord.catalog is not first
    assert coord.catalog["beverages"][0xE6]["profiles"][1][catalog.TAG_COFFEE_ML] == 0x64


def test_a_poll_with_no_readable_blobs_keeps_the_catalogue_it_had():
    """A blank poll must never look like a machine that lost its recipes."""
    client = _PollingClient(props=dict(SLOT_PROPS))
    coord = _coord("DL-millcore", client=client)
    asyncio.run(coord._async_update_data())
    known = coord.catalog

    client.props = {"software_version": {"value": "Millcore_demo 2.0.0"}}
    asyncio.run(coord._async_update_data())
    assert coord.catalog is known


def test_a_broken_catalogue_never_breaks_the_poll(monkeypatch):
    """Diagnostic-grade, like the monitor decode: the poll must still return."""
    client = _PollingClient(props=dict(SLOT_PROPS))
    coord = _coord("DL-millcore", client=client)
    monkeypatch.setattr(
        coordinator, "build_catalog", lambda props: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    props = asyncio.run(coord._async_update_data())
    assert props == SLOT_PROPS
    assert coord.catalog is None


def test_a_saved_recipe_frame_is_learned_because_the_machine_declares_it():
    """The bug: pressing "Perso 1" in the official app had its frame discarded.

    0xe6 is not in the hardcoded beverage list, so the only bytes that could
    ever reproduce that drink were dropped in silence. The catalogue built from
    the same poll is what makes the frame learnable.
    """
    frame = _slot_frame()
    coord = _coord("DL-striker-cb")
    _capture(coord, frame)
    assert coord.learned_start_frames == {}, "without a catalogue, still dropped"

    coord = _coord("DL-striker-cb")
    coord.catalog = catalog.build_catalog(SLOT_PROPS)
    _capture(coord, frame)
    assert coord.learned_start_frames == {0xE6: frame}
    assert coord._store.saves == 1, "and it is persisted, so it survives a restart"


def test_the_catalogue_is_built_before_the_frame_is_sniffed():
    """Ordering matters: a slot brewed on this very poll must be learnable now.

    If _update_catalog ran after _sniff_app_traffic, the first press of a saved
    recipe would always be lost and only the second would stick.
    """
    frame = _slot_frame()
    # Poll 1: the channel is seen for the first time (never a capture, by
    # design) and the machine has not published the slot yet.
    client = _PollingClient(
        props={"app_data_request": {"value": "AA==", "data_updated_at": "t0"}}
    )
    coord = _coord("DL-striker-cb", client=client)
    coord.command_property = "app_data_request"
    asyncio.run(coord._async_update_data())
    assert coord.catalog is None

    # Poll 2 brings the slot declaration and the frame together. Build the
    # catalogue after sniffing and this first press is lost.
    client.props = dict(SLOT_PROPS)
    client.props["app_data_request"] = {"value": frame, "data_updated_at": "t1"}
    asyncio.run(coord._async_update_data())
    assert coord.learned_start_frames == {0xE6: frame}


def test_an_id_no_one_declares_is_still_refused():
    """Widening the gate must not turn it into no gate at all."""
    frame = _slot_frame(bev_id=0x7F)
    coord = _coord("DL-striker-cb")
    coord.catalog = catalog.build_catalog(SLOT_PROPS)
    _capture(coord, frame)
    assert coord.learned_start_frames == {}

# --- Soul monitor-session keepalive (#14) -----------------------------------
#
# The field failure these pin down: on a PrimaDonna Soul (ECAM610.55,
# DL-millcore, ADA 1.5.3) `d302_monitor` had not moved in five days while the
# machine was in daily use, so Machine Status read `standby` throughout. Every
# poll succeeded and `connection_status` stayed `Online` - the stale value was in
# the cloud. The Soul publishes its monitor blob only when an app session is
# written to `device_connected`, and that happened once, at setup. Counters kept
# flowing the whole time, which is exactly what made it invisible.

def _soul_poll_props():
    props = _monitor_props(d302_monitor=SOUL_MONITOR_BLOB)
    props["device_connected"] = {"value": "1788209091"}
    return props


def _keepalives(client):
    return [w for w in client.writes if w[1] == "device_connected"]


def test_a_soul_poll_refreshes_the_monitor_session():
    """Without this write the machine simply stops publishing its status."""
    client = _PollingClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    asyncio.run(coord._async_update_data())

    assert coord.connected_property == "device_connected"
    assert len(_keepalives(client)) == 1
    # A plain unix timestamp - NOT the base64(ts + app_id) blob that ECAM's
    # app_device_connected takes (ayla_client.async_post_cloud_session). The two
    # payloads are not interchangeable.
    value = _keepalives(client)[0][2]
    assert isinstance(value, int)
    assert abs(value - int(time.time())) < 5


def test_the_keepalive_is_rate_limited():
    """Two polls inside one interval must still cost exactly one write."""
    client = _PollingClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    asyncio.run(coord._async_update_data())
    asyncio.run(coord._async_update_data())

    assert len(_keepalives(client)) == 1


def test_the_keepalive_comes_round_again_once_the_interval_lapses():
    client = _PollingClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)
    asyncio.run(coord._async_update_data())

    coord._last_monitor_session_at -= const.MONITOR_KEEPALIVE_INTERVAL + 1
    asyncio.run(coord._async_update_data())

    assert len(_keepalives(client)) == 2


def test_the_keepalive_interval_leaves_room_for_the_poll_that_carries_it():
    """The guard must not be able to reject the very poll it rides on.

    Equal to DEFAULT_SCAN_INTERVAL, `now - last < INTERVAL` is a coin flip: the
    keepalive is stamped a few hundred ms into a poll, so the *next* scheduled
    poll lands a hair under one interval later and is skipped.
    """
    assert const.MONITOR_KEEPALIVE_INTERVAL < const.DEFAULT_SCAN_INTERVAL


def test_a_poll_one_scan_interval_later_still_writes_the_keepalive():
    """The regression itself: successive writes were +61/+30/+31/+60 s, not +30.

    The gap here is a hair UNDER one scan interval, which is what a real polling
    loop produces - and is exactly what the old `< DEFAULT_SCAN_INTERVAL` guard
    rejected, deferring the write a whole cycle and halving the status
    resolution.
    """
    client = _PollingClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)
    asyncio.run(coord._async_update_data())
    assert len(_keepalives(client)) == 1

    coord._last_monitor_session_at -= const.DEFAULT_SCAN_INTERVAL - 0.05
    asyncio.run(coord._async_update_data())

    assert len(_keepalives(client)) == 2, (
        "a poll one scan interval later must carry a keepalive; "
        "it was being skipped every other cycle"
    )


def test_eletta_polls_do_not_get_the_soul_keepalive():
    """Eletta takes its session per command through app_device_connected; a
    periodic timestamp write there would fight the official app for it."""
    props = _monitor_props(d302_monitor_machine=ELETTA_MONITOR_BLOB)
    props["device_connected"] = {"value": "1788209091"}
    client = _PollingClient(props=props)
    coord = _coord("DL-striker-cb", client=client)

    asyncio.run(coord._async_update_data())

    assert _keepalives(client) == []


def test_a_failed_keepalive_never_fails_the_poll():
    """A missed keepalive costs one stale poll. If it propagated, a single cloud
    hiccup would blank every entity instead."""

    class _RefusingClient(_PollingClient):
        async def async_set_property_value(self, dsn, prop, value):
            raise RuntimeError("cloud refused the datapoint write")

    coord = _coord("DL-millcore", client=_RefusingClient(props=_soul_poll_props()))

    props = asyncio.run(coord._async_update_data())

    assert props
    assert coord.monitor["status_name"] == "standby"


# --- keepalive health: the failure must not be quiet ------------------------
#
# A keepalive that fails every time rebuilds the exact blind spot it exists to
# close: polls green, machine Online, counters moving, Machine Status frozen.
# The write is a plain POST that never enters the transient-retry helper, so
# nothing else in the stack would report it either.


class _RefusingKeepaliveClient(_PollingClient):
    """Fails the connected-property write, answers every poll normally."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.refuse = True

    async def async_set_property_value(self, dsn, prop, value):
        if prop == "device_connected" and self.refuse:
            raise RuntimeError("cloud refused the datapoint write")
        return await super().async_set_property_value(dsn, prop, value)


def test_the_first_keepalive_failure_warns_and_the_repeats_do_not(caplog):
    """Loud once, then quiet: a warning per poll would be its own kind of noise."""
    client = _RefusingKeepaliveClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    with caplog.at_level(logging.WARNING, logger=coordinator.__name__):
        asyncio.run(coord._async_update_data())
        first = [r for r in caplog.records if "keepalive failed" in r.message]
        assert len(first) == 1, "the first failure must be visible"
        assert "Machine Status will freeze" in first[0].message

        caplog.clear()
        coord._last_monitor_session_at -= const.MONITOR_KEEPALIVE_INTERVAL + 1
        asyncio.run(coord._async_update_data())
        assert [r for r in caplog.records if "keepalive failed" in r.message] == []
    assert coord._keepalive_failures == 2


def test_a_failure_is_retried_on_the_next_poll_not_deferred(caplog):
    """The rate-limit stamp must only advance on a write that landed.

    Stamping it before the attempt would turn every failure into a full interval
    of guaranteed silence, which is the opposite of what a keepalive is for.
    """
    client = _RefusingKeepaliveClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    asyncio.run(coord._async_update_data())
    assert coord._last_monitor_session_at == 0, "a failed write leaves the clock alone"

    client.refuse = False
    asyncio.run(coord._async_update_data())
    assert len(_keepalives(client)) == 1, "the very next poll retries, no waiting"
    assert coord._last_monitor_session_at > 0


def test_recovery_is_announced(caplog):
    """Coming back matters as much as breaking: the log has to close the loop."""
    client = _RefusingKeepaliveClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    with caplog.at_level(logging.WARNING, logger=coordinator.__name__):
        asyncio.run(coord._async_update_data())
        client.refuse = False
        caplog.clear()
        asyncio.run(coord._async_update_data())

    recovered = [r for r in caplog.records if "recovered" in r.message]
    assert len(recovered) == 1
    assert coord._keepalive_failures == 0


def test_arming_the_keepalive_is_announced_once(caplog):
    """The integration starts writing to the machine every 15 s; say so.

    Once, at INFO, naming the property and the cadence - so a user reading their
    log can tell this traffic is ours and deliberate.
    """
    client = _PollingClient(props=_soul_poll_props())
    coord = _coord("DL-millcore", client=client)

    with caplog.at_level(logging.INFO, logger=coordinator.__name__):
        asyncio.run(coord._async_update_data())
        coord._last_monitor_session_at -= const.MONITOR_KEEPALIVE_INTERVAL + 1
        asyncio.run(coord._async_update_data())

    armed = [r for r in caplog.records if "Keeping the monitor session alive" in r.message]
    assert len(armed) == 1, "announced on arming, not on every write"
    assert "device_connected" in armed[0].message


def test_an_unknown_soul_like_machine_is_never_written_to(caplog):
    """The keepalive payload is confirmed on DL-millcore and nowhere else.

    An unrecognised machine on the data_request channel keeps the Soul command
    dialect, which does generalise, and loses only the periodic write - which
    would otherwise put a bare timestamp into the property the official app uses
    to register its own session, 2880 times a day, on hardware nobody has tested.
    """
    props = _soul_poll_props()
    props["data_request"] = {"value": "AA==", "data_updated_at": "t0"}
    client = _PollingClient(props=props)
    coord = _coord("DL-future-xyz", client=client)
    coord.command_property = None  # let the poll detect the channel, as it does live

    asyncio.run(coord._async_update_data())

    assert coord.profile.key == "soul-generic", "the channel says Soul-like"
    assert _keepalives(client) == []
    assert coord._keepalive_armed is False


class _StallingSession:
    """A session whose request never answers, the way a wedged gateway behaves."""

    def request(self, *args, **kwargs):
        raise TimeoutError("total timeout expired")


def test_a_timeout_surfaces_as_a_cloud_error_not_a_raw_timeout():
    """`TimeoutError` is not an `aiohttp.ClientError`, and every caller here
    was built to expect `CloudError`.

    `ServerTimeoutError` is a ClientError, which makes this easy to get wrong:
    it covers the connect phase only, so a *total* timeout on a socket that
    connected and then went quiet is a plain builtin. Catching only
    `aiohttp.ClientError` would let it past the retry loop and past
    `_fetch_app_id_live`, which degrades a CloudError cleanly and would simply
    propagate this one.
    """
    client = ac.DelonghiAylaClient(_StallingSession(), "user@example.com", "secret")
    client._access_token = "token"
    client._expires_at = time.time() + 3600

    with pytest.raises(ac.CloudError):
        asyncio.run(client.async_get_property_resilient("DSN", "app_id"))


def test_every_ayla_read_is_bounded_by_a_timeout():
    """The session comes from Home Assistant and carries no total timeout.

    Without an explicit one, a request the far end accepts and never answers
    hangs as long as it likes. That was survivable while writes only happened on
    a user action; the monitor keepalive now writes every 15 seconds.
    """
    session = _FakeSession([])
    client = ac.DelonghiAylaClient(session, "user@example.com", "secret")
    client._access_token = "token"
    client._expires_at = time.time() + 3600

    asyncio.run(client.async_get_devices())
    assert session.timeouts, "the call must pass a timeout"

    # The auth chain too, and it is the one that matters most: it runs before
    # every read and every write, so an unbounded login stalls all of them. It
    # was also the one call site the first pass at this missed.
    session.timeouts.clear()
    client._access_token = None
    session._payload = {"sessionInfo": {"cookieValue": "x"}, "id_token": "y", "access_token": "z"}
    try:
        asyncio.run(client.async_authenticate())
    except Exception:
        pass  # the stub cannot complete the chain; what matters is what it recorded
    assert session.timeouts, "the auth chain must pass a timeout too"

    assert all(t is not None and t.total == const.CLOUD_HTTP_TIMEOUT for t in session.timeouts)


def test_no_call_site_is_left_without_a_timeout():
    """Belt to the braces: a new call added without one fails here.

    The per-call kwarg is easy to forget - the first pass at this covered seven
    of eight sites - so the source is checked directly rather than relying on a
    test happening to exercise every path.
    """
    import re

    source = (PKG_DIR / "ayla_client.py").read_text(encoding="utf-8")
    calls = re.findall(r"self\._session\.(?:get|post|request)\((.*?)\) as resp", source, re.S)
    assert len(calls) == 8, f"expected 8 call sites, found {len(calls)}"
    for args in calls:
        assert "timeout=_TIMEOUT" in args, f"unbounded Ayla call: {args[:80]}"


# --- user profile: read-back and switch (select "Profile") -------------------
#
# The official app switches the machine's active profile with one a9f0 frame
# and the machine answers on the response channel, where the reply then stays.
# fixtures/soul_properties.json caught both halves of one such exchange:
#   data_request  = 0d 06 a9 f0 01 d7 c0 69 e8 c5 ee   (app -> machine, slot 1)
#   data_response = d0 07 a9 f0 01 00 3b 3c 69 e8 c5 f0 (machine -> app, ok, +2 s)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "soul_properties.json"
FIXTURE_REPLY_TS = 0x69E8C5F0


def _fixture_props() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _profile_reply(profile: int, status: int, ts: int | None) -> str:
    """The machine's answer: d0 07 a9 f0 <p> <status> <crc16> [<ts 4B>]."""
    head = bytes([const.CMD_RESPONSE_PREFIX, 0x07, *const.CMD_FAMILY_PROFILE, profile, status])
    raw = head + cb.crc16_aug_ccitt(head).to_bytes(2, "big")
    if ts is not None:
        raw += ts.to_bytes(4, "big")
    return base64.b64encode(raw).decode("ascii")


def test_the_reply_builder_reproduces_the_fixture():
    """Guards every test below: the synthetic replies have the machine's shape."""
    assert _profile_reply(1, 0, FIXTURE_REPLY_TS) == _fixture_props()["data_response"]["value"]


def _soul_with_catalog(client=None, connection_status: str = "Online"):
    coord = _coord("DL-millcore", connection_status=connection_status, client=client)
    coord.catalog = catalog.build_catalog(_fixture_props())
    coord.response_property = "data_response"
    return coord


def _profile_writes(client) -> list[str]:
    return [value for _dsn, prop, value in client.writes if prop == "data_request"]


def test_soul_sends_the_app_frame_for_the_chosen_slot():
    """Byte parity with the app: only the profile byte and the CRC move."""
    client = _RecordingClient()
    coord = _soul_with_catalog(client)

    asyncio.run(coord.async_send_profile(3))

    assert len(client.writes) == 1
    dsn, prop, value = client.writes[0]
    assert (dsn, prop) == (coord.device.dsn, "data_request")
    assert base64.b64decode(value)[:7].hex(" ") == "0d 06 a9 f0 03 f7 82"
    assert value in coord._sent_values  # the echo must not read as app traffic
    assert coord.active_user_profile == 3  # optimistic, until the machine answers
    assert coord.profile_change_pending is True


@pytest.mark.parametrize("slot", [1, 2, 3, 4, 5])
def test_every_fixture_slot_has_the_pinned_crc(slot):
    crcs = {1: "d7 c0", 2: "e7 a3", 3: "f7 82", 4: "87 65", 5: "97 44"}
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    asyncio.run(coord.async_send_profile(slot))
    assert base64.b64decode(client.writes[0][2])[5:7].hex(" ") == crcs[slot]


def test_a_slot_the_machine_never_declared_is_refused_before_any_write():
    client = _RecordingClient()
    coord = _soul_with_catalog(client)

    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(coord.async_send_profile(9))

    assert err.value.translation_key == "unknown_profile"
    assert err.value.translation_domain == const.DOMAIN
    assert err.value.translation_placeholders == {
        "name": "Coffee Maker", "profile": "9", "known": "1, 2, 3, 4, 5",
    }
    assert client.writes == []
    assert coord.active_user_profile is None
    assert coord.profile_change_pending is False


def test_without_a_catalogue_the_machine_decides():
    """No slot list means no gate - refusing would make the select unusable on a
    machine whose recipe blobs were simply unreadable."""
    client = _RecordingClient()
    coord = _coord("DL-millcore", client=client)
    assert coord.user_profile_slots() == []

    asyncio.run(coord.async_send_profile(9))

    assert base64.b64decode(client.writes[0][2])[4] == 9


def test_a_profile_byte_the_wire_cannot_carry_is_refused_the_same_way():
    client = _RecordingClient()
    coord = _coord("DL-millcore", client=client)
    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(coord.async_send_profile(300))
    assert err.value.translation_key == "unknown_profile"
    assert client.writes == []


def test_a_poll_reads_the_active_profile_from_the_reply_left_on_the_channel():
    """The value present at startup counts: the sniffer skips it, this must not."""
    client = _PollingClient(props=_fixture_props())
    coord = _coord("DL-millcore", client=client)

    asyncio.run(coord._async_update_data())

    assert coord.response_property == "data_response"
    assert coord.user_profile_slots() == [1, 2, 3, 4, 5]
    assert coord.active_user_profile == 1
    assert coord.active_user_profile_at == FIXTURE_REPLY_TS
    assert coord.profile_change_pending is False


def test_a_second_identical_poll_changes_nothing():
    client = _PollingClient(props=_fixture_props())
    coord = _coord("DL-millcore", client=client)
    asyncio.run(coord._async_update_data())
    asyncio.run(coord._async_update_data())
    assert (coord.active_user_profile, coord.active_user_profile_at) == (1, FIXTURE_REPLY_TS)


def _poll_reply(coord, value: str) -> None:
    """Drive one reply through the profile reader with the fixture's catalogue."""
    props = _fixture_props()
    props["data_response"] = {"value": value}
    coord._update_active_profile(props)


def test_a_refusal_we_did_not_ask_for_leaves_the_profile_alone():
    coord = _soul_with_catalog()
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    _poll_reply(coord, _profile_reply(2, 1, FIXTURE_REPLY_TS + 60))
    assert coord.active_user_profile == 1
    assert coord.active_user_profile_at == FIXTURE_REPLY_TS


def test_a_reply_for_a_slot_the_machine_does_not_declare_is_not_believed():
    """Unknown must never read as a confident value."""
    coord = _soul_with_catalog()
    _poll_reply(coord, _profile_reply(9, 0, FIXTURE_REPLY_TS))
    assert coord.active_user_profile is None
    assert coord.active_user_profile_at is None


def test_a_reply_without_a_catalogue_is_the_only_evidence_and_is_taken():
    """No catalogue means no gate on either side: the machine decides what we
    send, and its CRC-valid reply is all there is to read back. Refusing it
    would leave a switch sent through the coordinator pending forever."""
    coord = _coord("DL-millcore")
    coord.response_property = "data_response"
    coord._update_active_profile({"data_response": {"value": _profile_reply(1, 0, None)}})
    assert coord.active_user_profile == 1


def _soul_with_names(client=None):
    """Three named profiles, five witnessed slots - the reference machine."""
    coord = _soul_with_catalog(client)
    coord.catalog["names"]["profiles"] = {1: "Anna", 2: "Bertil", 3: "Guest"}
    coord.catalog["profile_slots"] = [1, 2, 3]
    return coord


def test_a_reply_for_a_slot_the_display_does_not_offer_is_ignored():
    """Slot 4 is witnessed (recipes, priority list) but unnamed, so the select
    does not list it; accepting a reply for it would put the entity in a state
    its own option list lacks."""
    coord = _soul_with_names()
    assert coord.user_profile_slots() == [1, 2, 3, 4, 5]
    assert sorted(coord.user_profile_labels()) == [1, 2, 3]
    _poll_reply(coord, _profile_reply(3, 0, FIXTURE_REPLY_TS))
    _poll_reply(coord, _profile_reply(4, 0, FIXTURE_REPLY_TS + 60))
    assert coord.active_user_profile == 3


def test_a_switch_to_a_slot_the_display_does_not_offer_is_refused():
    client = _RecordingClient()
    coord = _soul_with_names(client)
    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(coord.async_send_profile(4))
    assert err.value.translation_placeholders["known"] == "1, 2, 3"
    assert client.writes == []


def test_a_switch_nobody_acknowledges_is_rolled_back_after_the_timeout():
    """A machine that never answers must not leave an unconfirmed profile on
    display, marked pending until the end of time."""
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))
    coord._profile_request_ts -= const.PROFILE_REPLY_TIMEOUT + 1

    # The channel still shows the old reply (stale, ignored) - or a brew ack.
    _poll_reply(coord, "AA==")

    assert coord.active_user_profile == 1
    assert coord.profile_change_pending is False


def test_a_second_switch_keeps_the_original_fallback():
    """Select 3, then 4, before any reply: a refusal of 4 must fall back to the
    profile the machine actually had, never to the unconfirmed 3."""
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))
    asyncio.run(coord.async_send_profile(4))
    assert coord.active_user_profile == 4

    _poll_reply(coord, _profile_reply(4, 1, coord._profile_request_ts + 2))

    assert coord.active_user_profile == 1
    assert coord.profile_change_pending is False


def test_the_unknown_profile_error_never_lists_nothing():
    coord = _coord("DL-millcore")
    err = coord.unknown_profile_error(300, [])
    assert err.translation_placeholders["known"] == "none declared"


def test_a_stale_reply_does_not_override_an_optimistic_switch():
    """The old reply is still on the channel for a poll or two after we write."""
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))
    assert coord._profile_request_ts > FIXTURE_REPLY_TS

    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))

    assert coord.active_user_profile == 3
    assert coord.profile_change_pending is True


def test_a_reply_from_a_slightly_slow_machine_clock_still_confirms():
    """The machine stamps its reply with its own clock; a few seconds behind the
    host must not turn a genuine acknowledgement into a "stale" one, or the
    switch would stay pending until the next unrelated reply."""
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))
    slow_clock = coord._profile_request_ts - const.PROFILE_REPLY_CLOCK_SKEW + 5

    _poll_reply(coord, _profile_reply(3, 0, slow_clock))

    assert coord.active_user_profile == 3
    assert coord.active_user_profile_at == slow_clock
    assert coord.profile_change_pending is False


def test_a_fresh_confirmation_clears_the_pending_switch():
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))
    reply_ts = coord._profile_request_ts + 2

    _poll_reply(coord, _profile_reply(3, 0, reply_ts))

    assert coord.active_user_profile == 3
    assert coord.active_user_profile_at == reply_ts
    assert coord.profile_change_pending is False
    assert coord._profile_before_request is None


def test_a_fresh_refusal_rolls_the_switch_back(caplog):
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    _poll_reply(coord, _profile_reply(1, 0, FIXTURE_REPLY_TS))
    asyncio.run(coord.async_send_profile(3))

    with caplog.at_level(logging.WARNING):
        _poll_reply(coord, _profile_reply(3, 1, coord._profile_request_ts + 2))

    assert coord.active_user_profile == 1
    assert coord.active_user_profile_at == FIXTURE_REPLY_TS  # untouched by the refusal
    assert coord.profile_change_pending is False
    assert any(
        "refused user profile 3 (status 1)" in rec.getMessage() for rec in caplog.records
    )


def test_a_refusal_of_a_switch_from_nothing_rolls_back_to_nothing():
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    asyncio.run(coord.async_send_profile(2))
    _poll_reply(coord, _profile_reply(2, 1, coord._profile_request_ts + 1))
    assert coord.active_user_profile is None
    assert coord.profile_change_pending is False


def test_another_family_on_the_shared_channel_keeps_the_last_profile():
    """The response channel also carries brew acks; those say nothing about profiles."""
    coord = _soul_with_catalog()
    _poll_reply(coord, _profile_reply(2, 0, FIXTURE_REPLY_TS))
    brew_ack = bytes([const.CMD_RESPONSE_PREFIX, 0x05, 0x83, 0xF0, 0x01])
    brew_ack += cb.crc16_aug_ccitt(brew_ack).to_bytes(2, "big")
    _poll_reply(coord, base64.b64encode(brew_ack).decode("ascii"))
    _poll_reply(coord, "AA==")
    _poll_reply(coord, "not base64 at all")
    assert coord.active_user_profile == 2
    assert coord.active_user_profile_at == FIXTURE_REPLY_TS


def test_a_reply_stamped_with_no_timestamp_is_still_accepted():
    """Only 8 bytes come back on some frames; the stale guard then cannot apply."""
    coord = _soul_with_catalog()
    _poll_reply(coord, _profile_reply(4, 0, None))
    assert coord.active_user_profile == 4
    assert coord.active_user_profile_at is None


def test_a_broken_profile_reader_never_breaks_the_poll(monkeypatch):
    client = _PollingClient(props=_fixture_props())
    coord = _coord("DL-millcore", client=client)
    monkeypatch.setattr(
        coordinator, "parse_profile_response",
        lambda value: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    props = asyncio.run(coord._async_update_data())
    assert props is client.props
    assert coord.active_user_profile is None


def test_the_setting_datapoint_is_only_a_fallback_without_a_response_channel():
    """d286_mach_sett_profile does not exist on the Soul; where it does, only a
    plain int in a declared slot is believed - never a string or blob."""
    coord = _coord("DL-millcore")
    coord.catalog = catalog.build_catalog(_fixture_props())
    assert coord.response_property is None

    coord._update_active_profile({"d286_mach_sett_profile": {"value": "2"}})
    assert coord.active_user_profile is None
    coord._update_active_profile({"d286_mach_sett_profile": {"value": True}})
    assert coord.active_user_profile is None
    coord._update_active_profile({"d286_mach_sett_profile": {"value": 9}})
    assert coord.active_user_profile is None
    coord._update_active_profile({"d286_mach_sett_profile": {"value": 2}})
    assert coord.active_user_profile == 2
    assert coord.active_user_profile_at is None

    # With a response channel the datapoint is not consulted at all.
    coord.response_property = "data_response"
    coord._update_active_profile({"d286_mach_sett_profile": {"value": 4}})
    assert coord.active_user_profile == 2


def test_eletta_profile_frame_carries_the_machine_signature():
    """Built like standby: the learned device signature rides in the tail."""
    coord = _coord("DL-striker-cb")
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    value = coord._profile_command_value(2, 0x69E8C5EE)
    raw = base64.b64decode(value)
    assert raw[:7].hex(" ") == "0d 06 a9 f0 02 e7 a3"
    assert raw[7:11] == (0x69E8C5EE).to_bytes(4, "big")
    assert raw[-4:] == COFFEE_SIGNATURE
    assert len(raw) == 15


def test_eletta_online_switch_goes_through_the_command_channel():
    client = _RecordingClient()
    coord = _coord("DL-striker-cb", client=client)
    coord._maybe_learn_frame(_decoded(COFFEE_FRAME))
    coord.command_property = "app_data_request"
    coord._last_connect_at = time.time()  # a fresh session: no cold connect needed

    asyncio.run(coord.async_send_profile(2))

    profile_writes = [w for w in client.writes if w[1] == "app_data_request"]
    assert len(profile_writes) == 1
    raw = base64.b64decode(profile_writes[0][2])
    assert raw[:5].hex(" ") == "0d 06 a9 f0 02"
    assert raw[-4:] == COFFEE_SIGNATURE
    assert coord.active_user_profile == 2
    assert coord.profile_change_pending is True


def test_an_app_profile_frame_is_captured_but_never_learned_as_a_beverage():
    coord = _coord("DL-striker-cb")
    frame = cb.build_profile_encoded(3, timestamp=0x69E8C5EE, signature=COFFEE_SIGNATURE)
    _capture(coord, frame)
    assert coord.last_captured_command["type"] == "profile"
    assert coord.last_captured_command["profile"] == 3
    assert coord.last_captured_command["origin"] == "app"
    assert coord.last_captured_command["matches_integration"] is True
    assert coord.learned_start_frames == {}
    assert coord.learned_stop_frames == {}
    assert coord.learned_wake_frame is None


# --- select entity -----------------------------------------------------------

def test_select_options_are_the_machine_labels():
    coord = _soul_with_catalog()
    entity = select.DelonghiUserProfileSelect(coord)
    assert entity.options == ["Profile 1", "Profile 2", "Profile 3", "Profile 4", "Profile 5"]
    assert entity._attr_unique_id == f"{coord.device.dsn}_user_profile"
    assert entity._attr_translation_key == "user_profile"
    assert entity._attr_entity_category == "config"


def test_select_state_is_unknown_until_the_machine_has_answered():
    coord = _soul_with_catalog()
    entity = select.DelonghiUserProfileSelect(coord)
    assert entity.current_option is None
    attrs = entity.extra_state_attributes
    assert attrs == {
        "profile_slot": None,
        "profile_read_at": None,
        "profile_slots": [1, 2, 3, 4, 5],
        "pending": False,
    }

    _poll_reply(coord, _profile_reply(2, 0, FIXTURE_REPLY_TS))

    assert entity.current_option == "Profile 2"
    assert entity.extra_state_attributes["profile_slot"] == 2
    assert entity.extra_state_attributes["profile_read_at"] == FIXTURE_REPLY_TS


def test_select_never_shows_a_slot_the_labels_do_not_know():
    coord = _soul_with_catalog()
    entity = select.DelonghiUserProfileSelect(coord)
    coord.active_user_profile = 9  # cannot happen through the reader; belt and braces
    assert entity.current_option is None


def test_selecting_an_option_sends_that_slot():
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    entity = select.DelonghiUserProfileSelect(coord)

    asyncio.run(entity.async_select_option("Profile 3"))

    assert len(client.writes) == 1
    assert base64.b64decode(client.writes[0][2])[4] == 3
    assert entity.current_option == "Profile 3"
    assert entity.extra_state_attributes["pending"] is True


def test_selecting_an_unknown_option_is_refused_without_a_write():
    client = _RecordingClient()
    coord = _soul_with_catalog(client)
    entity = select.DelonghiUserProfileSelect(coord)
    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(entity.async_select_option("Guest"))
    assert err.value.translation_key == "unknown_profile"
    assert err.value.translation_placeholders["profile"] == "Guest"
    assert client.writes == []


def test_select_stays_available_across_a_failed_poll():
    coord = _soul_with_catalog()
    coord.last_update_success = False
    assert select.DelonghiUserProfileSelect(coord).available is True


def test_an_available_select_still_refuses_an_offline_machine():
    coord = _soul_with_catalog(_RecordingClient(), connection_status="Offline")
    entity = select.DelonghiUserProfileSelect(coord)
    with pytest.raises(_StubHomeAssistantError) as err:
        asyncio.run(entity.async_select_option("Profile 2"))
    assert err.value.translation_key == "machine_offline"
    assert coord.client.writes == []
    assert coord.active_user_profile is None


def test_setup_adds_one_select_per_machine_that_declares_profiles():
    with_catalog = _soul_with_catalog()
    without_catalog = _coord("DL-millcore")
    entry = types.SimpleNamespace(entry_id="entry-1")
    hass = types.SimpleNamespace(
        data={const.DOMAIN: {entry.entry_id: [with_catalog, without_catalog]}}
    )
    added: list = []

    asyncio.run(select.async_setup_entry(hass, entry, added.extend))

    assert len(added) == 1
    assert isinstance(added[0], select.DelonghiUserProfileSelect)
    assert added[0].coordinator is with_catalog
