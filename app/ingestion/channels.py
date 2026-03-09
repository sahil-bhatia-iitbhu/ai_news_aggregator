"""Sample list of YouTube AI channels for testing the ingestion pipeline."""

# Channel ID -> display name. Get channel ID from channel page URL or API.
# These are well-known AI-focused YouTube channels.
SAMPLE_AI_CHANNELS: list[dict[str, str]] = [
    {
        "channel_id": "UCbfYPyITQ-7l4upoX8nvctg",
        "name": "Two Minute Papers",
    },
    {
        "channel_id": "UCZHmQk67mSJgfCCTn7xBfew",
        "name": "Yannic Kilcher",
    },
    {
        "channel_id": "UCNJ1Ymd5yFuUPtn21xtRbbw",
        "name": "AI Explained",
    },
    {
        "channel_id": "UCzCsyvyrq38R6TnztEzOmgg",
        "name": "ByteMonk",
    },
    {
        "channel_id": "UCdEov4L0bpJ_h6W3sJxkfUA",
        "name": "Vizuara AI Labs",
    },
]
