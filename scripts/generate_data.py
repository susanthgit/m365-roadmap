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
        "is_delayed": item.get("is_delayed", False),
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

    # 5. Generate RSS feed
    print("\n📡 RSS:")
    generate_rss(data)

    print("\n🎉 Data generation complete!")


def generate_rss(data):
    """Generate an RSS feed of roadmap items for subscribers."""
    from xml.sax.saxutils import escape as xml_escape

    items = data.get("items", [])
    # Focus on active items with changes, then newest active items
    changed = [i for i in items if i.get("change_type")]
    active = [i for i in items if i["status"] != "Launched"]
    # Combine: changed first, then recent active, capped at 100
    seen = set()
    rss_items = []
    for i in changed + active:
        if i["id"] not in seen:
            seen.add(i["id"])
            rss_items.append(i)
        if len(rss_items) >= 100:
            break

    xml_items = ""
    for item in rss_items:
        title = xml_escape(item.get("title", ""))
        link = xml_escape(item.get("roadmap_url", f"https://www.microsoft.com/microsoft-365/roadmap?id={item['id']}"))
        status = xml_escape(item.get("status", ""))
        summary = xml_escape(item.get("ai_summary", "") or item.get("description", "")[:300])
        ga_date = xml_escape(item.get("ga_date", ""))
        products = ", ".join(item.get("products", []))
        change = item.get("change_type", "")
        change_prefix = f"[{change.upper()}] " if change else ""

        desc = f"{change_prefix}{summary}"
        if ga_date:
            desc += f" (GA: {ga_date})"
        if products:
            desc += f" — {xml_escape(products)}"

        xml_items += f"""    <item>
      <title>{xml_escape(change_prefix)}{title}</title>
      <link>{link}</link>
      <description>{xml_escape(desc)}</description>
      <category>{xml_escape(status)}</category>
      <category>{xml_escape(item.get('product_category_name', ''))}</category>
      <guid isPermaLink="false">{item['id']}</guid>
    </item>\n"""

    now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>M365 Roadmap Tracker — A Guide to Cloud &amp; AI</title>
    <link>https://www.aguidetocloud.com/m365-roadmap/</link>
    <description>Daily Microsoft 365 roadmap updates — new features, status changes, and AI summaries</description>
    <language>en</language>
    <lastBuildDate>{now_str}</lastBuildDate>
    <atom:link href="https://www.aguidetocloud.com/data/roadmap/feed.xml" rel="self" type="application/rss+xml"/>
{xml_items}  </channel>
</rss>"""

    rss_path = SITE_DIR / "feed.xml"
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"  ✅ RSS feed ({len(rss_items)} items) → {rss_path}")


if __name__ == "__main__":
    main()
