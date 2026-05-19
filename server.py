"""
Local Control MCP
Cross-platform local machine control and observation via Model Context Protocol.
Covers: media playback (SMTC/MPRIS2/osascript), system stats, processes,
volume, clipboard, screenshots, notifications, open URL/file, and power/session.
"""

import asyncio
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import webbrowser
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Linux MPRIS optional import (jeepney — pure Python, no system headers) ───
MPRIS_AVAILABLE = False
if sys.platform.startswith("linux"):
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.asyncio import open_dbus_router
        MPRIS_AVAILABLE = True
    except ImportError:
        logger.warning("jeepney not installed — MPRIS tools unavailable. Run: pip install jeepney")

# ── Windows SMTC optional import ─────────────────────────────────────────────
SMTC_AVAILABLE = False
if sys.platform == "win32":
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _SMTCManager,
        )
        from winrt.windows.media import MediaPlaybackAutoRepeatMode as _RepeatMode
        SMTC_AVAILABLE = True
    except ImportError:
        logger.warning("winrt-Windows.Media.Control not installed — SMTC tools unavailable")

# ── pycaw optional import (Windows volume control) ───────────────────────────
PYCAW_AVAILABLE = False
if sys.platform == "win32":
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        PYCAW_AVAILABLE = True
    except ImportError:
        logger.warning("pycaw not installed — volume control limited on Windows")

# ── psutil optional import ───────────────────────────────────────────────────
PSUTIL_AVAILABLE = False
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logger.warning("psutil not installed — system/process tools unavailable. Run: pip install psutil")

# ── mss optional import (screenshots) ───────────────────────────────────────
MSS_AVAILABLE = False
try:
    import mss
    import mss.tools
    MSS_AVAILABLE = True
except ImportError:
    logger.warning("mss not installed — screenshot tools unavailable. Run: pip install mss")

# ── pyperclip optional import (clipboard) ───────────────────────────────────
PYPERCLIP_AVAILABLE = False
try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    logger.warning("pyperclip not installed — clipboard tools unavailable. Run: pip install pyperclip")

# ── plyer optional import (notifications on Windows) ────────────────────────
PLYER_AVAILABLE = False
try:
    from plyer import notification as _plyer_notification
    PLYER_AVAILABLE = True
except ImportError:
    pass  # plyer is optional; we fall back to platform-specific commands

mcp = FastMCP("Local Control MCP")


# ── MPRIS / macOS media helpers ───────────────────────────────────────────────

_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_OBJ = "/org/mpris/MediaPlayer2"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"
_MPRIS_LOOP_MAP = {"None": "none", "Track": "track", "Playlist": "list"}
_MPRIS_REPEAT_MAP = {"none": "None", "track": "Track", "list": "Playlist"}
_MPRIS_TIMEOUT = 5.0


def _mpris_addr(bus_name: str, interface: str) -> "DBusAddress":
    return DBusAddress(_MPRIS_OBJ, bus_name=bus_name, interface=interface)


async def _mpris_list_services(router) -> list:
    msg = new_method_call(
        DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus",
                    interface="org.freedesktop.DBus"),
        "ListNames",
    )
    reply = await router.send_and_get_reply(msg)
    return [n for n in reply.body[0] if n.startswith(_MPRIS_PREFIX)]


async def _mpris_resolve(router, hint: str = "") -> str | None:
    services = await _mpris_list_services(router)
    if not services:
        return None
    if not hint:
        return services[0]
    full = hint if hint.startswith(_MPRIS_PREFIX) else f"{_MPRIS_PREFIX}{hint}"
    matched = [s for s in services if s == full]
    return matched[0] if matched else None


async def _mpris_get_props(router, service: str) -> dict:
    msg = new_method_call(
        _mpris_addr(service, _PROPS_IFACE),
        "GetAll",
        signature="s",
        body=(_PLAYER_IFACE,),
    )
    reply = await router.send_and_get_reply(msg)
    return dict(reply.body[0])


async def _mpris_call(router, service: str, method: str, signature=None, body=()):
    msg = new_method_call(_mpris_addr(service, _PLAYER_IFACE), method,
                          signature=signature, body=body)
    return await router.send_and_get_reply(msg)


async def _mpris_set(router, service: str, prop: str, type_sig: str, value):
    # D-Bus variant is represented as (type_signature, value) in jeepney
    msg = new_method_call(
        _mpris_addr(service, _PROPS_IFACE),
        "Set",
        signature="ssv",
        body=(_PLAYER_IFACE, prop, (type_sig, value)),
    )
    return await router.send_and_get_reply(msg)


def _mpris_extract_track(props: dict) -> dict:
    meta = dict(props.get("Metadata", {}))
    artists = list(meta.get("xesam:artist", []))
    length_us = int(meta.get("mpris:length", 0))
    pos_us = int(props.get("Position", 0))
    loop = str(props.get("LoopStatus", "None"))
    status = str(props.get("PlaybackStatus", "Stopped"))
    return {
        "title": str(meta.get("xesam:title", "")),
        "artist": ", ".join(str(a) for a in artists),
        "album": str(meta.get("xesam:album", "")),
        "status": status.lower(),
        "position_seconds": round(pos_us / 1_000_000),
        "duration_seconds": round(length_us / 1_000_000),
        "shuffle": bool(props.get("Shuffle", False)),
        "repeat": _MPRIS_LOOP_MAP.get(loop, "none"),
        "track_id": str(meta.get("mpris:trackid", "")),
    }


# ── macOS helpers (osascript) ─────────────────────────────────────────────────

_MACOS_APPS = ["Spotify", "Music"]


def _osa(script: str) -> str:
    """Run an AppleScript fragment, return stdout stripped."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip()


def _osa_str(s: str) -> str:
    """Escape a Python string for safe inclusion inside AppleScript double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _macos_detect_player(hint: str = "") -> str | None:
    """Return the first MPRIS-like macOS app that is running, or None."""
    candidates = [hint] if hint else _MACOS_APPS
    for app in candidates:
        running = _osa(
            f'tell application "System Events" to '
            f'count (processes where name is "{_osa_str(app)}")'
        )
        if running == "1":
            return app
    return None


def _macos_now_playing_sync(hint: str = "") -> dict:
    app = _macos_detect_player(hint)
    if not app:
        return {"success": True, "playing": False, "track": None,
                "note": f"No supported player running. Tried: {_MACOS_APPS}"}
    if app == "Spotify":
        script = f'''
        tell application "Spotify"
            set s to player state as string
            if s is "playing" or s is "paused" then
                set t to name of current track
                set ar to artist of current track
                set al to album of current track
                set pos to player position
                set dur to duration of current track / 1000
                set sh to shuffling as string
                return s & "|||" & t & "|||" & ar & "|||" & al & "|||" & pos & "|||" & dur & "|||" & sh
            end if
        end tell
        '''
    else:  # Music / iTunes
        script = f'''
        tell application "{app}"
            set s to player state as string
            if s is "playing" or s is "paused" then
                set t to name of current track
                set ar to artist of current track
                set al to album of current track
                set pos to player position
                set dur to duration of current track
                set sh to shuffle enabled as string
                return s & "|||" & t & "|||" & ar & "|||" & al & "|||" & pos & "|||" & dur & "|||" & sh
            end if
        end tell
        '''
    raw = _osa(script)
    if not raw or "|||" not in raw:
        return {"success": True, "playing": False, "track": None}
    parts = raw.split("|||")
    status = parts[0].strip()
    return {
        "success": True,
        "playing": status == "playing",
        "player": app,
        "track": {
            "title": parts[1].strip() if len(parts) > 1 else "",
            "artist": parts[2].strip() if len(parts) > 2 else "",
            "album": parts[3].strip() if len(parts) > 3 else "",
            "status": status,
            "position_seconds": round(float(parts[4])) if len(parts) > 4 else 0,
            "duration_seconds": round(float(parts[5])) if len(parts) > 5 else 0,
            "shuffle": parts[6].strip() == "true" if len(parts) > 6 else False,
            "repeat": "unknown",
        },
    }


def _macos_control_sync(action: str, hint: str = "", value=None) -> dict:
    app = _macos_detect_player(hint)
    if not app:
        return {"success": False, "error": f"No supported player running. Tried: {_MACOS_APPS}"}

    if app == "Spotify":
        scripts = {
            "play": 'tell application "Spotify" to play',
            "pause": 'tell application "Spotify" to pause',
            "play_pause": 'tell application "Spotify" to playpause',
            "next": 'tell application "Spotify" to next track',
            "previous": 'tell application "Spotify" to previous track',
            "seek": f'tell application "Spotify" to set player position to {value}',
            "shuffle_on": 'tell application "Spotify" to set shuffling to true',
            "shuffle_off": 'tell application "Spotify" to set shuffling to false',
            "repeat_none": 'tell application "Spotify" to set repeating to false',
            "repeat_track": 'tell application "Spotify" to set repeating to true',
            "repeat_list": 'tell application "Spotify" to set repeating to true',
        }
    else:
        scripts = {
            "play": f'tell application "{app}" to play',
            "pause": f'tell application "{app}" to pause',
            "play_pause": f'tell application "{app}" to playpause',
            "next": f'tell application "{app}" to next track',
            "previous": f'tell application "{app}" to back track',
            "seek": f'tell application "{app}" to set player position to {value}',
            "shuffle_on": f'tell application "{app}" to set shuffle enabled to true',
            "shuffle_off": f'tell application "{app}" to set shuffle enabled to false',
            "repeat_none": f'tell application "{app}" to set song repeat to off',
            "repeat_track": f'tell application "{app}" to set song repeat to one',
            "repeat_list": f'tell application "{app}" to set song repeat to all',
        }
    script = scripts.get(action)
    if not script:
        return {"success": False, "error": f"Unknown action: {action}"}
    try:
        _osa(script)
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "osascript timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Media control (Windows SMTC · Linux MPRIS2 · macOS osascript) ─────────────

_NOT_SUPPORTED = {
    "success": False,
    "error": "Media control is not supported on this platform.",
}

# ── SMTC helpers ─────────────────────────────────────────────────────────────

_SMTC_NOT_AVAILABLE = {"success": False, "error": "SMTC requires Windows and: pip install winrt-Windows.Media.Control winrt-Windows.Media"}

_PLAYBACK_STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
_REPEAT_NAMES = {"none": 0, "track": 1, "list": 2}
_REPEAT_VALUES = {0: "none", 1: "track", 2: "list"}
_SMTC_TIMEOUT = 5.0  # seconds before giving up on a WinRT async call


async def _smtc_session():
    """Return the current SMTC media session, or None if nothing is playing."""
    manager = await asyncio.wait_for(_SMTCManager.request_async(), timeout=_SMTC_TIMEOUT)
    return manager.get_current_session()


# ── Unified playback control tools ───────────────────────────────────────────


@mcp.tool()
async def list_players(player: str = "") -> Dict[str, Any]:
    """
    List available media players.

    On Linux returns MPRIS2-registered players (Spotify, VLC, Firefox, etc.).
    On macOS returns which supported apps (Spotify, Music) are running.
    On Windows, SMTC controls whatever app owns the system media session — no
    per-player selection is needed, so this returns a fixed "system" entry.

    Returns:
        Dict with a list of player names.
    """
    if sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                services = await asyncio.wait_for(_mpris_list_services(router), _MPRIS_TIMEOUT)
            players = [s.removeprefix(_MPRIS_PREFIX) for s in services]
            return {"success": True, "players": players}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        running = [app for app in _MACOS_APPS if _macos_detect_player(app)]
        return {"success": True, "players": running}
    elif sys.platform == "win32":
        return {"success": True, "players": ["system"],
                "note": "SMTC controls the system-active media session — no per-player selection needed."}
    return _NOT_SUPPORTED


@mcp.tool()
async def get_now_playing(player: str = "") -> Dict[str, Any]:
    """
    Get the currently playing track.

    Auto-selects the backend by platform:
    - Windows: reads from SMTC (any app registered with System Media Transport Controls)
    - Linux: reads from MPRIS2 via D-Bus (Spotify, VLC, Firefox, Chromium, etc.)
    - macOS: reads via osascript (Spotify or Music)

    Args:
        player: Player name hint for Linux/macOS (e.g. "spotify", "vlc").
                Ignored on Windows — SMTC always reads the system-active session.

    Returns:
        Dict with title, artist, album, playback status, position, duration,
        shuffle state, and repeat mode.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": True, "playing": False, "track": None}
            props = await asyncio.wait_for(session.try_get_media_properties_async(), timeout=_SMTC_TIMEOUT)
            playback = session.get_playback_info()
            timeline = session.get_timeline_properties()
            status_int = int(playback.playback_status)
            return {
                "success": True,
                "playing": status_int == 4,
                "track": {
                    "title": props.title,
                    "artist": props.artist,
                    "album": props.album_title,
                    "track_number": props.track_number,
                    "status": _PLAYBACK_STATUS.get(status_int, "unknown"),
                    "position_seconds": round(timeline.position.total_seconds()),
                    "duration_seconds": round(timeline.end_time.total_seconds()),
                    "shuffle": playback.is_shuffle_active,
                    "repeat": _REPEAT_VALUES.get(int(playback.auto_repeat_mode), "unknown"),
                    "source_app": session.source_app_user_model_id,
                },
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await asyncio.wait_for(_mpris_resolve(router, player), _MPRIS_TIMEOUT)
                if not service:
                    services = await _mpris_list_services(router)
                    hint = (f"Available: {[s.removeprefix(_MPRIS_PREFIX) for s in services]}"
                            if services else "No MPRIS players are running")
                    return {"success": False, "error": hint}
                props = await asyncio.wait_for(_mpris_get_props(router, service), _MPRIS_TIMEOUT)
            track = _mpris_extract_track(props)
            return {
                "success": True,
                "playing": track["status"] == "playing",
                "player": service.removeprefix(_MPRIS_PREFIX),
                "track": track,
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_now_playing_sync, player)
    return _NOT_SUPPORTED


@mcp.tool()
async def play(player: str = "") -> Dict[str, Any]:
    """
    Resume playback.

    Args:
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ok = await asyncio.wait_for(session.try_play_async(), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(_mpris_call(router, service, "Play"), _MPRIS_TIMEOUT)
            return {"success": True}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "play", player)
    return _NOT_SUPPORTED


@mcp.tool()
async def pause(player: str = "") -> Dict[str, Any]:
    """
    Pause playback.

    Args:
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ok = await asyncio.wait_for(session.try_pause_async(), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(_mpris_call(router, service, "Pause"), _MPRIS_TIMEOUT)
            return {"success": True}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "pause", player)
    return _NOT_SUPPORTED


@mcp.tool()
async def play_pause(player: str = "") -> Dict[str, Any]:
    """
    Toggle play/pause.

    Args:
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            playback = session.get_playback_info()
            is_playing = int(playback.playback_status) == 4
            if is_playing:
                ok = await asyncio.wait_for(session.try_pause_async(), timeout=_SMTC_TIMEOUT)
                new_state = "paused"
            else:
                ok = await asyncio.wait_for(session.try_play_async(), timeout=_SMTC_TIMEOUT)
                new_state = "playing"
            return {"success": bool(ok), "state": new_state}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(_mpris_call(router, service, "PlayPause"), _MPRIS_TIMEOUT)
            return {"success": True}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "play_pause", player)
    return _NOT_SUPPORTED


@mcp.tool()
async def next_track(player: str = "") -> Dict[str, Any]:
    """
    Skip to the next track.

    Args:
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ok = await asyncio.wait_for(session.try_skip_next_async(), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(_mpris_call(router, service, "Next"), _MPRIS_TIMEOUT)
            return {"success": True}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "next", player)
    return _NOT_SUPPORTED


@mcp.tool()
async def previous_track(player: str = "") -> Dict[str, Any]:
    """
    Go to the previous track.

    Args:
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ok = await asyncio.wait_for(session.try_skip_previous_async(), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok)}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(_mpris_call(router, service, "Previous"), _MPRIS_TIMEOUT)
            return {"success": True}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "previous", player)
    return _NOT_SUPPORTED


@mcp.tool()
async def seek(position_seconds: float, player: str = "") -> Dict[str, Any]:
    """
    Seek to an absolute position in the current track.

    Args:
        position_seconds: Target position in seconds from the start of the track.
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag and the position seeked to.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ticks = int(position_seconds * 10_000_000)  # WinRT TimeSpan in 100-ns ticks
            ok = await asyncio.wait_for(session.try_change_playback_position_async(ticks), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok), "position_seconds": position_seconds}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                props = await _mpris_get_props(router, service)
                track_id = str(dict(props.get("Metadata", {})).get("mpris:trackid", ""))
                pos_us = int(position_seconds * 1_000_000)
                await asyncio.wait_for(
                    _mpris_call(router, service, "SetPosition",
                                signature="ox", body=(track_id, pos_us)),
                    _MPRIS_TIMEOUT,
                )
            return {"success": True, "position_seconds": position_seconds}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        return await asyncio.to_thread(_macos_control_sync, "seek", player, position_seconds)
    return _NOT_SUPPORTED


@mcp.tool()
async def set_shuffle(active: bool, player: str = "") -> Dict[str, Any]:
    """
    Enable or disable shuffle.

    Args:
        active: True to enable shuffle, False to disable.
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            ok = await asyncio.wait_for(session.try_change_shuffle_active_async(active), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok), "shuffle": active}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(
                    _mpris_set(router, service, "Shuffle", "b", active),
                    _MPRIS_TIMEOUT,
                )
            return {"success": True, "shuffle": active}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        action = "shuffle_on" if active else "shuffle_off"
        return await asyncio.to_thread(_macos_control_sync, action, player)
    return _NOT_SUPPORTED


@mcp.tool()
async def set_repeat(mode: str, player: str = "") -> Dict[str, Any]:
    """
    Set the repeat mode.

    Args:
        mode: One of "none", "track", or "list".
        player: Player name hint for Linux/macOS. Ignored on Windows.

    Returns:
        Dict with success flag.
    """
    mode = mode.lower()
    if mode not in _MPRIS_REPEAT_MAP:
        return {"success": False, "error": f"mode must be one of: {list(_MPRIS_REPEAT_MAP)}"}
    if sys.platform == "win32":
        if not SMTC_AVAILABLE:
            return _SMTC_NOT_AVAILABLE
        try:
            session = await _smtc_session()
            if not session:
                return {"success": False, "error": "No active media session"}
            repeat_value = _RepeatMode(int(_REPEAT_NAMES[mode]))
            ok = await asyncio.wait_for(session.try_change_auto_repeat_mode_async(repeat_value), timeout=_SMTC_TIMEOUT)
            return {"success": bool(ok), "repeat": mode}
        except asyncio.TimeoutError:
            return {"success": False, "error": "SMTC request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform.startswith("linux"):
        if not MPRIS_AVAILABLE:
            return {"success": False, "error": "Install jeepney: pip install jeepney"}
        try:
            loop_value = _MPRIS_REPEAT_MAP[mode]
            async with open_dbus_router() as router:
                service = await _mpris_resolve(router, player)
                if not service:
                    return {"success": False, "error": "No MPRIS player found"}
                await asyncio.wait_for(
                    _mpris_set(router, service, "LoopStatus", "s", loop_value),
                    _MPRIS_TIMEOUT,
                )
            return {"success": True, "repeat": mode}
        except asyncio.TimeoutError:
            return {"success": False, "error": "D-Bus request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        action = f"repeat_{mode}"
        return await asyncio.to_thread(_macos_control_sync, action, player)
    return _NOT_SUPPORTED


# ── System stats ──────────────────────────────────────────────────────────────

@mcp.tool()
async def get_system_info() -> Dict[str, Any]:
    """
    Get a snapshot of system resource usage.

    Returns CPU usage, RAM, disk usage for the root/C: drive, OS info,
    and system uptime. Requires psutil.
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    def _collect():
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("C:\\" if sys.platform == "win32" else "/")
            disk_info = {
                "total_gb": round(disk.total / 1024**3, 1),
                "used_gb": round(disk.used / 1024**3, 1),
                "free_gb": round(disk.free / 1024**3, 1),
                "percent": disk.percent,
            }
        except Exception:
            disk_info = None
        uptime_seconds = int(time.time() - psutil.boot_time())
        return {
            "success": True,
            "cpu_percent": cpu,
            "cpu_count": psutil.cpu_count(logical=True),
            "ram": {
                "total_gb": round(ram.total / 1024**3, 1),
                "used_gb": round(ram.used / 1024**3, 1),
                "available_gb": round(ram.available / 1024**3, 1),
                "percent": ram.percent,
            },
            "disk": disk_info,
            "uptime_seconds": uptime_seconds,
            "os": f"{sys.platform}",
        }
    return await asyncio.to_thread(_collect)


@mcp.tool()
async def get_battery() -> Dict[str, Any]:
    """
    Get battery status.

    Returns charge percent, whether plugged in, and estimated seconds remaining.
    Returns an error on desktop machines with no battery sensor.
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    def _collect():
        bat = psutil.sensors_battery()
        if bat is None:
            return {"success": False, "error": "No battery sensor found (desktop or unsupported platform)"}
        secs = bat.secsleft
        if secs in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
            secs = None
        return {
            "success": True,
            "percent": round(bat.percent, 1),
            "plugged_in": bat.power_plugged,
            "seconds_remaining": secs,
        }
    return await asyncio.to_thread(_collect)


@mcp.tool()
async def get_network_interfaces() -> Dict[str, Any]:
    """
    List network interfaces and their IP addresses.

    Returns each interface name with its IPv4 and IPv6 addresses,
    plus whether it appears to be up (has a non-loopback address).
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    def _collect():
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        interfaces = []
        for iface, addr_list in addrs.items():
            ipv4 = [a.address for a in addr_list if a.family.name == "AF_INET"]
            ipv6 = [a.address for a in addr_list if a.family.name == "AF_INET6"]
            is_up = stats[iface].isup if iface in stats else False
            interfaces.append({
                "name": iface,
                "is_up": is_up,
                "ipv4": ipv4,
                "ipv6": ipv6,
            })
        return {"success": True, "interfaces": interfaces}
    return await asyncio.to_thread(_collect)


# ── Process management ────────────────────────────────────────────────────────

@mcp.tool()
async def list_processes(sort_by: str = "cpu", limit: int = 20) -> Dict[str, Any]:
    """
    List running processes.

    Args:
        sort_by: Sort field — "cpu", "memory", or "name". Default "cpu".
        limit: Maximum number of processes to return. Default 20, max 100.

    Returns:
        Dict with a list of processes (pid, name, cpu_percent, memory_percent, status).
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    limit = min(limit, 100)
    sort_by = sort_by.lower()
    def _collect():
        # psutil.cpu_percent() needs two samples to compute a real value, so
        # prime each process, sleep briefly, then collect actual values.
        primed = []
        for p in psutil.process_iter():
            try:
                p.cpu_percent(None)
                primed.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.3)
        procs = []
        for p in primed:
            try:
                procs.append({
                    "pid": p.pid,
                    "name": p.name(),
                    "cpu_percent": p.cpu_percent(None),
                    "memory_percent": round(p.memory_percent(), 2),
                    "status": p.status(),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        key_map = {"cpu": "cpu_percent", "memory": "memory_percent", "name": "name"}
        key = key_map.get(sort_by, "cpu_percent")
        reverse = key != "name"
        procs.sort(key=lambda p: (p.get(key) or 0), reverse=reverse)
        return {"success": True, "processes": procs[:limit], "total_count": len(procs)}
    return await asyncio.to_thread(_collect)


@mcp.tool()
async def get_process(pid: int) -> Dict[str, Any]:
    """
    Get detailed information about a specific process by PID.

    Args:
        pid: Process ID.

    Returns:
        Dict with pid, name, status, cpu_percent, memory_percent, username,
        command line, and create time.
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    def _collect():
        try:
            p = psutil.Process(pid)
            info = p.as_dict(attrs=["pid", "name", "status", "cpu_percent",
                                     "memory_percent", "username", "cmdline", "create_time"])
            info["create_time"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.localtime(info.get("create_time", 0)))
            info["cmdline"] = " ".join(info.get("cmdline") or [])
            return {"success": True, "process": info}
        except psutil.NoSuchProcess:
            return {"success": False, "error": f"No process with PID {pid}"}
        except psutil.AccessDenied:
            return {"success": False, "error": f"Access denied for PID {pid}"}
    return await asyncio.to_thread(_collect)


@mcp.tool()
async def kill_process(pid: int) -> Dict[str, Any]:
    """
    Terminate a process by PID (sends SIGTERM / terminate signal).

    Args:
        pid: Process ID to terminate.

    Returns:
        Dict with success flag.
    """
    if not PSUTIL_AVAILABLE:
        return {"success": False, "error": "Install psutil: pip install psutil"}
    def _kill():
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            return {"success": True, "pid": pid, "name": name}
        except psutil.NoSuchProcess:
            return {"success": False, "error": f"No process with PID {pid}"}
        except psutil.AccessDenied:
            return {"success": False, "error": f"Access denied for PID {pid}"}
    return await asyncio.to_thread(_kill)


@mcp.tool()
async def launch_app(command: str) -> Dict[str, Any]:
    """
    Launch an application or command in the background.

    Args:
        command: Command to run (e.g. "notepad.exe", "gedit", "firefox https://example.com").
                 Tokenized with shell-style splitting (shlex.split) — quote arguments
                 that contain spaces. No shell expansion (no globbing, no env vars).

    Returns:
        Dict with success flag and the new process PID.
    """
    def _launch():
        try:
            argv = shlex.split(command, posix=(sys.platform != "win32"))
            if not argv:
                return {"success": False, "error": "Empty command"}
            proc = subprocess.Popen(
                argv,
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"success": True, "pid": proc.pid, "command": command}
        except FileNotFoundError:
            return {"success": False, "error": f"Command not found: {command}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_launch)


# ── Volume control ────────────────────────────────────────────────────────────

def _volume_not_available() -> Dict[str, Any]:
    if sys.platform == "win32":
        return {"success": False, "error": "Install pycaw: pip install pycaw"}
    return {"success": False, "error": "Volume control unavailable on this platform"}


def _pycaw_get_volume_endpoint():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


@mcp.tool()
async def get_volume() -> Dict[str, Any]:
    """
    Get the current system volume level and mute state.

    Returns:
        Dict with volume (0-100) and muted (bool).
    """
    if sys.platform == "win32":
        if not PYCAW_AVAILABLE:
            return _volume_not_available()
        def _get():
            vol = _pycaw_get_volume_endpoint()
            level = round(vol.GetMasterVolumeLevelScalar() * 100)
            muted = bool(vol.GetMute())
            return {"success": True, "volume": level, "muted": muted}
        return await asyncio.to_thread(_get)
    elif sys.platform.startswith("linux"):
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            # "Volume: front-left: 65536 /  100% / 0.00 dB, ..."
            m = re.search(r"(\d+)%", r.stdout)
            level = int(m.group(1)) if m else 0
            r2 = await asyncio.to_thread(
                subprocess.run,
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            muted = "yes" in r2.stdout.lower()
            return {"success": True, "volume": level, "muted": muted}
        except FileNotFoundError:
            return {"success": False, "error": "pactl not found — install pulseaudio-utils"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        def _get():
            raw = _osa("output volume of (get volume settings)")
            muted_raw = _osa("output muted of (get volume settings)")
            level = int(raw) if raw.isdigit() else 0
            muted = muted_raw.strip().lower() == "true"
            return {"success": True, "volume": level, "muted": muted}
        return await asyncio.to_thread(_get)
    return _volume_not_available()


@mcp.tool()
async def set_volume(level: int) -> Dict[str, Any]:
    """
    Set the system volume level.

    Args:
        level: Volume level from 0 (silent) to 100 (maximum).

    Returns:
        Dict with success flag and the new volume level.
    """
    level = max(0, min(100, level))
    if sys.platform == "win32":
        if not PYCAW_AVAILABLE:
            return _volume_not_available()
        def _set():
            vol = _pycaw_get_volume_endpoint()
            vol.SetMasterVolumeLevelScalar(level / 100.0, None)
            return {"success": True, "volume": level}
        return await asyncio.to_thread(_set)
    elif sys.platform.startswith("linux"):
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                capture_output=True, timeout=5,
            )
            return {"success": True, "volume": level}
        except FileNotFoundError:
            return {"success": False, "error": "pactl not found — install pulseaudio-utils"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        def _set():
            _osa(f"set volume output volume {level}")
            return {"success": True, "volume": level}
        return await asyncio.to_thread(_set)
    return _volume_not_available()


@mcp.tool()
async def toggle_mute() -> Dict[str, Any]:
    """
    Toggle the system mute state.

    Returns:
        Dict with success flag and the new muted state.
    """
    if sys.platform == "win32":
        if not PYCAW_AVAILABLE:
            return _volume_not_available()
        def _toggle():
            vol = _pycaw_get_volume_endpoint()
            new_mute = not bool(vol.GetMute())
            vol.SetMute(new_mute, None)
            return {"success": True, "muted": new_mute}
        return await asyncio.to_thread(_toggle)
    elif sys.platform.startswith("linux"):
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                capture_output=True, timeout=5,
            )
            r = await asyncio.to_thread(
                subprocess.run,
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            muted = "yes" in r.stdout.lower()
            return {"success": True, "muted": muted}
        except FileNotFoundError:
            return {"success": False, "error": "pactl not found — install pulseaudio-utils"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif sys.platform == "darwin":
        def _toggle():
            current = _osa("output muted of (get volume settings)")
            new_mute = current.strip().lower() != "true"
            _osa(f"set volume output muted {str(new_mute).lower()}")
            return {"success": True, "muted": new_mute}
        return await asyncio.to_thread(_toggle)
    return _volume_not_available()


# ── Clipboard ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_clipboard() -> Dict[str, Any]:
    """
    Read the current clipboard text content.

    Returns:
        Dict with text field containing the clipboard contents.
    """
    if not PYPERCLIP_AVAILABLE:
        return {"success": False, "error": "Install pyperclip: pip install pyperclip"}
    def _get():
        try:
            text = pyperclip.paste()
            return {"success": True, "text": text}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_get)


@mcp.tool()
async def set_clipboard(text: str) -> Dict[str, Any]:
    """
    Write text to the clipboard.

    Args:
        text: Text to place on the clipboard.

    Returns:
        Dict with success flag.
    """
    if not PYPERCLIP_AVAILABLE:
        return {"success": False, "error": "Install pyperclip: pip install pyperclip"}
    def _set():
        try:
            pyperclip.copy(text)
            return {"success": True, "length": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_set)


# ── Screenshots ───────────────────────────────────────────────────────────────

@mcp.tool()
async def take_screenshot(monitor: int = 1, save_path: str = "") -> Dict[str, Any]:
    """
    Capture a screenshot and save it to a file.

    Args:
        monitor: Monitor index. 1 is the primary monitor, 2 is the second, etc.
                 Use 0 to capture all monitors combined into one image.
        save_path: File path to save the PNG. Defaults to a temp file.

    Returns:
        Dict with success flag and the path where the image was saved.
    """
    if not MSS_AVAILABLE:
        return {"success": False, "error": "Install mss: pip install mss"}
    def _capture():
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor < 0 or monitor >= len(monitors):
                    return {"success": False,
                            "error": f"Monitor {monitor} not found. Available: 0-{len(monitors)-1}"}
                target = monitors[monitor]
                img = sct.grab(target)
                if save_path:
                    path = save_path
                else:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                        path = f.name
                mss.tools.to_png(img.rgb, img.size, output=path)
                return {"success": True, "path": path,
                        "width": img.width, "height": img.height}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_capture)


# ── OS Notifications ──────────────────────────────────────────────────────────

@mcp.tool()
async def send_notification(title: str, message: str, timeout: int = 5) -> Dict[str, Any]:
    """
    Send a desktop notification.

    Args:
        title: Notification title.
        message: Notification body text.
        timeout: How long to display in seconds (Linux/macOS hint; Windows uses system setting).

    Returns:
        Dict with success flag.
    """
    def _notify():
        try:
            if sys.platform == "win32":
                if PLYER_AVAILABLE:
                    _plyer_notification.notify(title=title, message=message,
                                               timeout=timeout, app_name="Local Control MCP")
                    return {"success": True}
                return {"success": False, "error": "Install plyer: pip install plyer"}
            elif sys.platform.startswith("linux"):
                subprocess.run(
                    ["notify-send", "--expire-time", str(timeout * 1000), title, message],
                    timeout=10, check=True,
                )
                return {"success": True}
            elif sys.platform == "darwin":
                script = (f'display notification "{_osa_str(message)}" '
                          f'with title "{_osa_str(title)}"')
                _osa(script)
                return {"success": True}
            return {"success": False, "error": "Notifications not supported on this platform"}
        except FileNotFoundError:
            return {"success": False, "error": "notify-send not found — install libnotify-bin"}
        except subprocess.CalledProcessError as e:
            return {"success": False, "error": f"notify-send failed: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_notify)


# ── Open URL / file ───────────────────────────────────────────────────────────

@mcp.tool()
async def open_url(url: str) -> Dict[str, Any]:
    """
    Open a URL in the default web browser.

    Args:
        url: URL to open (e.g. "https://example.com").

    Returns:
        Dict with success flag.
    """
    def _open():
        try:
            opened = webbrowser.open(url)
            if not opened:
                return {"success": False, "error": "No default browser found"}
            return {"success": True, "url": url}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_open)


@mcp.tool()
async def open_file(path: str) -> Dict[str, Any]:
    """
    Open a file in its default application (e.g. a PDF in the PDF viewer,
    an image in the image viewer, etc.).

    Args:
        path: Absolute path to the file.

    Returns:
        Dict with success flag.
    """
    def _open():
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=True, timeout=10)
            else:
                subprocess.run(["xdg-open", path], check=True, timeout=10)
            return {"success": True, "path": path}
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_open)


# ── Power / session ───────────────────────────────────────────────────────────

@mcp.tool()
async def lock_screen() -> Dict[str, Any]:
    """
    Lock the desktop session immediately.

    Returns:
        Dict with success flag.
    """
    def _lock():
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.LockWorkStation()
                return {"success": True}
            elif sys.platform.startswith("linux"):
                # Try loginctl first (systemd), then xdg-screensaver, then gnome-screensaver
                for cmd in (
                    ["loginctl", "lock-session"],
                    ["xdg-screensaver", "lock"],
                    ["gnome-screensaver-command", "--lock"],
                ):
                    try:
                        subprocess.run(cmd, check=True, timeout=5)
                        return {"success": True}
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        continue
                return {"success": False, "error": "No lock command found (tried loginctl, xdg-screensaver, gnome-screensaver-command)"}
            elif sys.platform == "darwin":
                # CGSession -suspend locks without needing Accessibility permission
                # (which the keystroke fallback would require).
                cgsession = (
                    "/System/Library/CoreServices/Menu Extras/User.menu/"
                    "Contents/Resources/CGSession"
                )
                subprocess.run([cgsession, "-suspend"], check=True, timeout=5)
                return {"success": True}
            return {"success": False, "error": "Lock not supported on this platform"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_lock)


@mcp.tool()
async def sleep_system() -> Dict[str, Any]:
    """
    Put the machine to sleep (suspend to RAM).

    Returns:
        Dict with success flag. Note: on success the machine will suspend
        immediately and the response may not be delivered before sleep begins.
    """
    def _sleep():
        try:
            if sys.platform == "win32":
                # SetSuspendState(hibernate=0, force=1, disable_wake_event=0)
                subprocess.run(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"],
                    timeout=10,
                )
                return {"success": True}
            elif sys.platform.startswith("linux"):
                subprocess.run(["systemctl", "suspend"], check=True, timeout=10)
                return {"success": True}
            elif sys.platform == "darwin":
                subprocess.run(["pmset", "sleepnow"], check=True, timeout=10)
                return {"success": True}
            return {"success": False, "error": "Sleep not supported on this platform"}
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return await asyncio.to_thread(_sleep)


# ── MCP prompt ────────────────────────────────────────────────────────────────

@mcp.prompt("local-control-assistant")
async def local_control_assistant() -> str:
    """System prompt for local machine control and observation."""
    return """
You are a local machine control assistant with tools to observe and control the host system.

## Media playback — auto-selects backend by platform (SMTC/MPRIS2/osascript)
- list_players — list active media players
- get_now_playing — current track: title, artist, album, status, position, duration, shuffle, repeat
- play / pause / play_pause — playback control
- next_track / previous_track — track navigation
- seek — jump to absolute position (seconds)
- set_shuffle — enable/disable shuffle
- set_repeat — set repeat mode: none / track / list

## System stats
- get_system_info — CPU%, RAM, disk, uptime, OS
- get_battery — charge level, plugged in, time remaining
- get_network_interfaces — network interfaces with IP addresses

## Process management
- list_processes(sort_by, limit) — running processes sorted by cpu/memory/name
- get_process(pid) — details for a specific process
- kill_process(pid) — terminate a process
- launch_app(command) — launch an application

## Volume control — auto-selects backend (pycaw/pactl/osascript)
- get_volume — current level (0-100) and mute state
- set_volume(level) — set volume 0-100
- toggle_mute — toggle mute on/off

## Clipboard
- get_clipboard — read clipboard text
- set_clipboard(text) — write text to clipboard

## Screenshots
- take_screenshot(monitor, save_path) — capture screen, returns file path

## Notifications
- send_notification(title, message, timeout) — send desktop notification

## Open / launch
- open_url(url) — open URL in default browser
- open_file(path) — open file in default app

## Power / session
- lock_screen — lock the desktop immediately
- sleep_system — suspend the machine to RAM

## Tips
- All tools return {"success": false, "error": "..."} when a backend is unavailable
- On Linux, volume tools require pulseaudio-utils (pactl); screenshots need a display
- Clipboard on Linux headless requires xclip or xsel installed
- sleep_system response may not arrive before the machine suspends
"""


def main():
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    transport = os.environ.get("MCP_TRANSPORT", "http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport in ("http", "streamable-http"):
        # stateless_http avoids the session initialization handshake that breaks
        # some clients (including AnythingLLM) which send requests before the
        # MCP initialize round-trip completes on a persistent session.
        mcp.run(transport=transport, host=host, port=port, stateless_http=True)
    elif transport == "sse":
        # SSE keeps a persistent session and does not support stateless mode.
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    main()
