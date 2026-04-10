"""
Step 3: Generate data files for the Hugo frontend.

Reads summaries.json and produces:
- latest.json  — current snapshot (active items only by default)
- weekly.json  — items changed in the last 7 days
- monthly.json — items changed in the current month
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SITE_DIR = SCRIPT_DIR / ".." / "site"
INPUT_FILE = SITE_DIR / "summaries.json"
DATA_DIR = SITE_DIR / "data"


def parse_dt(date_str):
    """Parse ISO datetime string into timezone-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.rstrip("Z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def archive_daily(data, today_str):
    """Archive today's data for historical tracking."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = DATA_DIR / f"{today_str}.json"

    # Save just the items (not full metadata) to keep archives small
    items = data.get("items", [])
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)

    print(f"  📦 Archived {len(items)} items → {archive_path}")
    return archive_path


def load_archived_items(date_str):
    """Load items from an archived data file."""
    path = DATA_DIR / f"{date_str}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def get_archived_dates():
    """Find all archived daily data files and return sorted dates."""
    if not DATA_DIR.exists():
        return []
    dates = sorted(f.stem for f in DATA_DIR.glob("*.json"))
    return dates


def slim_item(item):
    """Strip heavy fields for the frontend JSON. Keep only what the JS needs."""
    return {
        "id": item["id"],
        "title": item.get("title", ""),
        "ai_summary": item.get("ai_summary", ""),
        "status": item.get("status", ""),
        "status_order": item.get("status_order", 99),
        "change_type": item.get("change_type"),
        "previous_status": item.get("previous_status"),
        "previous_ga_date": item.get("previous_ga_date"),
        "ga_date": item.get("ga_date", ""),
        "ga_date_parsed": item.get("ga_date_parsed"),
        "products": item.get("products", []),
        "product_category": item.get("product_category", ""),
        "product_category_name": item.get("product_category_name", ""),
        "all_categories": item.get("all_categories", []),
        "platforms": item.get("platforms", []),
        "roadmap_url": item.get("roadmap_url", ""),
        "modified": item.get("modified", ""),
    }


def generate_latest(data):
    """Generate latest.json — the main data file for the frontend."""
    items = data.get("items", [])

    # Sort: Rolling out first, then In development, then Launched, then Cancelled
    # Within each status, sort by modified date (newest first)
    items.sort(key=lambda x: (
        x.get("status_order", 99),
        -(parse_dt(x.get("modified")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
    ))

    output = {
        "generated_at": data.get("generated_at", datetime.now(timezone.utc).isoformat()),
        "total_items": data.get("total_items", len(items)),
        "active_items": data.get("active_items", len([i for i in items if i["status"] != "Launched"])),
        "summarised": data.get("summarised", 0),
        "changes_summary": data.get("changes_summary", {}),
        "product_categories": data.get("product_categories", []),
        "status_counts": data.get("status_counts", {}),
        "items": [slim_item(i) for i in items],
    }

    output_path = SITE_DIR / "latest.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    active_count = output["active_items"]
    print(f"  ✅ Latest ({len(items)} total, {active_count} active) → {output_path}")
    return output_path


def generate_weekly(data):
    """Generate weekly.json — items that changed in the last 7 days."""
    all_dates = get_archived_dates()
    recent_dates = all_dates[-7:] if all_dates else []

    # Collect unique items from last 7 days of archives
    seen_ids = set()
    weekly_items = []

    if recent_dates:
        for date_str in recent_dates:
            for item in load_archived_items(date_str):
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    weekly_items.append(item)
    else:
        # No archives yet — use current items that have changes
        for item in data.get("items", []):
            if item.get("change_type") is not None:
                weekly_items.append(item)

    # Also include items from current run that were modified recently
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for item in data.get("items", []):
        if item["id"] not in seen_ids:
            modified = parse_dt(item.get("modified"))
            if modified and modified > cutoff:
                seen_ids.add(item["id"])
                weekly_items.append(item)

    # Sort by modified date (newest first)
    weekly_items.sort(
        key=lambda x: parse_dt(x.get("modified")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Separate changed items for the hero section
    changed_items = [i for i in weekly_items if i.get("change_type") is not None]

    date_range = f"{recent_dates[0]} to {recent_dates[-1]}" if recent_dates else "current"

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": date_range,
        "total_items": len(weekly_items),
        "changed_items_count": len(changed_items),
        "product_categories": data.get("product_categories", []),
        "items": [slim_item(i) for i in weekly_items],
    }

    output_path = SITE_DIR / "weekly.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"  ✅ Weekly ({len(weekly_items)} items, {len(changed_items)} changed) → {output_path}")
    return output_path


def generate_monthly(data):
    """Generate monthly.json — items that changed in the current month."""
    all_dates = get_archived_dates()
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    month_dates = [d for d in all_dates if d.startswith(current_month)]

    seen_ids = set()
    monthly_items = []

    if month_dates:
        for date_str in month_dates:
            for item in load_archived_items(date_str):
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    monthly_items.append(item)
    else:
        # No archives yet — use all current items
        monthly_items = list(data.get("items", []))

    # Also include recently modified items from current data
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for item in data.get("items", []):
        if item["id"] not in seen_ids:
            modified = parse_dt(item.get("modified"))
            if modified and modified > month_start:
                seen_ids.add(item["id"])
                monthly_items.append(item)

    monthly_items.sort(
        key=lambda x: parse_dt(x.get("modified")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    month_display = datetime.now(timezone.utc).strftime("%B %Y")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": month_display,
        "total_items": len(monthly_items),
        "product_categories": data.get("product_categories", []),
        "items": [slim_item(i) for i in monthly_items],
    }

    output_path = SITE_DIR / "monthly.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"  ✅ Monthly ({len(monthly_items)} items) → {output_path}")
    return output_path


def main():
    print("📊 M365 Roadmap Data Generator")
    print("=" * 50)

    if not INPUT_FILE.exists():
        print(f"❌ No summaries found at {INPUT_FILE}")
        print("   Run summarise.py first.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"📋 {len(items)} items to process")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Archive today's data
    print("\n📦 Archiving:")
    archive_daily(data, today_str)

    # 2. Generate latest.json
    print("\n📅 Latest:")
    generate_latest(data)

    # 3. Generate weekly digest
    print("\n📰 Weekly:")
    generate_weekly(data)

    # 4. Generate monthly digest
    print("\n📊 Monthly:")
    generate_monthly(data)

    print("\n🎉 Data generation complete!")


if __name__ == "__main__":
    main()
