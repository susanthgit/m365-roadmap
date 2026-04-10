"""
Step 2: Summarise roadmap items using Azure OpenAI (GPT-4o mini).

Reads items.json, adds AI summaries for new/changed items,
caches summaries to avoid re-processing unchanged items.
Outputs summaries.json.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import AzureOpenAI

SCRIPT_DIR = Path(__file__).parent
SITE_DIR = SCRIPT_DIR / ".." / "site"
INPUT_FILE = SITE_DIR / "items.json"
OUTPUT_FILE = SITE_DIR / "summaries.json"
CACHE_FILE = SITE_DIR / "summaries_cache.json"

# Azure OpenAI configuration (same endpoint as AI News)
AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://ainews-openai.openai.azure.com/")
AZURE_TOKEN = os.environ.get("AZURE_OPENAI_TOKEN", "")
DEPLOYMENT = "gpt-4o-mini"
API_VERSION = "2024-10-21"

BATCH_SIZE = 10

SYSTEM_PROMPT = """You are an IT admin's assistant summarising Microsoft 365 roadmap items.

For EACH item, produce:
1. **summary** — A concise 1-2 sentence summary (under 50 words) in plain English. State what's changing and who it affects. Avoid marketing language.
2. **impact** — One of: "high" (affects most users or is a major change), "medium" (affects specific roles or workflows), "low" (minor improvement or niche feature).

Rules:
- Write for IT admins and Microsoft 365 administrators
- Use simple, direct language — no buzzwords
- Focus on the WHAT and WHO, not the WHY
- If the item mentions specific products (Teams, Outlook, etc.), lead with that
- If it mentions admin controls, mention that

Return your response as a JSON array of objects in the SAME order as the input.
Each object: {"index": <number>, "summary": "<text>", "impact": "high|medium|low"}
Return ONLY the JSON array, no other text."""


def cache_key(item):
    """Generate a cache key from item ID + description hash."""
    desc_hash = hashlib.md5(item.get("description", "").encode()).hexdigest()[:12]
    return f"{item['id']}:{desc_hash}"


def load_cache():
    """Load the summaries cache."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_cache(cache):
    """Save the summaries cache."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def summarise_batch(client, batch):
    """Summarise a batch of items in a single API call."""
    items_text = ""
    for i, (idx, title, description) in enumerate(batch):
        items_text += f"\n---\nItem {i}:\nTitle: {title}\nDescription: {description[:600]}\n"

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Summarise these {len(batch)} M365 roadmap items:\n{items_text}"},
            ],
            max_tokens=200 * len(batch),
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        print("    ⚠️  JSON parse failed, retrying one-by-one")
        return None
    except Exception as e:
        print(f"    ❌ Batch failed: {e}")
        return None


def summarise_single(client, title, description):
    """Fallback: summarise one item at a time."""
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are an IT admin's assistant. Return a JSON object: {\"summary\": \"1-2 sentence plain English summary under 50 words\", \"impact\": \"high|medium|low\"}. Return ONLY the JSON."},
                {"role": "user", "content": f"Title: {title}\nDescription: {description[:600]}\n\nSummarise this M365 roadmap item."},
            ],
            max_tokens=150,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        return json.loads(raw)
    except Exception as e:
        print(f"    ❌ Summary failed: {e}")
        return {"summary": "", "impact": "medium"}


def main():
    print("🤖 M365 Roadmap Summariser (Azure OpenAI — GPT-4o mini)")
    print("=" * 60)

    if not AZURE_TOKEN:
        print("⚠️  AZURE_OPENAI_TOKEN not set — running in cache-only mode")
        print("   Existing cached summaries will be applied, but no new ones generated.")
        print("   To generate: az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv")
        client = None
    else:
        client = AzureOpenAI(
            azure_endpoint=AZURE_ENDPOINT,
            api_key=AZURE_TOKEN,
            api_version=API_VERSION,
        )

    if not INPUT_FILE.exists():
        print(f"❌ No items found at {INPUT_FILE}")
        print("   Run fetch_roadmap.py first.")
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"📋 {len(items)} items loaded")

    # Load cache
    cache = load_cache()
    print(f"📦 Cache: {len(cache)} existing summaries")

    # Apply cached summaries and find items that need new ones
    to_summarise = []
    cache_hits = 0

    for i, item in enumerate(items):
        key = cache_key(item)
        if key in cache:
            item["ai_summary"] = cache[key]["summary"]
            item["impact"] = cache[key].get("impact", "medium")
            cache_hits += 1
        elif item.get("change_type") is not None or len(cache) == 0:
            # New/changed items, or first run (cache empty) — need summarisation
            to_summarise.append((i, item["title"], item["description"]))
        else:
            # Unchanged item with no cache entry — still try to summarise
            to_summarise.append((i, item["title"], item["description"]))

    print(f"✅ Cache hits: {cache_hits}")
    print(f"📝 Need summarisation: {len(to_summarise)}")

    if to_summarise and client is None:
        print(f"⚠️  Skipping {len(to_summarise)} items (no API token)")
        to_summarise = []

    # Process in batches
    summarised = 0
    api_calls = 0

    for batch_start in range(0, len(to_summarise), BATCH_SIZE):
        batch = to_summarise[batch_start:batch_start + BATCH_SIZE]
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (len(to_summarise) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  📦 Batch {batch_num}/{total_batches} ({len(batch)} items)...", end=" ")

        results = summarise_batch(client, batch)
        api_calls += 1

        if results and isinstance(results, list):
            # Map by AI-returned index field
            index_map = {}
            for result_item in results:
                if isinstance(result_item, dict):
                    idx = result_item.get("index")
                    if idx is not None and 0 <= idx < len(batch):
                        index_map[idx] = result_item

            for j in range(len(batch)):
                result_item = index_map.get(j) or (results[j] if j < len(results) and isinstance(results[j], dict) else {})
                summary = result_item.get("summary", "")
                impact = result_item.get("impact", "medium")
                if summary:
                    orig_idx = batch[j][0]
                    items[orig_idx]["ai_summary"] = summary
                    items[orig_idx]["impact"] = impact
                    # Update cache
                    key = cache_key(items[orig_idx])
                    cache[key] = {
                        "summary": summary,
                        "impact": impact,
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }
                    summarised += 1
            print(f"✅ ({len(index_map) or len(results)} summaries)")
        else:
            # Fallback: summarise individually
            print("⚠️  falling back to individual mode")
            for idx, title, description in batch:
                result = summarise_single(client, title, description)
                api_calls += 1
                if isinstance(result, dict) and result.get("summary"):
                    items[idx]["ai_summary"] = result["summary"]
                    items[idx]["impact"] = result.get("impact", "medium")
                    key = cache_key(items[idx])
                    cache[key] = {
                        "summary": result["summary"],
                        "impact": result.get("impact", "medium"),
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }
                    summarised += 1
                time.sleep(0.5)

        time.sleep(1)

    # Save updated cache
    save_cache(cache)
    print(f"\n💾 Cache updated: {len(cache)} total entries")

    # Save output
    data["items"] = items
    data["summarised"] = cache_hits + summarised
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done: {cache_hits} cached + {summarised} new = {cache_hits + summarised} total summaries")
    print(f"   🔄 API calls used: {api_calls}")
    print(f"   Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
