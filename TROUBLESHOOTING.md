# Troubleshooting Log

This document records significant bugs encountered during development and the root causes found.

---

## Chromosome Lens Visualization — Revert and Click Issues

**Symptom:** After selecting a chromosome with full analysis data, the lens visualization would intermittently revert to a "Run or finish full analysis" placeholder state. The ideogram was also non-clickable after page refresh or after navigating between chromosomes.

Three separate bugs were contributing to the same symptom.

---

### Bug 1 — Stale SVG reference in click handler

**Symptom:** Lens not clickable after hard refresh or after any zoom-in/zoom-out cycle.

**Root cause:** `attachGenomeTrackEvents` captured the SVG element in a closure at listener-creation time:

```javascript
const svg = selectedChromosomeVisual.querySelector("svg");  // captured once
...
function inIdeogram(e) {
    const r = svg.getBoundingClientRect();  // stale reference
```

Every time `renderSelectedChromosomeVisual()` or `renderZoomedGenomeTrack()` was called, it replaced `selectedChromosomeVisual.innerHTML`. This detached the captured `svg` element from the DOM. A detached element returns `{width: 0, height: 0}` from `getBoundingClientRect()`, causing `inIdeogram()` to always return `false` — so every click was silently dropped.

**Fix:** Changed to query the live DOM at event time:

```javascript
function currentSvg() {
    return selectedChromosomeVisual.querySelector("svg");
}
function inIdeogram(e) {
    const s = currentSvg();
    if (!s || !s.getBoundingClientRect().width) return false;
    const r = s.getBoundingClientRect();
    ...
}
```

---

### Bug 2 — Background Athena fetch overwriting the overview after zoom-out

**Symptom:** User zooms into a region → clicks ← Zoom out → overview briefly appears → then the zoomed view reappears on its own.

**Root cause:** `zoomToGenomeRegion` rendered the zoomed frame immediately and then started an async Athena query for individual ORF/CpG positions (chr5 has ~5.8M pattern rows — query takes 30–55 seconds). When the user clicked zoom-out before the query finished, `renderSelectedChromosomeVisual()` restored the overview. But 30–55 seconds later the background query completed, called `renderZoomedGenomeTrack()` again, and replaced the overview with the stale zoomed view.

Clicking zoom-out again would restore the overview, but the next pending background query (from a prior click) would overwrite it again — creating an endless loop.

**Fix:** Added `AbortController` to `zoomToGenomeRegion`. Each new zoom call cancels the previous fetch. Zoom-out and chromosome selection also abort any pending fetch:

```javascript
async function zoomToGenomeRegion(chromosome, regionStart, regionEnd) {
  if (zoomAbortController) zoomAbortController.abort();
  zoomAbortController = new AbortController();
  const signal = zoomAbortController.signal;

  renderZoomedGenomeTrack(...);  // immediate

  const [orfRes, cpgRes] = await Promise.all([
    fetch(orfUrl, { signal }).then(...).catch(() => null),
    fetch(cpgUrl, { signal }).then(...).catch(() => null),
  ]);

  if (signal.aborted) return;  // zoom-out was clicked — discard results
  ...
}
```

---

### Bug 3 — Empty API response clearing the loaded visualization

**Symptom:** After the visualization loaded correctly, it would revert to the null/placeholder state. The summary card still showed the correct pattern count, but the analysis window track showed "Run or finish full analysis."

**Root cause:** `hydrateDashboard()` triggers a second `loadChromosomeDetails()` call after the initial page load. This second call fetches patterns and regions again. If the DynamoDB cache had expired and the Athena query ran cold or hit a partition miss, the regions endpoint returned `{items: []}` — an empty array. The check `Array.isArray(regions.items)` passes for `[]`, so `applyRegions([])` was called, setting `activeRegionItems = []` and triggering the placeholder state.

The visualization had already loaded correctly from the first call; the second call silently erased it.

**Fix:** Added a length guard so only non-empty results replace existing data:

```javascript
// Before (would clear with empty array)
if (regions && Array.isArray(regions.items)) applyRegions(regions.items);

// After (preserves existing data if API returns empty)
if (regions && Array.isArray(regions.items) && regions.items.length > 0) applyRegions(regions.items);
```

The same guard was applied to `applyPatterns`. A similar guard was added in `cache_put` in `web_api_handler.py` so that DynamoDB never caches an empty `{items:[]}` response — which would poison the cache for an hour.

---

### Related fix — CloudFront caching stale "pending" responses

**Symptom:** After a Batch full-analysis job completed successfully, the dashboard continued showing "Pending" for patterns and regions (even on refresh) for up to 1 hour.

**Root cause:** When a chromosome was first selected while analysis was still pending, CloudFront cached the `/api/chromosomes/{chr}/patterns` and `/api/chromosomes/{chr}/regions` responses with a 1-hour TTL. After the analysis completed and Athena partitions were synced, CloudFront was still serving the old empty responses.

**Fix:** Added automatic CloudFront cache invalidation to `trigger_partition_repair()` in `lambda_handler.py`. After every Batch job completes, it now:
1. Runs `MSCK REPAIR TABLE` for each affected Glue table (syncs Athena partitions)
2. Creates a CloudFront invalidation for `/api/chromosomes` and `/api/chromosomes/{chr}/*`

This ensures the dashboard sees fresh data within seconds of job completion.

---

## Athena Partition Sync

**Symptom:** After a Batch job completed successfully (patterns and regions data in S3), the dashboard showed "0 hits" and "0 window ORFs" in the summary card.

**Root cause:** AWS Glue does not automatically discover new S3 partitions when Parquet files are written. The Athena tables (`genome_sequences`, `sequence_patterns`, `sequence_regions`) had no knowledge of the new data until `MSCK REPAIR TABLE` was run.

**Fix:** `trigger_partition_repair()` in `lambda_handler.py` was added to fire `MSCK REPAIR TABLE` asynchronously at the end of every successful pipeline job. It was later extended to also invalidate CloudFront and DynamoDB cache entries.

---

## DynamoDB Cache Poisoning

**Symptom:** After deploying DynamoDB caching, chromosomes that had completed analysis continued showing empty data for an hour.

**Root cause:** The first API request after analysis completion (while `MSCK REPAIR TABLE` was still running) hit Athena before the partitions were synced, received `{items:[]}`, and wrote the empty response to DynamoDB with a 1-hour TTL. All subsequent requests served the empty cached response.

**Fix:** `cache_put()` was modified to skip writes when `items` is an empty list. Empty results from Athena indicate "not ready yet" rather than "genuinely empty," so they should always fall through to a fresh Athena query.

```python
def cache_put(key, data, ttl_seconds=3600):
    items = data.get("items")
    if isinstance(items, list) and len(items) == 0:
        return  # never cache empty results
    ...
```
