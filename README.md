# Deezer MCP Server

A Model Context Protocol (MCP) server for the Deezer API — search and explore music content (tracks, artists, albums, playlists, charts, and genres).

![Example usage](exemple.png)

## Features

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
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

If AnythingLLM is running inside Docker and the MCP server is on your host machine, use `http://host.docker.internal:8000/sse` instead of localhost.

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
