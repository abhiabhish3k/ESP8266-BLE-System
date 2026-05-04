#!/usr/bin/env python3
"""
BLE Beacon Advertiser for ESP8266 Attendance System
====================================================

Advertises a stable Beacon ID as a BLE local name so that the ESP8266
Attendance Android app can detect this device and mark the student present.

Because modern phones randomise their Bluetooth MAC address the system uses
the BLE *local name* as the device identifier instead.  Any device name that
starts with the "ATT-" prefix is treated as a Beacon ID by the Android scanner
(BLEScanner.kt) and sent verbatim to the ESP8266 as the ``device_id``.

Workflow
--------
1. Run this script:
       python advertiser.py --beacon-id ATT-john-doe

2. Register the student in the ESP8266 web dashboard → Students tab:
       Name      : John Doe
       Device ID : ATT-john-doe      ← exact string shown at startup

3. Start an attendance session (dashboard → Session tab).

4. Keep this script running while in the classroom.
   The Android scanner will detect the beacon and mark you present.

Requirements
------------
    pip install -r requirements.txt   (or:  pip install bless>=0.3.0)

Platforms
---------
    Linux   – BlueZ ≥ 5.43 required (most distros ship a newer version)
    macOS   – CoreBluetooth (no extra driver needed)
    Windows – WinRT Bluetooth (Windows 10 1803+)

    Raspberry Pi is the recommended portable device.
"""

import argparse
import asyncio
import logging
import signal
import sys

try:
    from bless import BlessServer
except ImportError:
    sys.exit(
        "Error: 'bless' is not installed.\n"
        "Install it with:  pip install bless>=0.3.0\n"
        "or:               pip install -r requirements.txt"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEACON_PREFIX = "ATT-"

# BLE local name is limited to ~29 bytes; keep the total well under that limit.
MAX_TOTAL_LEN = 29

# Custom GATT service UUID that identifies this as an attendance beacon.
# This is a randomly generated proprietary UUID chosen for this application;
# it does not correspond to any standard Bluetooth SIG service.
# The Android scanner does not need to read this service; it is included only
# to produce a well-formed GATT advertisement on all platforms.
ATTENDANCE_SERVICE_UUID = "a77e0001-d2c9-4d6b-9f2a-3c8e1b4f5d60"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="advertiser.py",
        description="BLE beacon advertiser for the ESP8266 Attendance System.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  # Advertise as ATT-john-doe
  python advertiser.py --beacon-id ATT-john-doe

  # Advertise as ATT-S12345 with debug output
  python advertiser.py --beacon-id ATT-S12345 --verbose

Registration steps (after the script starts)
--------------------------------------------
  1. Open the ESP8266 web dashboard in a browser.
  2. Go to the Students tab.
  3. Click "Register student".
  4. Enter:  Name      = <your full name>
             Device ID = ATT-john-doe   ← the exact Beacon ID printed at startup
  5. Save. The student is now enrolled.

Attendance
----------
  Keep this script running during class.  The Android scanner detects the
  beacon name and reports it to the ESP8266 as the device_id.  The ESP8266
  looks up that string in its student list and marks you present.
""",
    )
    p.add_argument(
        "--beacon-id",
        required=True,
        metavar="BEACON_ID",
        help=(
            'Stable ID to advertise over BLE. Must start with "ATT-" '
            '(e.g. ATT-john-doe, ATT-S12345).  '
            "Use this exact string as device_id when registering in the dashboard."
        ),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug-level logging from bless/bleak.",
    )
    return p


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_beacon_id(beacon_id: str) -> None:
    """Raise SystemExit with a friendly message when the beacon ID is invalid."""
    if not beacon_id.startswith(BEACON_PREFIX):
        sys.exit(
            f'Error: Beacon ID must start with "{BEACON_PREFIX}".\n'
            f'  Good: {BEACON_PREFIX}john-doe\n'
            f'  Good: {BEACON_PREFIX}S12345\n'
            f'  Bad : {beacon_id!r}'
        )

    suffix = beacon_id[len(BEACON_PREFIX):]
    if not suffix:
        sys.exit(
            f'Error: Beacon ID must have a non-empty suffix after "{BEACON_PREFIX}".\n'
            f"  Example: {BEACON_PREFIX}john-doe"
        )

    if len(beacon_id) > MAX_TOTAL_LEN:
        sys.exit(
            f"Error: Beacon ID is too long ({len(beacon_id)} chars, max {MAX_TOTAL_LEN}).\n"
            f"  Shorten the part after \"{BEACON_PREFIX}\"."
        )

    allowed = (
        set("abcdefghijklmnopqrstuvwxyz")
        | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        | set("0123456789")
        | set("-_")
    )
    bad_chars = set(beacon_id) - allowed
    if bad_chars:
        sys.exit(
            f"Error: Beacon ID contains invalid characters: {sorted(bad_chars)}\n"
            "  Allowed: letters (a-z, A-Z), digits (0-9), hyphen (-), underscore (_)."
        )


# ---------------------------------------------------------------------------
# Advertising coroutine
# ---------------------------------------------------------------------------


async def advertise(beacon_id: str) -> None:
    """
    Start BLE advertising with *beacon_id* as the device local name, then
    block until the user presses Ctrl+C or the process receives SIGTERM.
    """
    print()
    print("=" * 54)
    print("  ESP8266 Attendance System – BLE Beacon Advertiser")
    print("=" * 54)
    print(f"  Beacon ID  :  {beacon_id}")
    print(f"  Register as:  device_id = {beacon_id}")
    print("=" * 54)
    print()
    print("Starting BLE advertisement…  (Ctrl+C to stop)")
    print()

    server = BlessServer(name=beacon_id)

    # Register the attendance GATT service so the advertisement packet is
    # well-formed on all platforms (some stacks drop bare-name advertisements).
    await server.add_new_service(ATTENDANCE_SERVICE_UUID)

    await server.start()

    print(f"✓  Advertising as:  {beacon_id}")
    print("   Keep this terminal open while attending class.")
    print()

    # ------------------------------------------------------------------
    # Block until interrupted
    # ------------------------------------------------------------------
    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, OSError):
            # Windows does not support add_signal_handler; KeyboardInterrupt
            # will propagate naturally from asyncio.run().
            pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
        print()
        print("✓  Advertisement stopped.  Goodbye.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = _build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    validate_beacon_id(args.beacon_id)

    try:
        asyncio.run(advertise(args.beacon_id))
    except KeyboardInterrupt:
        # Ctrl+C on Windows before the signal handler is registered.
        print("\n✓  Advertisement stopped.  Goodbye.")


if __name__ == "__main__":
    main()
