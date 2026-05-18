# Deezer + Last.fm MCP Server

A Model Context Protocol (MCP) server combining the Deezer catalog with personalized Last.fm listening data. Search and explore music, see what you're playing right now, love tracks, and scrobble — all from an LLM agent.

![Example usage](exemple.png)

## Features

### Last.fm Personalization
- **Now playing**: See the track currently playing (via Deezer → Last.fm scrobbling)
- **Listening history**: Recent tracks, top tracks and artists by time period
- **Loved tracks**: Your hearted tracks
- **Write actions**: Love/unlove tracks, submit scrobbles, push now-playing status
- **Discovery**: Similar artists via Last.fm's similarity graph, track tags and wiki

### Search
- **Tracks**: Basic and advanced search with artist, BPM, duration, and label filters
- **Artists**: Search by name, get details, discography, top tracks, and related artists
- **Albums**: Search by title/artist, get full details and tracklists
- **Playlists**: Search public playlists, get full details

### Discovery
- **Charts**: Current top tracks, albums, artists, and playlists (filterable by genre)
- **Genres**: Browse all genres, find popular artists per genre

### Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `search_tracks` | Search tracks | query, limit (max 25), strict, order |
| `advanced_search` | Search with multiple criteria | artist, album, track, label, dur_min/max, bpm_min/max |
| `get_track_details` | Full track info by ID | track_id |
| `get_artist_details` | Artist profile by ID | artist_id |
| `get_artist_albums` | Artist discography | artist_id, limit |
| `get_artist_top_tracks` | Artist's most popular tracks | artist_id, limit |
| `get_artist_related` | Similar/related artists | artist_id, limit |
| `get_album_details` | Full album info by ID | album_id |
| `get_album_tracks` | Album tracklist | album_id |
| `get_playlist_details` | Playlist content by ID | playlist_id |
| `search_artists` | Search artists by name | query, limit |
| `search_albums` | Search albums | query, limit |
| `search_playlists` | Search public playlists | query, limit |
| `get_genre_list` | All available genres | — |
| `get_genre_artists` | Popular artists in a genre | genre_id, limit |
| `get_chart` | Current music charts | genre_id (0=all), limit |

### Windows SMTC Tools (Windows only)

Controls whatever media app is currently active system-wide — Deezer, Spotify, browser, anything registered with Windows SMTC. No API key required; reads directly from the OS media session.

| Tool | Description |
|------|-------------|
| `smtc_get_now_playing` | Current track: title, artist, album, position, duration, shuffle, repeat |
| `smtc_play` | Resume playback |
| `smtc_pause` | Pause playback |
| `smtc_play_pause` | Toggle play/pause |
| `smtc_next_track` | Skip to next track |
| `smtc_previous_track` | Go to previous track |
| `smtc_seek` | Seek to position (seconds) |
| `smtc_set_shuffle` | Enable or disable shuffle |
| `smtc_set_repeat` | Set repeat mode: `none`, `track`, or `list` |

All SMTC tools return `{"success": false, "error": "..."}` gracefully on non-Windows systems or if the winrt package is not installed.

### Last.fm Tools

| Tool | Auth needed | Description |
|------|-------------|-------------|
| `lastfm_get_now_playing` | API key | Currently playing track |
| `lastfm_get_recent_tracks` | API key | Listening history |
| `lastfm_get_top_tracks` | API key | Most played tracks by period |
| `lastfm_get_top_artists` | API key | Most played artists by period |
| `lastfm_get_loved_tracks` | API key | Hearted tracks |
| `lastfm_get_similar_artists` | API key | Artists similar to a given one |
| `lastfm_get_track_info` | API key | Tags, wiki, play counts |
| `lastfm_love_track` | Session key | Heart a track |
| `lastfm_unlove_track` | Session key | Unheart a track |
| `lastfm_update_now_playing` | Session key | Push now-playing status |
| `lastfm_scrobble` | Session key | Submit a listen record |

## Installation

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager

### Local Setup

```bash
# Create and activate a virtual environment
uv venv --python 3.11
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Copy and fill in your credentials
cp .env.example .env
# Edit .env with your LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME

# (Optional) Generate a session key for write operations
uv run lastfm_auth.py

# Start the server
uv run deezer_mcp_server.py
```

The server runs on `http://localhost:8000` by default using SSE transport.

### Docker

```bash
# Build and start
docker compose up --build

# Run in background
docker compose up -d

# Stop
docker compose down
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `0.0.0.0` | Host to bind to |
| `MCP_PORT` | `8000` | Port to listen on |
| `MCP_TRANSPORT` | `sse` | Transport type (`sse` or `stdio`) |

## Last.fm Setup

### 1. Get an API key
Register at [last.fm/api/account/create](https://www.last.fm/api/account/create) — it's free and instant.
Copy `LASTFM_API_KEY` and `LASTFM_API_SECRET` into your `.env`.

### 2. Enable scrobbling from Deezer
In the Deezer app: **Settings → Connections → Last.fm** — log in and enable scrobbling.
Now anything you play in Deezer will appear in `lastfm_get_now_playing`.

### 3. (Optional) Generate a session key for write access
Write operations (love/unlove, scrobble, now-playing) need a session key:

```bash
uv run lastfm_auth.py
```

This opens a browser auth page, then writes `LASTFM_SESSION_KEY` and `LASTFM_USERNAME` directly into your `.env`.

## Client Configuration

### AnythingLLM

AnythingLLM supports MCP servers. To connect:

1. Start the Deezer MCP server (`docker compose up -d` or `uv run deezer_mcp_server.py`)
2. In AnythingLLM, go to **Settings → Agent Skills → Custom MCP Servers**
3. Add a new server with the following config:

```json
{
  "mcpServers": {
    "deezer": {
      "type": "streamable",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

If AnythingLLM is running inside Docker and the MCP server is on your host machine, use `http://host.docker.internal:8000/mcp` instead of localhost.

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "deezer": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/sse",
        "--transport",
        "sse-only"
      ]
    }
  }
}
```

### Generic SSE Client

Connect to: `http://localhost:8000/sse`

## Advanced Search Reference

The `advanced_search` tool builds Deezer query syntax from structured parameters:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `artist` | Artist name | `"daft punk"` |
| `album` | Album title | `"random access memories"` |
| `track` | Track title | `"get lucky"` |
| `label` | Record label | `"columbia"` |
| `dur_min` / `dur_max` | Duration range in seconds | `180` / `300` |
| `bpm_min` / `bpm_max` | BPM range | `120` / `140` |

Example combining criteria:
```python
advanced_search({
    "artist": "daft punk",
    "bpm_min": 120,
    "dur_min": 180,
    "limit": 10
})
```

## Limitations

- **Public API only** — no user authentication, no playlist modification
- **Read-only** — cannot create or modify content
- **Rate limiting** — respect Deezer's API rate limits
- **Geo-restrictions** — some content may be unavailable based on region
