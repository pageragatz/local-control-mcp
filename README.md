# Local Control MCP

A Model Context Protocol (MCP) server for local machine control and observation. Give an LLM agent the ability to see what's happening on your machine and act on it — all through a clean MCP interface with no cloud dependencies.

## Features

### Media Playback
Cross-platform playback control that auto-selects the backend by OS:
- **Windows**: System Media Transport Controls (SMTC) — any app registered with the OS (Deezer, Spotify, browser, etc.)
- **Linux**: MPRIS2 via D-Bus (jeepney) — Spotify, VLC, Firefox, Chromium, Rhythmbox, etc.
- **macOS**: osascript — Spotify and Music

### System Observation
- CPU usage, RAM, disk, and uptime via `psutil`
- Battery level and charge status
- Network interfaces and IP addresses

### Process Management
- List running processes sorted by CPU/memory
- Detailed process info by PID
- Launch applications, terminate processes

### Audio / Volume
- Get and set system volume (0–100)
- Toggle mute
- Backends: `pycaw` (Windows), `pactl` (Linux), `osascript` (macOS)

### Clipboard
- Read and write clipboard text via `pyperclip`

### Screenshots
- Capture any monitor to a PNG file via `mss`

### Notifications
- Send desktop notifications via `notify-send` (Linux), `plyer` (Windows), `osascript` (macOS)

### Open / Launch
- Open a URL in the default browser
- Open a file in its default application

### Power / Session
- Lock the screen
- Sleep/suspend the machine

---

## Tool Reference

### Media Playback

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_players` | List active media players | — |
| `get_now_playing` | Current track: title, artist, album, position, duration, shuffle, repeat | player |
| `play` | Resume playback | player |
| `pause` | Pause playback | player |
| `play_pause` | Toggle play/pause | player |
| `next_track` | Skip to next track | player |
| `previous_track` | Go to previous track | player |
| `seek` | Seek to position (seconds) | position_seconds, player |
| `set_shuffle` | Enable or disable shuffle | active, player |
| `set_repeat` | Set repeat mode: `none`, `track`, or `list` | mode, player |

The `player` parameter is accepted by all tools but only used on Linux/macOS to select a specific app. On Windows it is silently ignored — SMTC always targets the system-active session.

### System Stats

| Tool | Description |
|------|-------------|
| `get_system_info` | CPU%, RAM, disk usage, uptime, OS |
| `get_battery` | Charge percent, plugged in, time remaining |
| `get_network_interfaces` | Network interfaces with IPv4/IPv6 addresses |

### Process Management

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_processes` | Running processes | sort_by (cpu/memory/name), limit |
| `get_process` | Process details by PID | pid |
| `kill_process` | Terminate process | pid |
| `launch_app` | Launch an application | command |

### Audio / Volume

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `get_volume` | Current volume (0-100) and mute state | — |
| `set_volume` | Set volume | level (0-100) |
| `toggle_mute` | Toggle mute on/off | — |

### Clipboard

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `get_clipboard` | Read clipboard text | — |
| `set_clipboard` | Write text to clipboard | text |

### Screenshots

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `take_screenshot` | Capture screen to PNG file | monitor (0=all, 1=primary), save_path |

### Notifications

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `send_notification` | Send desktop notification | title, message, timeout |

### Open / Launch

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `open_url` | Open URL in default browser | url |
| `open_file` | Open file in default app | path |

### Power / Session

| Tool | Description |
|------|-------------|
| `lock_screen` | Lock the desktop session |
| `sleep_system` | Suspend the machine to RAM |

All tools return `{"success": false, "error": "..."}` gracefully when a backend is unavailable or a package is missing.

---

## Installation

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

```bash
# Create and activate a virtual environment
uv venv --python 3.11
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
uv pip install -r requirements.txt

# Start the server
uv run server.py
```

The server runs on `http://localhost:8000` by default using streamable-http transport.

### Docker

```bash
docker compose up --build
docker compose up -d   # background
docker compose down
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `0.0.0.0` | Host to bind to |
| `MCP_PORT` | `8000` | Port to listen on |
| `MCP_TRANSPORT` | `http` | Transport: `http`, `streamable-http`, `sse`, or `stdio` |

---

## Client Configuration

### AnythingLLM

1. Start the server (`docker compose up -d` or `uv run server.py`)
2. In AnythingLLM: **Settings → Agent Skills → Custom MCP Servers**
3. Add:

```json
{
  "mcpServers": {
    "local-control": {
      "type": "streamable",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

If AnythingLLM runs inside Docker and the server is on the host, use `http://host.docker.internal:8000/mcp`.

### Claude Desktop

```json
{
  "mcpServers": {
    "local-control": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/sse", "--transport", "sse-only"]
    }
  }
}
```

### Generic SSE Client

Connect to: `http://localhost:8000/sse`

---

## Platform Notes

### Linux
- **Volume**: requires `pulseaudio-utils` (`apt install pulseaudio-utils` for `pactl`)
- **MPRIS playback**: install `jeepney` (included in requirements)
- **Screenshots**: requires a display; Wayland support depends on compositor
- **Clipboard**: headless environments need `xclip` or `xsel`
- **Notifications**: requires `libnotify-bin` (`apt install libnotify-bin` for `notify-send`)
- **Lock screen**: tries `loginctl lock-session`, then `xdg-screensaver`, then `gnome-screensaver-command`

### Windows
- **Playback (SMTC)**: install `winrt-Windows.Media.Control` and `winrt-Windows.Media` (included in requirements)
- **Volume**: install `pycaw` (included in requirements)
- **Notifications**: install `plyer` (included in requirements)

### macOS
- **Playback**: uses `osascript` (no extra packages)
- **Volume**: uses `osascript` (no extra packages)
- **Notifications**: uses `osascript` (no extra packages)
- **Screenshots**: works on main display; may require Screen Recording permission
