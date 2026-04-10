#!/usr/bin/env python3
"""
Slack channel ingest — pull messages + threads from specified channels
and dump as markdown into the local RAG store.

Uses a browser user token (xoxc-* + d cookie, or xoxs-*).

Usage:
    # Set token (grab from browser DevTools → Network tab → any Slack API call → Headers → token param)
    export SLACK_TOKEN="xoxc-..."
    # If using xoxc- token, also need the 'd' cookie:
    export SLACK_COOKIE="d=xoxd-..."

    python3 scripts/slack_ingest.py

Read-only. Never posts, edits, or deletes anything in Slack.

How to get your token from the browser:
    1. Open Slack in browser (app.slack.com)
    2. Open DevTools (F12) → Network tab
    3. Do anything in Slack (switch channel, etc.)
    4. Filter network requests for "api/"
    5. Click any Slack API request → Headers tab
    6. In the request payload/form data, find "token" → copy the xoxc-... value
    7. In the request headers, find "Cookie" → copy the "d=xoxd-..." part
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

CHANNELS = {
    "sx-support-help": "C0327R3TJ20",
    "sx-support-senior-team": "C03P3QJCFGX",
}
OUT_DIR  = Path(__file__).resolve().parent.parent / "workspace" / "out" / "rag" / "SLACK"
RATE_LIMIT = 2.0  # seconds between API calls (conservative to avoid 429s)
MAX_MESSAGES_PER_CHANNEL = 5000  # safety cap
OLDEST_DAYS = 180  # how far back to go (6 months)

SLACK_API = "https://kantata.slack.com/api"


def get_credentials() -> tuple[str, dict]:
    """Get token and headers from environment."""
    token = os.environ.get("SLACK_TOKEN", "").strip()
    if not token:
        print("ERROR: Set SLACK_TOKEN environment variable.")
        print("  See docstring for how to grab it from browser DevTools.")
        sys.exit(1)

    headers = {}
    cookie = os.environ.get("SLACK_COOKIE", "").strip()
    if cookie:
        headers["Cookie"] = cookie

    return token, headers


def slack_api(client: httpx.Client, method: str, headers: dict, token: str, params: dict) -> dict:
    """Make a Slack API POST request (form-encoded with token in body)."""
    form_data = {"token": token}
    form_data.update(params)
    resp = client.post(f"{SLACK_API}/{method}", headers=headers, data=form_data, timeout=30.0)
    # Handle 429 before raise_for_status
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 10))
        print(f"    Rate limited (429), waiting {retry_after}s...")
        time.sleep(retry_after)
        return slack_api(client, method, headers, token, params)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        error = data.get("error", "unknown")
        if error == "ratelimited":
            retry_after = int(resp.headers.get("Retry-After", 10))
            print(f"    Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            return slack_api(client, method, headers, token, params)
        raise RuntimeError(f"Slack API error: {error} — {data.get('response_metadata', {}).get('messages', [])}")
    return data


def find_channel_id(client: httpx.Client, headers: dict, token: str, channel_name: str) -> str | None:
    """Find channel ID by name, paginating through conversations.list."""
    cursor = None
    while True:
        params = {"types": "public_channel,private_channel", "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        data = slack_api(client, "conversations.list", headers, token, params)
        for ch in data.get("channels", []):
            if ch.get("name") == channel_name:
                return ch["id"]
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(RATE_LIMIT)
    return None


def fetch_messages(client: httpx.Client, headers: dict, token: str, channel_id: str,
                   channel_name: str, oldest_ts: str) -> list[dict]:
    """Fetch all messages from a channel, newest first."""
    messages = []
    cursor = None
    page = 0

    while True:
        params = {
            "channel": channel_id,
            "limit": "200",
            "oldest": oldest_ts,
        }
        if cursor:
            params["cursor"] = cursor

        data = slack_api(client, "conversations.history", headers, token, params)
        batch = data.get("messages", [])
        messages.extend(batch)
        page += 1
        print(f"    Page {page}: {len(batch)} messages (total: {len(messages)})")

        if len(messages) >= MAX_MESSAGES_PER_CHANNEL:
            print(f"    Hit cap of {MAX_MESSAGES_PER_CHANNEL} messages")
            break

        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor or not data.get("has_more"):
            break
        time.sleep(RATE_LIMIT)

    return messages


def fetch_thread(client: httpx.Client, headers: dict, token: str, channel_id: str,
                 thread_ts: str) -> list[dict]:
    """Fetch all replies in a thread."""
    replies = []
    cursor = None

    while True:
        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor

        data = slack_api(client, "conversations.replies", headers, token, params)
        batch = data.get("messages", [])
        # First message in replies is the parent — skip it
        if not cursor and batch:
            batch = batch[1:]
        replies.extend(batch)

        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor or not data.get("has_more"):
            break
        time.sleep(RATE_LIMIT)

    return replies


def format_ts(ts: str) -> str:
    """Convert Slack timestamp to readable date."""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return ts


def sanitize_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80].rstrip(". ")


def scrub_user_ids(text: str) -> str:
    """Replace <@U...> mentions with [user] for privacy."""
    return re.sub(r'<@U[A-Z0-9]+>', '[user]', text or "")


def write_channel_markdown(channel_name: str, messages: list[dict],
                           threads: dict[str, list[dict]]) -> Path:
    """Write channel messages + threads as a single markdown file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Sort messages oldest first
    messages.sort(key=lambda m: float(m.get("ts", "0")))

    date_range_start = format_ts(messages[0]["ts"])[:10] if messages else "?"
    date_range_end = format_ts(messages[-1]["ts"])[:10] if messages else "?"

    lines = [
        f"# #{channel_name}",
        f"**Messages:** {len(messages)} | **Threads:** {len(threads)}",
        f"**Date range:** {date_range_start} to {date_range_end}",
        f"**Ingested:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "---",
        "",
    ]

    thread_count = 0
    for msg in messages:
        ts = format_ts(msg.get("ts", ""))
        user = msg.get("user", msg.get("username", "?"))
        text = scrub_user_ids(msg.get("text", ""))

        if not text.strip():
            continue

        lines.append(f"### {ts} — {user}")
        lines.append("")
        lines.append(text)

        # Append thread replies inline
        msg_ts = msg.get("ts", "")
        if msg_ts in threads and threads[msg_ts]:
            thread_replies = threads[msg_ts]
            thread_count += 1
            lines.append("")
            lines.append(f"> **Thread ({len(thread_replies)} replies):**")
            for reply in thread_replies:
                r_ts = format_ts(reply.get("ts", ""))
                r_user = reply.get("user", reply.get("username", "?"))
                r_text = scrub_user_ids(reply.get("text", ""))
                if r_text.strip():
                    # Indent thread replies with >
                    for line in r_text.split("\n"):
                        lines.append(f"> [{r_ts}] {r_user}: {line}")

        lines.append("")
        lines.append("---")
        lines.append("")

    out_path = OUT_DIR / f"{channel_name}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_daily_chunks(channel_name: str, messages: list[dict],
                       threads: dict[str, list[dict]]) -> list[Path]:
    """Also write daily chunk files for more granular RAG matching."""
    chunk_dir = OUT_DIR / channel_name
    chunk_dir.mkdir(parents=True, exist_ok=True)

    # Group messages by date
    by_date: dict[str, list[dict]] = {}
    for msg in messages:
        date_str = format_ts(msg.get("ts", ""))[:10]
        by_date.setdefault(date_str, []).append(msg)

    paths = []
    for date_str, day_msgs in sorted(by_date.items()):
        day_msgs.sort(key=lambda m: float(m.get("ts", "0")))
        lines = [f"# #{channel_name} — {date_str}", ""]

        for msg in day_msgs:
            ts = format_ts(msg.get("ts", ""))
            user = msg.get("user", msg.get("username", "?"))
            text = scrub_user_ids(msg.get("text", ""))
            if not text.strip():
                continue
            lines.append(f"**{ts} — {user}**")
            lines.append(text)

            msg_ts = msg.get("ts", "")
            if msg_ts in threads and threads[msg_ts]:
                for reply in threads[msg_ts]:
                    r_ts = format_ts(reply.get("ts", ""))
                    r_user = reply.get("user", reply.get("username", "?"))
                    r_text = scrub_user_ids(reply.get("text", ""))
                    if r_text.strip():
                        lines.append(f"> [{r_ts}] {r_user}: {r_text}")

            lines.append("")

        path = chunk_dir / f"{date_str}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)

    return paths


def main():
    token, headers = get_credentials()
    print(f"Token type: {token[:5]}...")
    print(f"Channels: {list(CHANNELS.keys())}")
    print(f"Output: {OUT_DIR}")
    print(f"Lookback: {OLDEST_DAYS} days")
    print()

    oldest_ts = str(time.time() - (OLDEST_DAYS * 86400))

    with httpx.Client() as client:
        # Verify auth
        print("Verifying auth...", end=" ")
        try:
            auth = slack_api(client, "auth.test", headers, token, {})
            print(f"OK — user={auth.get('user', '?')}, team={auth.get('team', '?')}")
        except Exception as e:
            print(f"FAILED: {e}")
            sys.exit(1)

        for channel_name, channel_id in CHANNELS.items():
            print(f"\n{'='*60}")
            print(f"Channel: #{channel_name} ({channel_id})")
            print(f"{'='*60}")

            # Fetch messages
            print(f"  Fetching messages (oldest={OLDEST_DAYS} days)...")
            messages = fetch_messages(client, headers, token, channel_id, channel_name, oldest_ts)
            print(f"  Total messages: {len(messages)}")

            if not messages:
                print("  No messages — skipping")
                continue

            # Find messages with threads (reply_count > 0)
            threaded = [m for m in messages if int(m.get("reply_count", 0)) > 0]
            print(f"  Messages with threads: {len(threaded)}")

            # Fetch threads
            threads: dict[str, list[dict]] = {}
            for i, msg in enumerate(threaded, 1):
                thread_ts = msg["ts"]
                reply_count = int(msg.get("reply_count", 0))
                print(f"    Thread [{i}/{len(threaded)}] ({reply_count} replies)...", end=" ", flush=True)
                try:
                    replies = fetch_thread(client, headers, token, channel_id, thread_ts)
                    threads[thread_ts] = replies
                    print(f"got {len(replies)}")
                except Exception as e:
                    print(f"FAILED: {e}")
                time.sleep(RATE_LIMIT)

            total_thread_msgs = sum(len(r) for r in threads.values())
            print(f"  Total thread replies: {total_thread_msgs}")

            # Write output
            print(f"  Writing markdown...")
            main_path = write_channel_markdown(channel_name, messages, threads)
            print(f"    Main file: {main_path} ({main_path.stat().st_size / 1024:.0f} KB)")

            daily_paths = write_daily_chunks(channel_name, messages, threads)
            print(f"    Daily chunks: {len(daily_paths)} files in {OUT_DIR / channel_name}/")

    print(f"\n{'='*60}")
    print(f"Done. Output in {OUT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
