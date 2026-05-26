"""
Step 1: Fetch M365 Roadmap data and detect changes.

Pulls from Microsoft's public API, compares with previous run,
annotates each item with change_type (new, status_changed, date_changed, updated).
Outputs items.json for the summariser.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCRIPT_DIR = Path(__file__).parent
CATEGORIES_FILE = SCRIPT_DIR / "categories.json"
SITE_DIR = SCRIPT_DIR / ".." / "site"
OUTPUT_FILE = SITE_DIR / "items.json"
PREVIOUS_STATE_FILE = SITE_DIR / "previous_state.json"
CHANGELOG_FILE = SITE_DIR / "changelog.json"

API_URL = "https://www.microsoft.com/releasecommunications/api/v1/m365"
REQUEST_TIMEOUT = 30


def make_session_with_retries():
    """Build a requests.Session with retry-on-transient-error.

    Added 2026-05-26 after the May 23/24/25 morning runs failed transiently
    on the MS releasecommunications API and there was no retry layer.
    Retries 3 times with exponential backoff (2s, 4s, 8s) on 429 + 5xx.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def load_categories():
    """Load product → UI category mapping."""
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Build reverse lookup: API product name → category info
    product_map = {}
    for cat in config["categories"]:
        for product_name in cat["products"]:
            product_map[product_name] = {
                "id": cat["id"],
                "name": cat["name"],
                "emoji": cat["emoji"],
                "color": cat["color"],
            }

    status_config = config.get("status_config", {})
    return config["categories"], product_map, status_config


def load_previous_state():
    """Load previous run's state for change detection."""
    if not PREVIOUS_STATE_FILE.exists():
        return {}
    try:
        with open(PREVIOUS_STATE_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        return {item["id"]: item for item in items}
    except (json.JSONDecodeError, KeyError):
        return {}


def content_hash(item):
    """Hash the meaningful content fields to detect real changes."""
    fields = (
        str(item.get("title", ""))
        + str(item.get("description", ""))
        + str(item.get("status", ""))
        + str(item.get("publicDisclosureAvailabilityDate", ""))
        + str(item.get("publicPreviewDate", ""))
    )
    return hashlib.md5(fields.encode()).hexdigest()


def parse_ga_date(date_str):
    """Parse 'May CY2026' into sortable '2026-05'. Handles Q-format and bare years."""
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()
    months = {
        "January": "01", "February": "02", "March": "03", "April": "04",
        "May": "05", "June": "06", "July": "07", "August": "08",
        "September": "09", "October": "10", "November": "11", "December": "12",
    }

    # "May CY2026" → "2026-05"
    for month_name, month_num in months.items():
        if month_name in date_str:
            year = date_str.replace(month_name, "").replace("CY", "").strip()
            if year.isdigit():
                return f"{year}-{month_num}"

    # "Q1 CY2026" → "2026-Q1"
    if date_str.startswith("Q") and "CY" in date_str:
        parts = date_str.split()
        if len(parts) >= 2:
            quarter = parts[0]
            year = parts[1].replace("CY", "")
            if year.isdigit():
                return f"{year}-{quarter}"

    # "CY2026" → "2026"
    if "CY" in date_str:
        year = date_str.replace("CY", "").strip()
        if year.isdigit():
            return year

    return None


def map_product_categories(item, product_map):
    """Map API product tags to our consolidated UI categories."""
    products_raw = item.get("tagsContainer", {}).get("products", [])
    product_names = [p["tagName"] for p in products_raw]

    categories_seen = set()
    primary_category = None

    for product_name in product_names:
        cat_info = product_map.get(product_name)
        if cat_info and cat_info["id"] not in categories_seen:
            categories_seen.add(cat_info["id"])
            if primary_category is None:
                primary_category = cat_info

    # Fallback for unmapped products
    if primary_category is None:
        primary_category = {
            "id": "admin",
            "name": "Admin & Platform",
            "emoji": "⚙️",
            "color": "#5C2D91",
        }

    return product_names, primary_category, list(categories_seen)


def detect_changes(current_items, previous_state):
    """Compare current items against previous state and annotate changes."""
    changes_summary = {
        "new_items": 0,
        "status_changes": 0,
        "date_changes": 0,
        "updated_items": 0,
        "removed_items": 0,
        "unchanged": 0,
    }

    current_ids = set()

    for item in current_items:
        item_id = item["id"]
        current_ids.add(item_id)

        prev = previous_state.get(item_id)
        if prev is None:
            item["change_type"] = "new"
            item["previous_status"] = None
            item["previous_ga_date"] = None
            changes_summary["new_items"] += 1

        elif item.get("status") != prev.get("status"):
            item["change_type"] = "status_changed"
            item["previous_status"] = prev.get("status")
            item["previous_ga_date"] = prev.get("publicDisclosureAvailabilityDate")
            changes_summary["status_changes"] += 1

        elif item.get("publicDisclosureAvailabilityDate") != prev.get("publicDisclosureAvailabilityDate"):
            item["change_type"] = "date_changed"
            item["previous_status"] = prev.get("status")
            item["previous_ga_date"] = prev.get("publicDisclosureAvailabilityDate")
            changes_summary["date_changes"] += 1

        elif content_hash(item) != content_hash(prev):
            item["change_type"] = "updated"
            item["previous_status"] = prev.get("status")
            item["previous_ga_date"] = prev.get("publicDisclosureAvailabilityDate")
            changes_summary["updated_items"] += 1

        else:
            item["change_type"] = None
            item["previous_status"] = None
            item["previous_ga_date"] = None
            changes_summary["unchanged"] += 1

    # Detect removed items
    removed_ids = set(previous_state.keys()) - current_ids
    changes_summary["removed_items"] = len(removed_ids)

    return current_items, changes_summary, removed_ids


def transform_item(item, product_map, status_config):
    """Transform a raw API item into our enriched output format."""
    product_names, primary_cat, all_cat_ids = map_product_categories(item, product_map)
    status = item.get("status", "Unknown")
    status_info = status_config.get(status, {"emoji": "⚪", "color": "#999", "order": 99})

    platforms = [p["tagName"] for p in item.get("tagsContainer", {}).get("platforms", [])]
    cloud_instances = [c["tagName"] for c in item.get("tagsContainer", {}).get("cloudInstances", [])]
    release_phases = [r["tagName"] for r in item.get("tagsContainer", {}).get("releasePhase", [])]

    ga_date_raw = item.get("publicDisclosureAvailabilityDate", "")
    preview_date_raw = item.get("publicPreviewDate", "")
    ga_parsed = parse_ga_date(ga_date_raw)

    # Delay detection: GA date is in the past but item isn't Launched
    is_delayed = False
    if ga_parsed and status not in ("Launched", "Cancelled"):
        now_month = datetime.now(timezone.utc).strftime("%Y-%m")
        if len(ga_parsed) >= 7 and ga_parsed < now_month:
            is_delayed = True

    return {
        "id": item["id"],
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "ai_summary": "",
        "status": status,
        "status_emoji": status_info["emoji"],
        "status_color": status_info["color"],
        "status_order": status_info["order"],
        "change_type": item.get("change_type"),
        "previous_status": item.get("previous_status"),
        "previous_ga_date": item.get("previous_ga_date"),
        "is_delayed": is_delayed,
        "ga_date": ga_date_raw,
        "ga_date_parsed": ga_parsed,
        "preview_date": preview_date_raw,
        "preview_date_parsed": parse_ga_date(preview_date_raw),
        "products": product_names,
        "product_category": primary_cat["id"],
        "product_category_name": primary_cat["name"],
        "product_category_emoji": primary_cat["emoji"],
        "product_category_color": primary_cat["color"],
        "all_categories": all_cat_ids,
        "platforms": platforms,
        "cloud_instances": cloud_instances,
        "release_phases": release_phases,
        "more_info_link": item.get("moreInfoLink"),
        "roadmap_url": f"https://www.microsoft.com/microsoft-365/roadmap?id={item['id']}",
        "created": item.get("created", ""),
        "modified": item.get("modified", ""),
    }


def update_changelog(items):
    """Accumulate a per-item changelog across runs."""
    # Load existing changelog
    changelog = {}
    if CHANGELOG_FILE.exists():
        try:
            with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
                changelog = json.load(f)
        except (json.JSONDecodeError, IOError):
            changelog = {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    added = 0

    for item in items:
        change_type = item.get("change_type")
        if not change_type:
            continue

        item_id = str(item["id"])
        if item_id not in changelog:
            changelog[item_id] = []

        # Don't duplicate entries for the same day + event
        existing_dates = {e["date"] + e["event"] for e in changelog[item_id]}
        key = today + change_type
        if key in existing_dates:
            continue

        entry = {
            "date": today,
            "event": change_type,
            "status": item.get("status", ""),
            "ga_date": item.get("ga_date", ""),
        }
        if change_type == "status_changed" and item.get("previous_status"):
            entry["from_status"] = item["previous_status"]
        if change_type == "date_changed" and item.get("previous_ga_date"):
            entry["from_ga_date"] = item["previous_ga_date"]

        changelog[item_id].append(entry)
        added += 1

    with open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        json.dump(changelog, f, ensure_ascii=False)

    print(f"📜 Changelog: {added} new entries, {len(changelog)} items tracked → {CHANGELOG_FILE}")


def main():
    print("📋 M365 Roadmap Fetcher")
    print("=" * 50)

    # Load config
    categories, product_map, status_config = load_categories()
    print(f"📂 {len(categories)} UI categories, {len(product_map)} product mappings")

    # Load previous state for change detection
    previous_state = load_previous_state()
    is_first_run = len(previous_state) == 0
    if is_first_run:
        print("🆕 First run — all items will be marked as new")
    else:
        print(f"📊 Previous state: {len(previous_state)} items")

    # Fetch from API (with retry-on-transient-error — see make_session_with_retries above)
    print(f"\n📡 Fetching from M365 Roadmap API...")
    session = make_session_with_retries()
    try:
        resp = session.get(API_URL, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "M365RoadmapTracker/1.0 (aguidetocloud.com)",
        })
        resp.raise_for_status()
        raw_items = resp.json()
    except requests.RequestException as e:
        print(f"❌ API fetch failed after retries: {e}")
        sys.exit(1)

    print(f"✅ Fetched {len(raw_items)} items from API")

    # Detect changes
    print("\n🔄 Change detection:")
    raw_items, changes_summary, removed_ids = detect_changes(raw_items, previous_state)

    if is_first_run:
        # Don't overwhelm first-run output — skip marking everything as "new"
        # Instead, mark all as None and let the frontend show them normally
        for item in raw_items:
            item["change_type"] = None
            item["previous_status"] = None
            item["previous_ga_date"] = None
        changes_summary = {k: 0 for k in changes_summary}
        print("  ℹ️  First run — skipping change annotations")
    else:
        print(f"  🆕 New items: {changes_summary['new_items']}")
        print(f"  🔄 Status changes: {changes_summary['status_changes']}")
        print(f"  📅 Date changes: {changes_summary['date_changes']}")
        print(f"  ✏️  Content updates: {changes_summary['updated_items']}")
        print(f"  🗑️  Removed items: {changes_summary['removed_items']}")
        print(f"  ⏸️  Unchanged: {changes_summary['unchanged']}")

    # Transform items
    print("\n🔧 Transforming items...")
    items = [transform_item(item, product_map, status_config) for item in raw_items]

    # Sort: changed items first (new > status_changed > date_changed > updated),
    # then by status order, then by modified date
    change_priority = {"new": 0, "status_changed": 1, "date_changed": 2, "updated": 3}
    items.sort(key=lambda x: (
        change_priority.get(x["change_type"], 99),
        x["status_order"],
        x["modified"] or "",
    ), reverse=False)
    # Reverse the modified sort within same priority (newest first)
    items.sort(key=lambda x: (
        change_priority.get(x["change_type"], 99),
        x["status_order"],
    ))

    # Count by status
    status_counts = {}
    for item in items:
        s = item["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # Count by product category
    cat_counts = {}
    for item in items:
        cid = item["product_category"]
        cat_counts[cid] = cat_counts.get(cid, 0) + 1

    # Build product_categories array for frontend
    product_categories = []
    for cat in categories:
        count = cat_counts.get(cat["id"], 0)
        product_categories.append({
            "id": cat["id"],
            "name": cat["name"],
            "emoji": cat["emoji"],
            "color": cat["color"],
            "count": count,
        })
    product_categories.sort(key=lambda x: x["count"], reverse=True)

    active_items = [i for i in items if i["status"] != "Launched"]
    delayed_items = [i for i in items if i.get("is_delayed")]

    print(f"\n📊 Summary:")
    print(f"  Total items: {len(items)}")
    print(f"  Active (non-Launched): {len(active_items)}")
    print(f"  ⚠️  Delayed (past GA date): {len(delayed_items)}")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")

    # Save output
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(items),
        "active_items": len(active_items),
        "delayed_items": len(delayed_items),
        "changes_summary": changes_summary,
        "product_categories": product_categories,
        "status_counts": status_counts,
        "items": items,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Saved {len(items)} items → {OUTPUT_FILE}")

    # Save current raw state for next run's diff
    state_items = []
    for raw in raw_items:
        state_items.append({
            "id": raw["id"],
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "status": raw.get("status", ""),
            "publicDisclosureAvailabilityDate": raw.get("publicDisclosureAvailabilityDate", ""),
            "publicPreviewDate": raw.get("publicPreviewDate", ""),
        })

    with open(PREVIOUS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_items, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved previous state ({len(state_items)} items) → {PREVIOUS_STATE_FILE}")

    # Accumulate changelog (historical record of changes per item)
    update_changelog(items)

    if removed_ids:
        print(f"\n⚠️  {len(removed_ids)} item(s) removed from roadmap: {sorted(removed_ids)[:10]}...")

    print("\n✅ Fetch complete!")


if __name__ == "__main__":
    main()
