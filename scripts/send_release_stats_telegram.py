#!/usr/bin/env python3
"""
Send feature cache release statistics to Telegram.

Collects file sizes, row counts, and metadata from generated cache files
and sends a formatted report to a Telegram chat.
"""

import html
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests


def get_file_size_mb(file_path: str) -> float:
    """Get file size in MB."""
    if not os.path.exists(file_path):
        return 0.0
    return os.path.getsize(file_path) / (1024 * 1024)


def get_parquet_row_count(file_path: str) -> int:
    """Get row count from parquet file."""
    if not os.path.exists(file_path):
        return 0
    try:
        df = pd.read_parquet(file_path, columns=[])  # Read only metadata
        return len(df)
    except Exception as e:
        print(f"Warning: Could not read row count from {file_path}: {e}", file=sys.stderr)
        return 0


def load_meta_json(file_path: str) -> dict[str, Any]:
    """Load metadata from JSON file."""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load metadata from {file_path}: {e}", file=sys.stderr)
        return {}


def format_filesize(size_mb: float) -> str:
    """Format file size for display."""
    if size_mb < 1:
        return f"{size_mb * 1024:.1f}KB"
    return f"{size_mb:.2f}MB"


def format_number(num: int) -> str:
    """Format number with thousand separators."""
    return f"{num:,}"


def build_validation_failure_message(cache_dir: str = "cache", validation_errors: list[str] = None) -> str:
    """Build Telegram message for validation failure."""
    message_lines = ["❌ <b>Feature Cache Validation Failed</b>", ""]
    message_lines.append("<b>⚠️ Release Blocked - Data Quality Issues Detected</b>")
    message_lines.append("")
    
    # List validation errors with proper HTML escaping
    if validation_errors:
        message_lines.append("<b>🔍 Validation Errors:</b>")
        for i, error in enumerate(validation_errors, 1):
            # Use html.escape for proper HTML escaping
            error_escaped = html.escape(error)
            message_lines.append(f"  {i}. {error_escaped}")
        message_lines.append("")
    
    # Show file statistics for context
    message_lines.append("<b>📊 Generated Files:</b>")
    files_info = [
        ("krx_stock_master.parquet", "KRX Stock Master"),
        ("korea_universe_feature_frame.parquet", "Universe Features"),
        ("korea_industry_feature_frame.parquet", "Industry Features"),
    ]
    
    for filename, label in files_info:
        file_path = os.path.join(cache_dir, filename)
        if os.path.exists(file_path):
            size_mb = get_file_size_mb(file_path)
            row_count = get_parquet_row_count(file_path)
            size_str = format_filesize(size_mb)
            if row_count > 0:
                message_lines.append(f"  • {label}: {size_str} ({format_number(row_count)} rows)")
            else:
                message_lines.append(f"  • {label}: {size_str}")
    
    message_lines.append("")
    
    # Show ticker count if available in metadata
    universe_meta_path = os.path.join(cache_dir, "korea_universe_feature_frame.meta.json")
    universe_meta = load_meta_json(universe_meta_path)
    if universe_meta and "ticker_count" in universe_meta:
        ticker_count = universe_meta.get("ticker_count")
        message_lines.append(f"<b>📈 Ticker Count:</b> {format_number(ticker_count)}")
        message_lines.append("")
    
    message_lines.append("⛔ <b>Action Required:</b> Fix data quality issues before releasing.")
    
    return "\n".join(message_lines)


def build_telegram_message(cache_dir: str = "cache") -> str:
    """Build Telegram message with release statistics."""
    message_lines = ["🎉 <b>Feature Cache Release Report</b>", ""]

    # File statistics
    message_lines.append("<b>📦 Files:</b>")

    files_info = [
        ("krx_stock_master.parquet", "KRX Stock Master"),
        ("korea_universe_feature_frame.parquet", "Universe Features"),
        ("korea_industry_feature_frame.parquet", "Industry Features"),
    ]

    for filename, label in files_info:
        file_path = os.path.join(cache_dir, filename)
        if os.path.exists(file_path):
            size_mb = get_file_size_mb(file_path)
            row_count = get_parquet_row_count(file_path)
            size_str = format_filesize(size_mb)
            if row_count > 0:
                message_lines.append(f"  • {label}: {size_str} ({format_number(row_count)} rows)")
            else:
                message_lines.append(f"  • {label}: {size_str}")

    message_lines.append("")

    # Universe feature metadata
    universe_meta_path = os.path.join(cache_dir, "korea_universe_feature_frame.meta.json")
    universe_meta = load_meta_json(universe_meta_path)
    if universe_meta:
        message_lines.append("<b>🌐 Universe Features:</b>")
        if "date_range" in universe_meta:
            date_range = universe_meta.get("date_range", {})
            message_lines.append(f"  • Date range: {date_range.get('start')} ~ {date_range.get('end')}")
        if "ticker_count" in universe_meta:
            ticker_count = universe_meta.get("ticker_count")
            message_lines.append(f"  • Tickers: {format_number(ticker_count)}")
        if "successful_ticker_count" in universe_meta:
            success_count = universe_meta.get("successful_ticker_count")
            total_count = universe_meta.get("ticker_count", 1)
            success_rate = (success_count / total_count * 100) if total_count > 0 else 0
            message_lines.append(f"  • Successful: {format_number(success_count)}/{format_number(total_count)} ({success_rate:.1f}%)")
        if "columns" in universe_meta:
            col_count = len(universe_meta.get("columns", []))
            message_lines.append(f"  • Columns: {col_count}")
        message_lines.append("")

    # Industry feature metadata
    industry_meta_path = os.path.join(cache_dir, "korea_industry_feature_frame.meta.json")
    industry_meta = load_meta_json(industry_meta_path)
    if industry_meta:
        message_lines.append("<b>🏭 Industry Features:</b>")
        if "date_range" in industry_meta:
            date_range = industry_meta.get("date_range", {})
            message_lines.append(f"  • Date range: {date_range.get('start')} ~ {date_range.get('end')}")
        if "industry_count" in industry_meta:
            industry_count = industry_meta.get("industry_count")
            if isinstance(industry_count, dict):
                message_lines.append(f"  • Industries: Large={industry_count.get('large', 0)}, Mid={industry_count.get('mid', 0)}, Small={industry_count.get('small', 0)}")
            else:
                message_lines.append(f"  • Industries: {format_number(industry_count)}")
        if "columns" in industry_meta:
            col_count = len(industry_meta.get("columns", []))
            message_lines.append(f"  • Columns: {col_count}")
        message_lines.append("")

    # Summary
    message_lines.append("✅ Release ready for distribution!")

    return "\n".join(message_lines)


def send_telegram_message(message: str, bot_token: str, chat_id: str) -> bool:
    """Send message to Telegram."""
    if not bot_token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200:
            print("Telegram message sent successfully")
            return True
        else:
            print(f"Error sending Telegram message: {response.status_code}", file=sys.stderr)
            print(response.text, file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error sending Telegram message: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Send release statistics to Telegram")
    parser.add_argument("--cache-dir", default="cache", help="Cache directory")
    parser.add_argument("--bot-token", help="Telegram bot token (or TELEGRAM_BOT_TOKEN env var)")
    parser.add_argument("--chat-id", help="Telegram chat ID (or TELEGRAM_CHAT_ID env var)")
    parser.add_argument("--dry-run", action="store_true", help="Print message without sending")
    parser.add_argument("--validation-failed", action="store_true", help="Send validation failure message")
    parser.add_argument("--validation-errors", help="Validation error messages (one per line)")

    args = parser.parse_args()

    # Get credentials from arguments or environment
    bot_token = args.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    # Build message
    if args.validation_failed:
        # Parse validation errors from argument (newline-separated)
        validation_errors = []
        if args.validation_errors:
            validation_errors = args.validation_errors.strip().split("\n")
        message = build_validation_failure_message(args.cache_dir, validation_errors)
    else:
        message = build_telegram_message(args.cache_dir)
    
    print(message)

    if args.dry_run:
        print("\n[DRY RUN] Message not sent to Telegram")
        sys.exit(0)

    # Send message
    if not send_telegram_message(message, bot_token, chat_id):
        sys.exit(1)
