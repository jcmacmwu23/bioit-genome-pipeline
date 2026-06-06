const chromosomeStatus = [
  "1", "2", "3", "4", "5", "6", "7", "8",
  "9", "10", "11", "12", "13", "14", "15", "16",
  "17", "18", "19", "20", "21", "22", "X", "Y",
].map((chromosome) => ({
  chromosome,
  ready: chromosome === "22" || chromosome === "Y",
  patternsReady: chromosome === "22" || chromosome === "Y",
  regionsReady: chromosome === "22" || chromosome === "Y",
}));

const chromosomeSummaries = [
  {
    chromosome: "22",
    length: "50.8M bp",
    gc: "36.2%",
    patterns: "analysis ready",
    orfs: "candidate ORFs detected",
  },
];

const patternRows = [
  { name: "CpG-like motif", type: "motif", hits: 1482 },
  { name: "poly-A run", type: "repeat", hits: 764 },
  { name: "candidate ORF", type: "orf", hits: 221 },
  { name: "poly-G run", type: "repeat", hits: 205 },
];

const orfRows = [
  { window: "22:0-100000", gc: "35.8", orfs: 4 },
  { window: "22:100000-200000", gc: "37.3", orfs: 7 },
  { window: "22:200000-300000", gc: "41.6", orfs: 9 },
  { window: "22:300000-400000", gc: "44.1", orfs: 11 },
];

const gcValues = [32, 36, 41, 44, 39, 35, 37, 42];
const API_BASE_URL = window.BIOIT_API_BASE_URL || "";
const DEFAULT_CHROMOSOME = "22";
const HUMAN_CHROMOSOME_LENGTHS = {
  "1": 248956422,
  "2": 242193529,
  "3": 198295559,
  "4": 190214555,
  "5": 181538259,
  "6": 170805979,
  "7": 159345973,
  "8": 145138636,
  "9": 138394717,
  "10": 133797422,
  "11": 135086622,
  "12": 133275309,
  "13": 114364328,
  "14": 107043718,
  "15": 101991189,
  "16": 90338345,
  "17": 83257441,
  "18": 80373285,
  "19": 58617616,
  "20": 64444167,
  "21": 46709983,
  "22": 50818468,
  X: 156040895,
  Y: 57227415,
};
const CHROMOSOME_MAX_LENGTH = Math.max(...Object.values(HUMAN_CHROMOSOME_LENGTHS));
const VISUAL_BAND_TONES = ["#f4f1eb", "#d9d4cb", "#a49d92", "#5d5955", "#1f1e1c"];
const CENTROMERE_FILL = "#f46b61";
const SELECTED_ACCENT = "#7167c7";
const ATLAS_SELECTED_GLOW = "#e6ff00";
const ATHENA_SYNC_TYPICAL_SECONDS = 45;
const ATHENA_SYNC_WARNING_SECONDS = 120;

const singleJobForm = document.getElementById("singleJobForm");
const payloadPreview = document.getElementById("payloadPreview");
const submitBatchButton = document.getElementById("submitBatchButton");
const chromosomeGrid = document.getElementById("chromosomeGrid");
const summaryCards = document.getElementById("summaryCards");
const patternTable = document.getElementById("patternTable");
const orfTable = document.getElementById("orfTable");
const gcBars = document.getElementById("gcBars");
const regionChartNote = document.getElementById("regionChartNote");
const readyChromosomes = document.getElementById("readyChromosomes");
const queueDepth = document.getElementById("queueDepth");
const selectedChromosomeLabel = document.getElementById("selectedChromosomeLabel");
const selectedChromosomeStatus = document.getElementById("selectedChromosomeStatus");
const selectedSequenceStatus = document.getElementById("selectedSequenceStatus");
const selectedSequenceDetail = document.getElementById("selectedSequenceDetail");
const selectedPatternStatus = document.getElementById("selectedPatternStatus");
const selectedPatternDetail = document.getElementById("selectedPatternDetail");
const selectedRegionStatus = document.getElementById("selectedRegionStatus");
const selectedRegionDetail = document.getElementById("selectedRegionDetail");
const selectedFullAnalysisStatus = document.getElementById("selectedFullAnalysisStatus");
const selectedFullAnalysisDetail = document.getElementById("selectedFullAnalysisDetail");
const runFullAnalysisButton = document.getElementById("runFullAnalysisButton");
const runFullAnalysisHint = document.getElementById("runFullAnalysisHint");
const chromosomeAtlas = document.getElementById("chromosomeAtlas");
const selectedChromosomeVisual = document.getElementById("selectedChromosomeVisual");
const selectedChromosomeVisualNote = document.getElementById("selectedChromosomeVisualNote");

let activeChromosome = initialChromosomeFromUrl();
let chromosomeInventory = new Map(chromosomeStatus.map((item) => [item.chromosome, item]));
let activeSummary = null;
let batchStatusPollTimer = null;
let chromosomeDetailsLoading = false;
const athenaSyncObservedAt = new Map();
let activePatternItems = patternRows.map((row) => ({
  pattern_name: row.name,
  pattern_type: row.type,
  hit_count: row.hits,
}));
let lensFocusRange = null;
let lensDetailZoom = false;
let activeRegionItems = orfRows.map((row, index) => {
  const [, range = "0-0"] = row.window.split(":");
  const [start, end] = range.split("-").map((value) => Number(value));
  return {
    window_start: start,
    window_end: end,
    gc_content: Number(row.gc),
    orf_count: Number(row.orfs),
    motif_hits: Math.max(1, 5 - index),
    repeat_bases: 1800 + (index * 420),
  };
});

function initialChromosomeFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const candidate = params.get("chr");
  const upper = candidate ? candidate.toUpperCase() : null;
  return chromosomeStatus.some((item) => item.chromosome === upper) ? upper : DEFAULT_CHROMOSOME;
}

function chromosomeState(chromosome) {
  return chromosomeInventory.get(chromosome) || {
    chromosome,
    ready: false,
    patternsReady: false,
    regionsReady: false,
    fullAnalysisEligible: false,
    fullAnalysisStatus: "sequence_pending",
    fullAnalysisReason: "Sequence data must land before full analysis can run.",
  };
}

function chromosomeLengthValue(chromosome) {
  const item = chromosomeState(chromosome);
  const direct = Number(item.sequenceLength || 0);
  if (Number.isFinite(direct) && direct > 0) {
    return direct;
  }
  return HUMAN_CHROMOSOME_LENGTHS[chromosome] || 0;
}

function formatMb(basePairs) {
  if (!basePairs) {
    return "n/a";
  }
  return `${(basePairs / 1000000).toFixed(basePairs >= 100000000 ? 0 : 1)} Mb`;
}

function formatWindowRange(start, end) {
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return "window unavailable";
  }
  return `${(start / 1000000).toFixed(1)}-${(end / 1000000).toFixed(1)} Mb`;
}

function formatCompactCoord(bp) {
  const value = Number(bp || 0);
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)} Mb`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)} kb`;
  }
  return `${Math.round(value)} bp`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function bandSeed(chromosome) {
  return Array.from(chromosome).reduce((sum, character) => sum + character.charCodeAt(0), 0);
}

function centromereRatio(chromosome) {
  const ratios = {
    "1": 0.49, "2": 0.38, "3": 0.46, "4": 0.40, "5": 0.48, "6": 0.43,
    "7": 0.44, "8": 0.39, "9": 0.47, "10": 0.39, "11": 0.48, "12": 0.37,
    "13": 0.16, "14": 0.15, "15": 0.17, "16": 0.40, "17": 0.30, "18": 0.26,
    "19": 0.43, "20": 0.44, "21": 0.14, "22": 0.15, X: 0.46, Y: 0.53,
  };
  return ratios[chromosome] || 0.45;
}

function buildChromosomeBands(chromosome) {
  const seed = bandSeed(chromosome);
  const bands = [];
  const centromereStart = Math.max(0.1, centromereRatio(chromosome) - 0.02);
  const centromereEnd = Math.min(0.9, centromereRatio(chromosome) + 0.02);
  let cursor = 0;

  while (cursor < 1) {
    let span = 0.028 + (((seed + bands.length * 17) % 9) * 0.0075);
    const next = cursor + span;

    if (cursor < centromereStart && next > centromereStart) {
      span = centromereStart - cursor;
    } else if (cursor < centromereEnd && next > centromereEnd) {
      span = centromereEnd - cursor;
    }

    if (span <= 0.004) {
      span = 0.01;
    }

    const end = Math.min(1, cursor + span);
    const inCentromere = cursor >= centromereStart && end <= centromereEnd + 0.001;
    const toneIndex = (seed + bands.length * 5) % VISUAL_BAND_TONES.length;

    bands.push({
      start: cursor,
      end,
      fill: inCentromere ? CENTROMERE_FILL : VISUAL_BAND_TONES[toneIndex],
      stroke: inCentromere ? "#b63f37" : "rgba(24, 34, 45, 0.2)",
    });

    cursor = end;
    if (cursor >= centromereStart && cursor < centromereEnd) {
      bands.push({
        start: cursor,
        end: centromereEnd,
        fill: CENTROMERE_FILL,
        stroke: "#b63f37",
      });
      cursor = centromereEnd;
    }
  }

  if (bands[bands.length - 1].end < 1) {
    bands[bands.length - 1].end = 1;
  }

  return bands;
}

function gcColor(gcValue) {
  const gc = Number(gcValue || 0);
  if (gc >= 55) {
    return "#0b6e4f";
  }
  if (gc >= 45) {
    return "#4f8f70";
  }
  if (gc >= 38) {
    return "#b7c67d";
  }
  return "#d98752";
}

function buildLensRegionSample(items, maxCount = 24) {
  if (!items.length) {
    return [];
  }

  const informative = items.filter((item) => {
    const gc = Number(item.gc_content || 0);
    const orfs = Number(item.orf_count || 0);
    const motifs = Number(item.motif_hits || 0);
    return gc > 0 || orfs > 0 || motifs > 0;
  });

  const source = informative.length ? informative : items;
  if (source.length <= maxCount) {
    return source;
  }

  const sample = [];
  const seenStarts = new Set();
  const step = (source.length - 1) / (maxCount - 1);

  for (let index = 0; index < maxCount; index += 1) {
    const picked = source[Math.round(index * step)];
    const start = Number(picked.window_start || 0);
    if (seenStarts.has(start)) {
      continue;
    }
    seenStarts.add(start);
    sample.push(picked);
  }

  return sample;
}

function densityScale(value, maxValue, minSize = 10, maxSize = 72) {
  const safeMax = Math.max(1, Number(maxValue || 0));
  const safeValue = Math.max(0, Number(value || 0));
  return minSize + ((safeValue / safeMax) * (maxSize - minSize));
}

function lensAxisLabel(value, detailZoom = false) {
  if (detailZoom) {
    return formatCompactCoord(value);
  }
  return `${(Number(value || 0) / 1000000).toFixed(1)} Mb`;
}

function buildLensIdeogramBands(chromosome) {
  return buildChromosomeBands(chromosome).map((band) => {
    if (band.fill === CENTROMERE_FILL) {
      return band;
    }
    return {
      ...band,
      fill: "#d7d2c8",
      stroke: "rgba(24, 34, 45, 0.14)",
    };
  });
}

function buildFocusedLensRegionSample(items, maxCount = 24) {
  if (!items.length) {
    return [];
  }

  if (!lensFocusRange) {
    return buildLensRegionSample(items, maxCount);
  }

  const startBound = Number(lensFocusRange.start || 0);
  const endBound = Number(lensFocusRange.end || 0);
  const overlapping = items.filter((item) => {
    const start = Number(item.window_start || 0);
    const end = Number(item.window_end || 0);
    return end >= startBound && start <= endBound;
  });

  if (overlapping.length) {
    return buildLensRegionSample(overlapping, maxCount);
  }

  const center = (startBound + endBound) / 2;
  const nearest = items
    .slice()
    .sort((a, b) => {
      const aCenter = (Number(a.window_start || 0) + Number(a.window_end || 0)) / 2;
      const bCenter = (Number(b.window_start || 0) + Number(b.window_end || 0)) / 2;
      return Math.abs(aCenter - center) - Math.abs(bCenter - center);
    })
    .slice(0, maxCount);

  return buildLensRegionSample(nearest, maxCount);
}

function setSelectedChromosome(chromosome) {
  activeChromosome = chromosome;
  lensFocusRange = null;
  lensDetailZoom = false;
  selectedChromosomeLabel.textContent = chromosome;
  singleJobForm.elements.chromosome.value = chromosome;
  const url = new URL(window.location.href);
  url.searchParams.set("chr", chromosome);
  window.history.replaceState({}, "", url);
  renderChromosomeGrid();
  renderChromosomeAtlas();
  renderSelectedChromosomeVisual();
}

function showLoadingChromosome(chromosome) {
  chromosomeDetailsLoading = true;
  selectedChromosomeLabel.textContent = chromosome;
  selectedChromosomeStatus.textContent = `Loading live analysis for chromosome ${chromosome}...`;
  selectedSequenceStatus.textContent = "Loading";
  selectedPatternStatus.textContent = "Loading";
  selectedRegionStatus.textContent = "Loading";
  selectedFullAnalysisStatus.textContent = "Loading";
  selectedSequenceDetail.textContent = "Fetching sequence summary from the dashboard API...";
  selectedPatternDetail.textContent = "Checking pattern analysis status...";
  selectedRegionDetail.textContent = "Checking region analysis status...";
  selectedFullAnalysisDetail.textContent = "Determining the active analysis path...";
  runFullAnalysisHint.textContent = `Loading chromosome ${chromosome} status...`;
  chromosomeSummaries.length = 0;
  chromosomeSummaries.push({
    chromosome,
    length: "Loading...",
    gc: "Loading...",
    patterns: "Loading from dashboard API...",
    orfs: "Loading from dashboard API...",
  });
  patternRows.length = 0;
  orfRows.length = 0;
  gcValues.length = 0;
  activePatternItems = [];
  activeRegionItems = [];
  renderSummaryCards();
  renderPatternTable();
  renderOrfTable();
  renderGcBars();
  renderChromosomeGrid();
  renderChromosomeAtlas();
  renderSelectedChromosomeVisual();
}

function selectedChromosomeMessage(item) {
  if (isAthenaSyncPending(item)) {
    return "Sequence landed. Batch finished, and Athena is still loading pattern and region summaries.";
  }
  if (hasTrackedProcessing(item)) {
    return formatTrackedProgressDetail(item, "Full analysis") || "Full analysis is active.";
  }
  if (item && item.batchStatus && item.batchStatus.status === "FAILED") {
    return "Full analysis failed on AWS Batch. Open the job controls or retry after checking the Batch logs.";
  }
  if (hasBatchProgress(item)) {
    return formatBatchProgressDetail(item, "Full analysis") || "Full analysis is active on AWS Batch.";
  }
  if (item.ready && item.patternsReady && item.regionsReady) {
    return "Live sequence, pattern, and region analysis loaded";
  }
  if (item.ready && item.fullAnalysisStatus === "batch_required") {
    return "Sequence landed. This chromosome will use AWS Batch on Fargate for full analysis.";
  }
  if (item.ready && item.fullAnalysisEligible === false && item.fullAnalysisStatus === "batch_unavailable") {
    return "Sequence landed, but AWS Batch is not available for full analysis right now.";
  }
  if (item.ready) {
    return "Sequence landed, but some analysis datasets are still pending";
  }
  return "No completed analysis yet for this chromosome";
}

function renderChromosomeGrid() {
  chromosomeGrid.innerHTML = "";

  chromosomeStatus.forEach((item) => {
    const live = chromosomeState(item.chromosome);
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = `chromosome-pill ${live.ready ? "ready" : "pending"} ${item.chromosome === activeChromosome ? "active" : ""}`;
    pill.textContent = item.chromosome;
    pill.setAttribute("aria-pressed", item.chromosome === activeChromosome ? "true" : "false");
    pill.addEventListener("click", () => handleChromosomeSelection(item.chromosome));
    chromosomeGrid.appendChild(pill);
  });

  const readyCount = Array.from(chromosomeInventory.values()).filter((item) => item.ready).length;
  readyChromosomes.textContent = `${readyCount} / ${chromosomeStatus.length}`;
  selectedChromosomeStatus.textContent = selectedChromosomeMessage(chromosomeState(activeChromosome));
  updateSelectionMeta(chromosomeState(activeChromosome));
}

function statusLabel(isReady) {
  return isReady ? "Ready" : "Pending";
}

function hasBatchProgress(item) {
  if (!item || !item.batchStatus) {
    return false;
  }
  return ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING", "SUCCEEDED", "FAILED"].includes(item.batchStatus.status);
}

function currentTrackedProcessing(item) {
  if (!item || !item.processingStatus) {
    return null;
  }
  return item.processingStatus;
}

function hasTrackedProcessing(item) {
  const status = currentTrackedProcessing(item);
  return Boolean(status && ["submitted", "running", "failed"].includes(String(status.status || "").toLowerCase()));
}

function hasResolvedAnalysisMetrics(item) {
  return Number((item && item.patternHitCount) || 0) > 0 || Number((item && item.orfCount) || 0) > 0;
}

function isBatchSizedChromosome(item) {
  if (!item) {
    return false;
  }
  const length = Number(item.sequenceLength || 0);
  const maxBases = Number(item.fullAnalysisMaxBases || 0);
  return length > 0 && maxBases > 0 && length > maxBases;
}

function trackAthenaSyncState(item) {
  if (!item || !item.chromosome) {
    return;
  }
  if (isAthenaSyncPending(item)) {
    if (!athenaSyncObservedAt.has(item.chromosome)) {
      athenaSyncObservedAt.set(item.chromosome, Date.now());
    }
    return;
  }
  athenaSyncObservedAt.delete(item.chromosome);
}

function isAthenaSyncPending(item) {
  if (!item) {
    return false;
  }
  return item.ready
    && item.patternsReady
    && item.regionsReady
    && (item.fullAnalysisBackend === "batch" || isBatchSizedChromosome(item))
    && !hasResolvedAnalysisMetrics(item);
}

function formatAthenaSyncDetail(item, analysisKind) {
  if (!isAthenaSyncPending(item)) {
    return null;
  }

  trackAthenaSyncState(item);
  const observedAt = athenaSyncObservedAt.get(item.chromosome) || Date.now();
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - observedAt) / 1000));
  const elapsedLabel = elapsedSeconds >= 60
    ? `${Math.max(1, Math.round(elapsedSeconds / 60))} min elapsed`
    : `${elapsedSeconds}s elapsed`;

  if (elapsedSeconds < ATHENA_SYNC_TYPICAL_SECONDS) {
    return `${analysisKind} is loading from Athena (${elapsedLabel} · typical remaining: under 1 min).`;
  }
  if (elapsedSeconds < ATHENA_SYNC_WARNING_SECONDS) {
    return `${analysisKind} is still loading from Athena (${elapsedLabel} · typical remaining: about 1 min).`;
  }
  return `${analysisKind} is taking longer than usual in Athena (${elapsedLabel}). If this keeps going, it is likely a partition refresh issue rather than compute time.`;
}

function athenaSyncSeverity(item) {
  if (!isAthenaSyncPending(item)) {
    return null;
  }
  trackAthenaSyncState(item);
  const observedAt = athenaSyncObservedAt.get(item.chromosome) || Date.now();
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - observedAt) / 1000));
  if (elapsedSeconds < ATHENA_SYNC_TYPICAL_SECONDS) {
    return "normal";
  }
  if (elapsedSeconds < ATHENA_SYNC_WARNING_SECONDS) {
    return "slow";
  }
  return "stalled";
}

function isBatchJobActive(item) {
  if (!item || !item.batchStatus) {
    return false;
  }
  return ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"].includes(item.batchStatus.status);
}

function isTrackedProcessingActive(item) {
  const status = currentTrackedProcessing(item);
  return Boolean(status && ["submitted", "running"].includes(String(status.status || "").toLowerCase()));
}

function formatTrackedProgressDetail(item, analysisKind) {
  const status = currentTrackedProcessing(item);
  if (!status) {
    return null;
  }

  const backendLabel = String(status.backend || "lambda").toLowerCase() === "batch"
    ? "AWS Batch"
    : "Lambda";
  const phase = String(status.status || "").toLowerCase();
  const progress = Number(status.progress_pct || 0);
  const elapsed = Number(status.elapsed_minutes || 0);
  const expected = Number(status.expected_minutes || 0);
  const remaining = expected > elapsed ? Math.max(1, Math.round(expected - elapsed)) : null;
  const progressLabel = progress > 0 ? `~${progress}%` : null;
  const elapsedLabel = elapsed > 0 ? `${Math.max(1, Math.round(elapsed))} min elapsed` : null;
  const etaLabel = remaining ? `ETA ~${remaining} min` : null;
  const detailBits = [progressLabel, elapsedLabel, etaLabel].filter(Boolean).join(" · ");

  if (phase === "running") {
    return `${analysisKind} is running on ${backendLabel}${detailBits ? ` (${detailBits})` : ""}.`;
  }
  if (phase === "submitted") {
    return `${analysisKind} was submitted to ${backendLabel}${detailBits ? ` (${detailBits})` : ""}.`;
  }
  if (phase === "failed") {
    return `${analysisKind} failed on ${backendLabel}${status.failure_reason ? ` (${status.failure_reason})` : ""}.`;
  }
  if (phase === "succeeded") {
    return `${analysisKind} finished on ${backendLabel}.`;
  }
  return null;
}

function formatBatchProgressDetail(item, analysisKind) {
  if (!hasBatchProgress(item)) {
    return null;
  }

  const status = item.batchStatus.status;
  const progress = Number(item.batchStatus.progress_pct || 0);
  const elapsed = Number(item.batchStatus.elapsed_minutes || 0);
  const expected = Number(item.batchStatus.expected_minutes || 0);
  const remaining = expected > elapsed ? Math.max(1, Math.round(expected - elapsed)) : null;
  const progressLabel = progress > 0 ? `~${progress}%` : null;
  const elapsedLabel = elapsed > 0 ? `${Math.max(1, Math.round(elapsed))} min elapsed` : null;
  const etaLabel = remaining ? `ETA ~${remaining} min` : null;
  const detailBits = [progressLabel, elapsedLabel, etaLabel].filter(Boolean).join(" · ");

  if (status === "RUNNING") {
    return `${analysisKind} is running on AWS Batch${detailBits ? ` (${detailBits})` : ""}.`;
  }
  if (status === "STARTING") {
    return `${analysisKind} container is starting on AWS Batch${detailBits ? ` (${detailBits})` : ""}.`;
  }
  if (status === "RUNNABLE") {
    return `${analysisKind} is queued on AWS Batch and waiting for Fargate capacity.`;
  }
  if (status === "SUBMITTED" || status === "PENDING") {
    return `${analysisKind} job was submitted to AWS Batch${detailBits ? ` (${detailBits})` : ""}.`;
  }
  if (status === "SUCCEEDED") {
    return `${analysisKind} finished on Batch and Athena is catching up.`;
  }
  if (status === "FAILED") {
    const reason = item.batchStatus.status_reason || "Batch container exited unexpectedly.";
    return `${analysisKind} failed on AWS Batch (${reason}).`;
  }
  return null;
}

function formatBatchStatusLabel(item, ready) {
  if (ready || !item || !item.batchStatus) {
    return statusLabel(ready);
  }
  if (item.batchStatus.status === "FAILED") {
    return "Failed";
  }
  const progress = Number(item.batchStatus.progress_pct || 0);
  if (progress > 0 && isBatchJobActive(item)) {
    return `${progress}%`;
  }
  return statusLabel(false);
}

function formatTrackedStatusLabel(item, ready) {
  const status = currentTrackedProcessing(item);
  if (ready || !status) {
    return statusLabel(ready);
  }
  if (String(status.status || "").toLowerCase() === "failed") {
    return "Failed";
  }
  const progress = Number(status.progress_pct || 0);
  if (progress > 0 && isTrackedProcessingActive(item)) {
    return `${progress}%`;
  }
  return statusLabel(false);
}

function latestOutputLabel(item) {
  if (!item.latestOutputAt) {
    return "No output landed yet";
  }
  return `Latest output: ${item.latestOutputAt}`;
}

function setSelectionCardState(node, state) {
  const card = node ? node.closest(".selection-card") : null;
  if (!card) {
    return;
  }
  card.classList.remove("selection-card-sync-normal", "selection-card-sync-slow", "selection-card-sync-stalled");
  if (state) {
    card.classList.add(`selection-card-sync-${state}`);
  }
}

function updateSelectionMeta(item) {
  trackAthenaSyncState(item);
  const syncSeverity = athenaSyncSeverity(item);
  const trackedProcessingDetail = formatTrackedProgressDetail(item, "Pattern analysis");
  const trackedRegionDetail = formatTrackedProgressDetail(item, "Region analysis");
  selectedSequenceStatus.textContent = statusLabel(item.ready);
  selectedPatternStatus.textContent = isAthenaSyncPending(item)
    ? "Syncing"
    : hasTrackedProcessing(item)
      ? formatTrackedStatusLabel(item, item.patternsReady)
    : formatBatchStatusLabel(item, item.patternsReady);
  selectedRegionStatus.textContent = isAthenaSyncPending(item)
    ? "Syncing"
    : hasTrackedProcessing(item)
      ? formatTrackedStatusLabel(item, item.regionsReady)
    : formatBatchStatusLabel(item, item.regionsReady);

  selectedSequenceDetail.textContent = item.ready
    ? latestOutputLabel(item)
    : "Sequence parquet has not landed in S3 yet";
  selectedPatternDetail.textContent = item.patternsReady
    ? isAthenaSyncPending(item)
      ? formatAthenaSyncDetail(item, "Pattern analysis")
      : "Pattern leaderboard is queryable in Athena"
    : trackedProcessingDetail || formatBatchProgressDetail(item, "Pattern analysis") || "Pattern analysis has not completed yet";
  selectedRegionDetail.textContent = item.regionsReady
    ? isAthenaSyncPending(item)
      ? formatAthenaSyncDetail(item, "Region analysis")
      : "Region windows and GC bars are available"
    : trackedRegionDetail || formatBatchProgressDetail(item, "Region analysis") || "Region summaries are not available yet";

  const fullyAnalyzed = item.patternsReady && item.regionsReady;
  const fullAnalysisEligible = item.fullAnalysisEligible !== false;
  selectedFullAnalysisStatus.textContent = isAthenaSyncPending(item)
    ? "Athena"
    : fullyAnalyzed
    ? "Complete"
    : hasTrackedProcessing(item)
      ? formatTrackedStatusLabel(item, false)
    : item.fullAnalysisStatus === "batch_required"
      ? "Batch"
    : fullAnalysisEligible
      ? "Eligible"
      : "Blocked";
  selectedFullAnalysisDetail.textContent = isAthenaSyncPending(item)
    ? "Batch finished; Athena is still refreshing summary outputs for this chromosome."
    : hasTrackedProcessing(item)
      ? formatTrackedProgressDetail(item, "Full analysis") || item.fullAnalysisReason || "Full analysis is active."
    : item.fullAnalysisReason || "Full-analysis routing has not been evaluated yet.";

  setSelectionCardState(selectedSequenceStatus, null);
  setSelectionCardState(selectedPatternStatus, syncSeverity);
  setSelectionCardState(selectedRegionStatus, syncSeverity);
  setSelectionCardState(selectedFullAnalysisStatus, syncSeverity);

  runFullAnalysisButton.disabled = !item.ready || fullyAnalyzed || !fullAnalysisEligible;
  runFullAnalysisButton.textContent = item.fullAnalysisStatus === "batch_required"
    ? "Run Full Analysis on Batch"
    : "Run Full Analysis";
  runFullAnalysisHint.textContent = fullyAnalyzed
    ? isAthenaSyncPending(item)
      ? `Chromosome ${item.chromosome} finished on Batch and is still loading from Athena. Typical sync is under 1 minute.`
      : `Chromosome ${item.chromosome} already has full analysis outputs.`
    : hasTrackedProcessing(item)
      ? formatTrackedProgressDetail(item, "Full analysis") || `Chromosome ${item.chromosome} is currently processing.`
    : !item.ready
      ? `Chromosome ${item.chromosome} needs sequence data before full analysis can run.`
      : !fullAnalysisEligible
        ? item.fullAnalysisReason
        : `Chromosome ${item.chromosome} can be promoted from sequence-only to full analysis.`;
}

function renderChromosomeAtlas() {
  const width = 660;
  const height = 320;
  const barWidth = 18;
  const topMargin = 32;
  const bottomMargin = 42;
  const usableHeight = 232;
  const gap = 8;
  const startX = 14;
  const svgParts = [
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Human chromosome atlas ideogram">`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="24" fill="rgba(255,255,255,0.56)" />`,
    `<text x="20" y="24" fill="#5b6672" font-size="13" font-family="IBM Plex Sans">All chromosomes scaled by reference length</text>`,
  ];

  chromosomeStatus.forEach((item, index) => {
    const chromosome = item.chromosome;
    const length = chromosomeLengthValue(chromosome);
    const ratio = length / CHROMOSOME_MAX_LENGTH;
    const barHeight = Math.max(70, usableHeight * ratio);
    const x = startX + index * (barWidth + gap);
    const y = topMargin + (usableHeight - barHeight);
    const radius = barWidth / 2;
    const clipId = `atlas-clip-${chromosome}`;
    const bands = buildChromosomeBands(chromosome);
    const live = chromosomeState(chromosome);
    const outline = chromosome === activeChromosome ? ATLAS_SELECTED_GLOW : "rgba(24, 34, 45, 0.45)";
    const outlineWidth = chromosome === activeChromosome ? 3.2 : 1.1;
    const readyFill = live.ready ? "rgba(11, 110, 79, 0.14)" : "rgba(191, 95, 47, 0.12)";
    const isSelected = chromosome === activeChromosome;
    const groupOpacity = isSelected ? 1 : 0.42;

    svgParts.push(`<g opacity="${groupOpacity}">`);
    svgParts.push(`<defs><clipPath id="${clipId}"><rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="${radius}" ry="${radius}" /></clipPath></defs>`);
    if (isSelected) {
      svgParts.push(`<rect x="${x - 12}" y="${y - 16}" width="${barWidth + 24}" height="${barHeight + 32}" rx="${radius + 12}" fill="rgba(230,255,0,0.42)" stroke="rgba(230,255,0,0.96)" stroke-width="2.2" />`);
      svgParts.push(`<rect x="${x - 16}" y="${y - 20}" width="${barWidth + 32}" height="${barHeight + 40}" rx="${radius + 16}" fill="rgba(230,255,0,0.16)" stroke="none" />`);
    } else {
      svgParts.push(`<rect x="${x - 3}" y="${y - 6}" width="${barWidth + 6}" height="${barHeight + 12}" rx="${radius + 4}" fill="${readyFill}" />`);
    }
    svgParts.push(`<rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="${radius}" ry="${radius}" fill="#f8f4ee" stroke="${outline}" stroke-width="${outlineWidth}" />`);

    bands.forEach((band) => {
      const bandY = y + (band.start * barHeight);
      const bandHeight = Math.max(2, (band.end - band.start) * barHeight);
      svgParts.push(
        `<rect x="${x}" y="${bandY}" width="${barWidth}" height="${bandHeight}" fill="${band.fill}" stroke="${band.stroke}" stroke-width="0.45" clip-path="url(#${clipId})" />`,
      );
    });

    if (live.patternsReady && live.regionsReady) {
      svgParts.push(`<circle cx="${x + barWidth / 2}" cy="${y + barHeight + 12}" r="${isSelected ? 5.2 : 3.8}" fill="${isSelected ? "#b7ff00" : "#0b6e4f"}" />`);
    } else if (live.ready) {
      svgParts.push(`<circle cx="${x + barWidth / 2}" cy="${y + barHeight + 12}" r="${isSelected ? 5.2 : 3.8}" fill="${isSelected ? "#b7ff00" : "#bf5f2f"}" />`);
    }

    svgParts.push(
      `<text x="${x + barWidth / 2}" y="${height - 14}" text-anchor="middle" fill="${isSelected ? "#b7ff00" : "#5b6672"}" font-size="${isSelected ? 13.5 : 12}" font-family="Space Grotesk" font-weight="700">${chromosome}</text>`,
    );
    svgParts.push(`</g>`);
  });

  svgParts.push("</svg>");
  chromosomeAtlas.innerHTML = svgParts.join("");
}

function renderSelectedChromosomeVisual() {
  if (chromosomeDetailsLoading && !activeSummary) {
    const width = 760;
    const height = 320;
    const svgParts = [
      `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Loading chromosome detail">`,
      `<rect x="0" y="0" width="${width}" height="${height}" rx="26" fill="rgba(255,255,255,0.5)" />`,
      `<text x="44" y="56" fill="#18222d" font-size="28" font-family="Space Grotesk" font-weight="700">Chromosome ${activeChromosome}</text>`,
      `<text x="44" y="86" fill="#5b6672" font-size="16" font-family="IBM Plex Sans">Loading live sequence, pattern, and region detail from the BioIT API...</text>`,
      `<rect x="44" y="120" width="672" height="30" rx="15" fill="#f8f4ee" stroke="rgba(24,34,45,0.18)" stroke-width="1.2" />`,
      `<rect x="44" y="120" width="198" height="30" rx="15" fill="rgba(113,103,199,0.18)" />`,
      `<rect x="44" y="188" width="672" height="96" rx="18" fill="rgba(255,255,255,0.72)" stroke="rgba(24,34,45,0.16)" stroke-width="1.2" />`,
      `<text x="44" y="178" fill="#bf5f2f" font-size="12" font-family="IBM Plex Sans" font-weight="700">CANDIDATE ORF TRACK</text>`,
      `<text x="68" y="244" fill="#5b6672" font-size="15" font-family="IBM Plex Sans">Waiting for chromosome detail to load before rendering the lens.</text>`,
      `</svg>`,
    ];
    selectedChromosomeVisual.innerHTML = svgParts.join("");
    selectedChromosomeVisualNote.textContent = `Chromosome ${activeChromosome} is loading. If this stays here for more than a few seconds, the dashboard API likely returned an error.`;
    return;
  }

  const item = chromosomeState(activeChromosome);
  const length = chromosomeLengthValue(activeChromosome);
  const patterns = activePatternItems.slice(0, 3);
  const regions = buildFocusedLensRegionSample(activeRegionItems, 24);
  const width = 760;
  const controlsActive = Boolean(lensFocusRange);
  const height = controlsActive ? 658 : 626;
  const ideogramX = 44;
  const ideogramY = controlsActive ? 100 : 64;
  const ideogramWidth = 672;
  const ideogramHeight = 30;
  const clipId = `focus-clip-${activeChromosome}`;
  const bands = buildLensIdeogramBands(activeChromosome);
  const maxWindowEnd = regions.length
    ? Math.max(...regions.map((region) => Number(region.window_end || 0)))
    : 0;
  const minWindowStart = regions.length
    ? Math.min(...regions.map((region) => Number(region.window_start || 0)))
    : 0;
  const trackX = 44;
  const trackY = controlsActive ? 232 : 196;
  const trackWidth = 672;
  const trackHeight = 104;
  const motifTrackY = controlsActive ? 424 : 388;
  const motifTrackHeight = 84;
  const maxOrfCount = regions.length ? Math.max(...regions.map((region) => Number(region.orf_count || 0))) : 0;
  const maxMotifHits = regions.length ? Math.max(...regions.map((region) => Number(region.motif_hits || 0))) : 0;
  const focusStart = lensFocusRange ? Number(lensFocusRange.start || 0) : 0;
  const focusEnd = lensFocusRange ? Number(lensFocusRange.end || 0) : 0;
  const focusOrfTotal = regions.reduce((sum, region) => sum + Number(region.orf_count || 0), 0);
  const focusMotifTotal = regions.reduce((sum, region) => sum + Number(region.motif_hits || 0), 0);
  const focusAvgGc = regions.length
    ? (regions.reduce((sum, region) => sum + Number(region.gc_content || 0), 0) / regions.length)
    : 0;
  const trackStart = lensDetailZoom && lensFocusRange ? focusStart : 0;
  const trackEnd = lensDetailZoom && lensFocusRange ? focusEnd : Math.max(length, 1);
  const trackSpan = Math.max(1, trackEnd - trackStart);
  const lensTitle = lensDetailZoom && lensFocusRange
    ? `Zoomed chr${activeChromosome}: ${formatCompactCoord(focusStart)} - ${formatCompactCoord(focusEnd)}`
    : lensFocusRange
      ? `Chr${activeChromosome}: ${formatCompactCoord(focusStart)} - ${formatCompactCoord(focusEnd)}`
    : `Chromosome ${activeChromosome}`;
  const lensSubtitle = lensFocusRange
    ? `${regions.length} sampled windows · ${focusOrfTotal.toLocaleString()} ORFs · ${focusMotifTotal.toLocaleString()} motif hits · ${focusAvgGc.toFixed(1)}% avg GC${lensDetailZoom ? " · local detail view" : ""}`
    : `${formatMb(length)} reference span · ${item.patternsReady ? "patterns ready" : "patterns pending"} · ${item.regionsReady ? "regions ready" : "regions pending"}`;
  const orfLegendY = trackY - 30;
  const orfAxisBaselineY = trackY + trackHeight + 18;
  const motifAxisBottomY = motifTrackY + motifTrackHeight - 10;
  const motifAxisTopY = motifTrackY + 10;
  const badgeY = motifAxisBottomY + 48;
  const badgeTextY = badgeY + 18;
  const trackTickCount = lensDetailZoom ? 6 : 5;
  const trackTickValues = Array.from({ length: trackTickCount }, (_, index) => trackStart + ((trackSpan * index) / (trackTickCount - 1)));
  const orfLegendItems = [
    { color: "#0b6e4f", label: "GC-rich (>=55%)" },
    { color: "#4f8f70", label: "GC-high (45-54%)" },
    { color: "#b7c67d", label: "Balanced GC (38-44%)" },
    { color: "#d98752", label: "AT-rich (<38%)" },
  ];
  const svgParts = [
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Selected chromosome ideogram and analysis track">`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="26" fill="rgba(255,255,255,0.5)" />`,
    `<text x="${ideogramX}" y="34" fill="#18222d" font-size="24" font-family="Space Grotesk" font-weight="700">${lensTitle}</text>`,
    `<text x="${ideogramX}" y="52" fill="#5b6672" font-size="13" font-family="IBM Plex Sans">${lensSubtitle}</text>`,
    `<defs><clipPath id="${clipId}"><rect x="${ideogramX}" y="${ideogramY}" width="${ideogramWidth}" height="${ideogramHeight}" rx="15" ry="15" /></clipPath></defs>`,
    `<rect x="${ideogramX}" y="${ideogramY}" width="${ideogramWidth}" height="${ideogramHeight}" rx="15" ry="15" fill="#f8f4ee" stroke="rgba(24,34,45,0.4)" stroke-width="1.3" />`,
  ];

  if (controlsActive) {
    const zoomFill = lensDetailZoom ? "rgba(113,103,199,0.26)" : "rgba(113,103,199,0.14)";
    svgParts.push(`<rect x="${ideogramX + ideogramWidth - 206}" y="64" width="102" height="24" rx="12" fill="${zoomFill}" stroke="${SELECTED_ACCENT}" stroke-width="1" data-zoom-toggle="true" style="cursor:pointer" />`);
    svgParts.push(`<text x="${ideogramX + ideogramWidth - 155}" y="80" text-anchor="middle" fill="${SELECTED_ACCENT}" font-size="11" font-family="IBM Plex Sans" font-weight="700" pointer-events="none">ZOOMED VIEW</text>`);
    svgParts.push(`<rect x="${ideogramX + ideogramWidth - 94}" y="64" width="86" height="24" rx="12" fill="rgba(113,103,199,0.14)" stroke="${SELECTED_ACCENT}" stroke-width="1" data-reset-focus="true" style="cursor:pointer" />`);
    svgParts.push(`<text x="${ideogramX + ideogramWidth - 51}" y="80" text-anchor="middle" fill="${SELECTED_ACCENT}" font-size="12" font-family="IBM Plex Sans" font-weight="700" pointer-events="none">Reset lens</text>`);
  }

  bands.forEach((band) => {
    const bandX = ideogramX + (band.start * ideogramWidth);
    const bandWidth = Math.max(3, (band.end - band.start) * ideogramWidth);
    svgParts.push(
      `<rect x="${bandX}" y="${ideogramY}" width="${bandWidth}" height="${ideogramHeight}" fill="${band.fill}" stroke="${band.stroke}" stroke-width="0.45" clip-path="url(#${clipId})" />`,
    );
  });

  if (regions.length && length > 0 && maxWindowEnd > minWindowStart) {
    const highlightStart = ideogramX + (minWindowStart / length) * ideogramWidth;
    const highlightWidth = Math.max(18, ((maxWindowEnd - minWindowStart) / length) * ideogramWidth);
    svgParts.push(`<rect x="${highlightStart}" y="${ideogramY - 6}" width="${highlightWidth}" height="${ideogramHeight + 12}" rx="12" fill="rgba(113,103,199,0.16)" stroke="${SELECTED_ACCENT}" stroke-width="1.4" />`);
    svgParts.push(`<path d="M ${highlightStart + 8} ${ideogramY + ideogramHeight + 8} C ${highlightStart + 18} 136, ${trackX + 24} 142, ${trackX + 24} ${trackY}" fill="none" stroke="rgba(24,34,45,0.35)" stroke-width="1.4" />`);
    svgParts.push(`<path d="M ${highlightStart + highlightWidth - 8} ${ideogramY + ideogramHeight + 8} C ${highlightStart + highlightWidth - 18} 136, ${trackX + trackWidth - 24} 142, ${trackX + trackWidth - 24} ${trackY}" fill="none" stroke="rgba(24,34,45,0.35)" stroke-width="1.4" />`);
  }

  svgParts.push(`<rect x="${ideogramX}" y="${ideogramY}" width="${ideogramWidth}" height="${ideogramHeight}" rx="15" fill="transparent" data-ideogram-click="true" style="cursor:pointer" />`);

  svgParts.push(`<text x="${ideogramX}" y="${ideogramY + 56}" fill="#5b6672" font-size="12" font-family="IBM Plex Sans">0 Mb</text>`);
  svgParts.push(`<text x="${ideogramX + ideogramWidth}" y="${ideogramY + 56}" text-anchor="end" fill="#5b6672" font-size="12" font-family="IBM Plex Sans">${formatMb(length)}</text>`);
  svgParts.push(`<rect x="${trackX}" y="${trackY}" width="${trackWidth}" height="${trackHeight}" rx="18" fill="rgba(255,255,255,0.72)" stroke="rgba(24,34,45,0.16)" stroke-width="1.2" />`);
  svgParts.push(`<text x="${trackX}" y="${trackY - 14}" fill="#bf5f2f" font-size="12" font-family="IBM Plex Sans" font-weight="700">CANDIDATE ORF TRACK</text>`);
  orfLegendItems.forEach((legendItem, index) => {
    const legendX = trackX + 172 + (index * 128);
    svgParts.push(`<rect x="${legendX}" y="${orfLegendY}" width="12" height="12" rx="3" fill="${legendItem.color}" opacity="0.9" />`);
    svgParts.push(`<text x="${legendX + 18}" y="${orfLegendY + 10}" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${legendItem.label}</text>`);
  });
  svgParts.push(`<line x1="${trackX}" y1="${orfAxisBaselineY}" x2="${trackX + trackWidth}" y2="${orfAxisBaselineY}" stroke="rgba(24,34,45,0.26)" stroke-width="1" />`);

  if (!regions.length) {
    svgParts.push(`<text x="${trackX + 24}" y="${trackY + 58}" fill="#5b6672" font-size="14" font-family="IBM Plex Sans">Run or finish full analysis to populate region windows for this chromosome.</text>`);
  } else {
    regions.forEach((region, index) => {
      const start = Number(region.window_start || 0);
      const end = Number(region.window_end || 0);
      const gc = Number(region.gc_content || 0);
      const motifHits = Number(region.motif_hits || 0);
      const orfCount = Number(region.orf_count || 0);
      const repeatBases = Number(region.repeat_bases || 0);
      const regionX = trackX + (((start - trackStart) / trackSpan) * trackWidth);
      const regionWidth = Math.max(lensDetailZoom ? 10 : 14, ((end - start) / trackSpan) * trackWidth);
      const columnHeight = densityScale(orfCount, maxOrfCount, 18, 74);
      const regionY = trackY + trackHeight - columnHeight - 14;
      const labelY = trackY + trackHeight - 4;

      svgParts.push(`<rect x="${regionX}" y="${regionY}" width="${regionWidth}" height="${columnHeight}" rx="8" fill="${gcColor(gc)}" opacity="0.88" data-focus-start="${start}" data-focus-end="${end}" style="cursor:pointer" />`);

      if (motifHits > 0) {
        svgParts.push(`<circle cx="${regionX + regionWidth / 2}" cy="${regionY - 8}" r="${Math.min(6, 2 + motifHits / 2)}" fill="#bf5f2f" opacity="0.78" />`);
      }

      if (orfCount > 0) {
        const flagX = regionX + regionWidth - 4;
        const flagY = regionY - 16;
        svgParts.push(`<path d="M ${flagX} ${flagY} l 10 4 l -10 4 z" fill="${SELECTED_ACCENT}" />`);
        svgParts.push(`<line x1="${flagX}" y1="${flagY - 2}" x2="${flagX}" y2="${regionY}" stroke="${SELECTED_ACCENT}" stroke-width="1.2" />`);
      }

      if (repeatBases > 0) {
        const repeatWidth = Math.min(regionWidth, Math.max(5, repeatBases / 900));
        svgParts.push(`<rect x="${regionX}" y="${trackY + 10}" width="${repeatWidth}" height="8" rx="4" fill="rgba(24,34,45,0.18)" />`);
      }
    });

    trackTickValues.forEach((tickValue) => {
      const tickX = trackX + (((tickValue - trackStart) / trackSpan) * trackWidth);
      svgParts.push(`<line x1="${tickX}" y1="${orfAxisBaselineY}" x2="${tickX}" y2="${orfAxisBaselineY - 7}" stroke="rgba(24,34,45,0.3)" stroke-width="1" />`);
      svgParts.push(`<text x="${tickX}" y="${orfAxisBaselineY + 14}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${lensAxisLabel(tickValue, lensDetailZoom)}</text>`);
    });
    svgParts.push(`<text x="${trackX + trackWidth / 2}" y="${orfAxisBaselineY + 30}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">Genomic position</text>`);

    svgParts.push(`<rect x="${trackX}" y="${motifTrackY}" width="${trackWidth}" height="${motifTrackHeight}" rx="16" fill="rgba(219,245,246,0.58)" stroke="rgba(10,136,142,0.22)" stroke-width="1.2" />`);
    svgParts.push(`<text x="${trackX}" y="${motifTrackY - 10}" fill="#0a888e" font-size="12" font-family="IBM Plex Sans" font-weight="700">CpG MOTIFS (DENSITY)</text>`);
    svgParts.push(`<line x1="${trackX + 20}" y1="${motifAxisTopY}" x2="${trackX + 20}" y2="${motifAxisBottomY}" stroke="rgba(10,136,142,0.32)" stroke-width="1" />`);
    svgParts.push(`<line x1="${trackX + 20}" y1="${motifAxisBottomY}" x2="${trackX + trackWidth - 12}" y2="${motifAxisBottomY}" stroke="rgba(10,136,142,0.32)" stroke-width="1" />`);
    svgParts.push(`<text x="${trackX + 10}" y="${motifAxisTopY + 4}" text-anchor="end" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${maxMotifHits.toLocaleString()}</text>`);
    svgParts.push(`<text x="${trackX + 10}" y="${motifAxisBottomY + 4}" text-anchor="end" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">0</text>`);
    svgParts.push(`<text x="${trackX - 22}" y="${motifTrackY + motifTrackHeight / 2}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans" transform="rotate(-90 ${trackX - 22} ${motifTrackY + motifTrackHeight / 2})">Motif hits / window</text>`);

    regions.forEach((region) => {
      const start = Number(region.window_start || 0);
      const end = Number(region.window_end || 0);
      const motifHits = Number(region.motif_hits || 0);
      const regionX = trackX + 20 + (((start - trackStart) / trackSpan) * (trackWidth - 32));
      const regionWidth = Math.max(10, ((end - start) / trackSpan) * trackWidth);
      const barHeight = densityScale(motifHits, maxMotifHits, 8, motifTrackHeight - 24);
      const barY = motifAxisBottomY - barHeight;
      svgParts.push(`<rect x="${regionX}" y="${barY}" width="${Math.max(4, regionWidth * 0.45)}" height="${barHeight}" rx="3" fill="#16a3a8" opacity="0.9" />`);
    });
    trackTickValues.forEach((tickValue) => {
      const tickX = trackX + 20 + (((tickValue - trackStart) / trackSpan) * (trackWidth - 32));
      svgParts.push(`<line x1="${tickX}" y1="${motifAxisBottomY}" x2="${tickX}" y2="${motifAxisBottomY + 5}" stroke="rgba(10,136,142,0.28)" stroke-width="1" />`);
      svgParts.push(`<text x="${tickX}" y="${motifAxisBottomY + 18}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${lensAxisLabel(tickValue, lensDetailZoom)}</text>`);
    });
    svgParts.push(`<text x="${trackX + trackWidth / 2}" y="${motifAxisBottomY + 34}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">Genomic position</text>`);

    const topPatternBadges = patterns.map((pattern, index) => {
      const x = trackX + (index * 182);
      const label = `${pattern.pattern_name} · ${pattern.hit_count}`;
      return [
        `<rect x="${x}" y="${badgeY}" width="170" height="28" rx="14" fill="rgba(255,255,255,0.84)" stroke="rgba(24,34,45,0.12)" />`,
        `<text x="${x + 12}" y="${badgeTextY}" fill="#18222d" font-size="12" font-family="IBM Plex Sans">${escapeHtml(label)}</text>`,
      ].join("");
    });
    svgParts.push(...topPatternBadges);
  }

  svgParts.push("</svg>");
  selectedChromosomeVisual.innerHTML = svgParts.join("");

  const ideogramClick = selectedChromosomeVisual.querySelector("[data-ideogram-click='true']");
  if (ideogramClick) {
    ideogramClick.addEventListener("click", (event) => {
      const rect = ideogramClick.getBoundingClientRect();
      const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
      const center = Math.max(0, Math.min(1, ratio)) * Math.max(length, 1);
      const span = Math.max(12000000, Math.round(Math.max(length, 1) * 0.18));
      lensFocusRange = {
        start: Math.max(0, center - (span / 2)),
        end: Math.min(Math.max(length, 1), center + (span / 2)),
      };
      lensDetailZoom = false;
      renderSelectedChromosomeVisual();
    });
  }

  selectedChromosomeVisual.querySelectorAll("[data-focus-start]").forEach((node) => {
    node.addEventListener("click", () => {
      const start = Number(node.getAttribute("data-focus-start") || 0);
      const end = Number(node.getAttribute("data-focus-end") || 0);
      const padding = Math.max(1500000, (end - start) * 2);
      lensFocusRange = {
        start: Math.max(0, start - padding),
        end: Math.min(Math.max(length, 1), end + padding),
      };
      lensDetailZoom = false;
      renderSelectedChromosomeVisual();
    });
  });

  const resetFocus = selectedChromosomeVisual.querySelector("[data-reset-focus='true']");
  if (resetFocus) {
    resetFocus.addEventListener("click", () => {
      lensFocusRange = null;
      lensDetailZoom = false;
      renderSelectedChromosomeVisual();
    });
  }

  const zoomToggle = selectedChromosomeVisual.querySelector("[data-zoom-toggle='true']");
  if (zoomToggle) {
    zoomToggle.addEventListener("click", () => {
      lensDetailZoom = !lensDetailZoom;
      renderSelectedChromosomeVisual();
    });
  }

  if (!regions.length) {
    selectedChromosomeVisualNote.textContent = `Chromosome ${activeChromosome} does not have region-level outputs yet, so the lower lens is waiting on full analysis.`;
    return;
  }

  const firstRegion = regions[0];
  const lastRegion = regions[regions.length - 1];
  selectedChromosomeVisualNote.textContent = lensDetailZoom
    ? `Zoomed view covers ${formatWindowRange(Number(firstRegion.window_start || 0), Number(lastRegion.window_end || 0))}. Click ZOOMED VIEW to toggle back, or Reset lens to return to the full chromosome.`
    : `The lower lens covers ${formatWindowRange(Number(firstRegion.window_start || 0), Number(lastRegion.window_end || 0))} with GC-colored ORF bars, motif markers, and CpG density for chromosome ${activeChromosome}. Click the ideogram or ORF bars to refocus the lens, then click ZOOMED VIEW for a local-range detail view.`;
}

function applyChromosomeInventory(items) {
  chromosomeInventory = new Map(
    items.map((item) => [
      item.chromosome,
      {
        chromosome: item.chromosome,
        ready: Boolean(item.sequence_ready),
        patternsReady: Boolean(item.patterns_ready),
        regionsReady: Boolean(item.regions_ready),
        latestOutputAt: item.latest_output_at,
        latestKey: item.latest_key,
        sequenceLength: item.sequence_length,
        avgGcContent: item.avg_gc_content,
        patternHitCount: item.pattern_hit_count,
        orfCount: item.orf_count,
        fullAnalysisEligible: item.full_analysis_eligible,
        fullAnalysisStatus: item.full_analysis_status,
        fullAnalysisReason: item.full_analysis_reason,
        fullAnalysisMaxBases: item.full_analysis_max_bases,
        fullAnalysisBackend: item.full_analysis_backend,
      },
    ]),
  );

  chromosomeStatus.forEach((item) => {
    const live = chromosomeState(item.chromosome);
    item.ready = live.ready;
    item.patternsReady = live.patternsReady;
    item.regionsReady = live.regionsReady;
  });

  renderChromosomeGrid();
  renderChromosomeAtlas();
}

function renderSummaryCards() {
  summaryCards.innerHTML = "";

  chromosomeSummaries.forEach((item) => {
    const live = chromosomeState(item.chromosome);
    const trackedProcessing = hasTrackedProcessing(live) && !live.patternsReady && !live.regionsReady;
    const batchActive = hasBatchProgress(live) && !live.patternsReady && !live.regionsReady;
    const athenaPending = isAthenaSyncPending(live);
    const patternAthenaDetail = athenaPending
      ? formatAthenaSyncDetail(live, "Pattern analysis")
      : null;
    const regionAthenaDetail = athenaPending
      ? formatAthenaSyncDetail(live, "Region analysis")
      : null;
    const patternBatchDetail = batchActive
      ? formatBatchProgressDetail(live, "Pattern analysis")
      : null;
    const regionBatchDetail = batchActive
      ? formatBatchProgressDetail(live, "Region analysis")
      : null;
    const patternTrackedDetail = trackedProcessing
      ? formatTrackedProgressDetail(live, "Pattern analysis")
      : null;
    const regionTrackedDetail = trackedProcessing
      ? formatTrackedProgressDetail(live, "Region analysis")
      : null;
    const card = document.createElement("article");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="metric-label">Chromosome ${item.chromosome}</span>
      <strong>${item.length}</strong>
      <p>GC content: ${item.gc}</p>
      <p>Patterns: ${athenaPending ? "Loading from Athena..." : trackedProcessing ? "Loading from operations store..." : batchActive ? "Loading from AWS Batch..." : item.patterns}</p>
      ${athenaPending ? `<p class="summary-subdetail">${escapeHtml(patternAthenaDetail || "")}</p>` : ""}
      ${!athenaPending && patternTrackedDetail ? `<p class="summary-subdetail">${escapeHtml(patternTrackedDetail)}</p>` : ""}
      ${!athenaPending && patternBatchDetail ? `<p class="summary-subdetail">${escapeHtml(patternBatchDetail)}</p>` : ""}
      <p>ORF status: ${athenaPending ? "Loading from Athena..." : trackedProcessing ? "Loading from operations store..." : batchActive ? "Loading from AWS Batch..." : item.orfs}</p>
      ${athenaPending ? `<p class="summary-subdetail">${escapeHtml(regionAthenaDetail || "")}</p>` : ""}
      ${!athenaPending && regionTrackedDetail ? `<p class="summary-subdetail">${escapeHtml(regionTrackedDetail)}</p>` : ""}
      ${!athenaPending && regionBatchDetail ? `<p class="summary-subdetail">${escapeHtml(regionBatchDetail)}</p>` : ""}
    `;
    summaryCards.appendChild(card);
  });
}

function syncTableScrollIndicators() {
  document.querySelectorAll(".table-scroll-frame").forEach((frame) => {
    frame.querySelectorAll(".table-scroll-indicator").forEach((indicator) => indicator.remove());
    delete frame.dataset.scrollIndicatorBound;
  });
}

function renderPatternTable() {
  patternTable.innerHTML = "";

  if (!patternRows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="table-empty" colspan="3">No pattern analysis available for chromosome ${activeChromosome} yet.</td>`;
    patternTable.appendChild(tr);
    return;
  }

  patternRows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.name}</td>
      <td>${row.type}</td>
      <td>${row.hits}</td>
    `;
    patternTable.appendChild(tr);
  });

  syncTableScrollIndicators();
}

function renderOrfTable() {
  orfTable.innerHTML = "";

  if (!orfRows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="table-empty" colspan="3">No region windows available for chromosome ${activeChromosome} yet.</td>`;
    orfTable.appendChild(tr);
    return;
  }

  orfRows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.window}</td>
      <td>${row.gc}</td>
      <td>${row.orfs}</td>
    `;
    orfTable.appendChild(tr);
  });

  syncTableScrollIndicators();
}

function renderGcBars() {
  gcBars.innerHTML = "";

  if (!activeRegionItems.length) {
    const note = document.createElement("p");
    note.className = "table-empty";
    note.textContent = `No region windows available for chromosome ${activeChromosome} yet.`;
    gcBars.appendChild(note);
    if (regionChartNote) {
      regionChartNote.textContent = "GC% and ORF-count chart will appear after region-level outputs load.";
    }
    return;
  }

  const regions = buildLensRegionSample(activeRegionItems, 18);
  const width = 720;
  const height = 250;
  const margin = { top: 18, right: 56, bottom: 54, left: 56 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxGc = Math.max(50, ...regions.map((item) => Number(item.gc_content || 0)));
  const gcAxisMax = Math.ceil((maxGc + 5) / 5) * 5;
  const maxOrf = Math.max(1, ...regions.map((item) => Number(item.orf_count || 0)));
  const xStep = regions.length > 1 ? plotWidth / (regions.length - 1) : plotWidth;
  const tickCount = 4;
  const svgParts = [
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Region chart showing GC percentage and ORF counts by genomic window">`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="14" fill="rgba(255,255,255,0.42)" />`,
    `<text x="${margin.left}" y="14" fill="#18222d" font-size="12" font-family="IBM Plex Sans" font-weight="700">GC% line + ORF-count bars</text>`,
  ];

  for (let tick = 0; tick <= tickCount; tick += 1) {
    const ratio = tick / tickCount;
    const y = margin.top + plotHeight - (ratio * plotHeight);
    const gcLabel = Math.round(ratio * gcAxisMax);
    const orfLabel = Math.round(ratio * maxOrf);
    svgParts.push(`<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" stroke="rgba(24,34,45,0.08)" stroke-width="1" />`);
    svgParts.push(`<text x="${margin.left - 10}" y="${y + 4}" text-anchor="end" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${gcLabel}</text>`);
    svgParts.push(`<text x="${width - margin.right + 10}" y="${y + 4}" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${orfLabel}</text>`);
  }

  const linePoints = [];
  regions.forEach((region, index) => {
    const start = Number(region.window_start || 0);
    const end = Number(region.window_end || 0);
    const gc = Number(region.gc_content || 0);
    const orf = Number(region.orf_count || 0);
    const x = margin.left + (index * xStep);
    const barHeight = (orf / maxOrf) * plotHeight;
    const barY = margin.top + plotHeight - barHeight;
    const gcY = margin.top + plotHeight - ((gc / gcAxisMax) * plotHeight);
    const barWidth = Math.max(10, Math.min(22, xStep * 0.48 || 18));
    linePoints.push(`${x},${gcY}`);
    svgParts.push(
      `<rect x="${x - (barWidth / 2)}" y="${barY}" width="${barWidth}" height="${Math.max(2, barHeight)}" rx="5" fill="rgba(191,95,47,0.52)"><title>${activeChromosome}: ${formatCompactCoord(start)}-${formatCompactCoord(end)} | ORFs ${orf.toLocaleString()} | GC ${gc.toFixed(2)}%</title></rect>`,
    );
  });

  svgParts.push(`<polyline fill="none" stroke="#0b6e4f" stroke-width="3" points="${linePoints.join(" ")}" />`);

  regions.forEach((region, index) => {
    const start = Number(region.window_start || 0);
    const end = Number(region.window_end || 0);
    const gc = Number(region.gc_content || 0);
    const x = margin.left + (index * xStep);
    const gcY = margin.top + plotHeight - ((gc / gcAxisMax) * plotHeight);
    svgParts.push(
      `<circle cx="${x}" cy="${gcY}" r="4" fill="#0b6e4f" stroke="#f8fff9" stroke-width="1.5"><title>${activeChromosome}: ${formatCompactCoord(start)}-${formatCompactCoord(end)} | GC ${gc.toFixed(2)}%</title></circle>`,
    );
  });

  const labelIndexes = new Set([0, Math.round((regions.length - 1) * 0.33), Math.round((regions.length - 1) * 0.66), Math.max(0, regions.length - 1)]);
  regions.forEach((region, index) => {
    if (!labelIndexes.has(index)) {
      return;
    }
    const start = Number(region.window_start || 0);
    const x = margin.left + (index * xStep);
    svgParts.push(`<text x="${x}" y="${height - 18}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${formatCompactCoord(start)}</text>`);
  });

  svgParts.push(`<line x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${width - margin.right}" y2="${margin.top + plotHeight}" stroke="rgba(24,34,45,0.22)" stroke-width="1.2" />`);
  svgParts.push(`<text x="${width / 2}" y="${height - 4}" text-anchor="middle" fill="#5b6672" font-size="11" font-family="IBM Plex Sans">Genomic window start</text>`);
  svgParts.push(`<text x="16" y="${margin.top + (plotHeight / 2)}" text-anchor="middle" fill="#0b6e4f" font-size="11" font-family="IBM Plex Sans" transform="rotate(-90 16 ${margin.top + (plotHeight / 2)})">GC %</text>`);
  svgParts.push(`<text x="${width - 10}" y="${margin.top + (plotHeight / 2)}" text-anchor="middle" fill="#bf5f2f" font-size="11" font-family="IBM Plex Sans" transform="rotate(90 ${width - 10} ${margin.top + (plotHeight / 2)})">ORF count</text>`);
  svgParts.push(`<rect x="${width - 180}" y="10" width="10" height="10" rx="3" fill="rgba(191,95,47,0.52)" />`);
  svgParts.push(`<text x="${width - 165}" y="19" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">ORF count</text>`);
  svgParts.push(`<line x1="${width - 96}" y1="15" x2="${width - 78}" y2="15" stroke="#0b6e4f" stroke-width="3" />`);
  svgParts.push(`<circle cx="${width - 87}" cy="15" r="3.5" fill="#0b6e4f" stroke="#f8fff9" stroke-width="1.2" />`);
  svgParts.push(`<text x="${width - 72}" y="19" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">GC %</text>`);
  svgParts.push(`</svg>`);

  gcBars.innerHTML = svgParts.join("");
  if (regionChartNote) {
    regionChartNote.textContent = `Sampled ${regions.length} informative region windows for chromosome ${activeChromosome}; ORF count is shown as bars and GC% as a line.`;
  }
}

function applySummary(summary) {
  const live = chromosomeState(summary.chromosome);
  const patternSummary = summary.patterns_ready
    ? (Number(summary.pattern_hit_count || 0) > 0 ? `${summary.pattern_hit_count} hits` : "0 hits")
    : "No completed pattern dataset yet";
  const regionSummary = summary.regions_ready
    ? (Number(summary.orf_count || 0) > 0 ? `${summary.orf_count} window ORFs` : "0 window ORFs")
    : "No completed region dataset yet";
  chromosomeDetailsLoading = false;
  activeSummary = summary;
  chromosomeInventory.set(summary.chromosome, {
    ...live,
    ready: typeof summary.sequence_ready === "boolean" ? summary.sequence_ready : live.ready,
    patternsReady: typeof summary.patterns_ready === "boolean" ? summary.patterns_ready : live.patternsReady,
    regionsReady: typeof summary.regions_ready === "boolean" ? summary.regions_ready : live.regionsReady,
    latestOutputAt: summary.latest_output_at || live.latestOutputAt,
    sequenceLength: summary.sequence_length,
    avgGcContent: summary.avg_gc_content,
    patternHitCount: summary.pattern_hit_count,
    orfCount: summary.orf_count,
    fullAnalysisEligible: summary.full_analysis_eligible,
    fullAnalysisStatus: summary.full_analysis_status,
    fullAnalysisReason: summary.full_analysis_reason,
    fullAnalysisMaxBases: summary.full_analysis_max_bases,
    processingStatus: summary.processing_status || live.processingStatus,
    batchStatus: summary.batch_status || live.batchStatus,
  });
  chromosomeSummaries.length = 0;
  chromosomeSummaries.push({
    chromosome: summary.chromosome,
    length: summary.sequence_length ? `${Number(summary.sequence_length).toLocaleString()} bp` : "n/a",
    gc: summary.avg_gc_content ? `${summary.avg_gc_content}%` : "n/a",
    patterns: patternSummary,
    orfs: regionSummary,
  });
  renderSummaryCards();
  renderSelectedChromosomeVisual();
}

function applyPatterns(items) {
  chromosomeDetailsLoading = false;
  activePatternItems = items.slice();
  patternRows.length = 0;
  items.forEach((item) => {
    patternRows.push({
      name: item.pattern_name,
      type: item.pattern_type,
      hits: item.hit_count,
    });
  });
  renderPatternTable();
  renderSelectedChromosomeVisual();
}

function applyRegions(items) {
  chromosomeDetailsLoading = false;
  activeRegionItems = items.slice();
  orfRows.length = 0;
  gcValues.length = 0;

  buildLensRegionSample(items, 24).forEach((item) => {
    orfRows.push({
      window: `${activeChromosome}:${item.window_start}-${item.window_end}`,
      gc: item.gc_content,
      orfs: item.orf_count,
    });
    gcValues.push(Number(item.gc_content || 0));
  });

  renderOrfTable();
  renderGcBars();
  renderSelectedChromosomeVisual();
}

function applyBatchStatus(chromosome, batchStatus) {
  const live = chromosomeState(chromosome);
  chromosomeInventory.set(chromosome, {
    ...live,
    batchStatus: batchStatus || null,
  });
}

function applyOperations(chromosome, operations) {
  if (!operations || (!operations.item && !operations.processing_status)) {
    return;
  }

  const live = chromosomeState(chromosome);
  const current = operations.item || {};
  const sequenceReady = typeof current.sequence_ready === "boolean"
    ? current.sequence_ready
    : live.ready;
  const patternsReady = typeof current.patterns_ready === "boolean"
    ? current.patterns_ready
    : live.patternsReady;
  const regionsReady = typeof current.regions_ready === "boolean"
    ? current.regions_ready
    : live.regionsReady;
  const latestOutputAt = current.updated_at || current.finished_at || current.submitted_at || live.latestOutputAt;

  chromosomeInventory.set(chromosome, {
    ...live,
    ready: sequenceReady,
    patternsReady,
    regionsReady,
    latestOutputAt,
    processingStatus: operations.processing_status || live.processingStatus || null,
  });
}

function showUnavailableChromosome(chromosome) {
  chromosomeDetailsLoading = false;
  activeSummary = null;
  activePatternItems = [];
  activeRegionItems = [];
  chromosomeSummaries.length = 0;
  chromosomeSummaries.push({
    chromosome,
    length: "Not loaded",
    gc: "n/a",
    patterns: "No completed pattern dataset yet",
    orfs: "No completed region dataset yet",
  });
  patternRows.length = 0;
  orfRows.length = 0;
  gcValues.length = 0;
  renderSummaryCards();
  renderPatternTable();
  renderOrfTable();
  renderGcBars();
  renderChromosomeGrid();
  renderChromosomeAtlas();
  renderSelectedChromosomeVisual();
}

function stopBatchStatusPolling() {
  if (batchStatusPollTimer) {
    window.clearTimeout(batchStatusPollTimer);
    batchStatusPollTimer = null;
  }
}

function scheduleBatchStatusPolling(chromosome) {
  stopBatchStatusPolling();
  const item = chromosomeState(chromosome);
  if (
    chromosome !== activeChromosome
    || (!hasTrackedProcessing(item) && !isBatchJobActive(item) && !isAthenaSyncPending(item))
  ) {
    return;
  }
  batchStatusPollTimer = window.setTimeout(() => {
    if (chromosome === activeChromosome) {
      loadChromosomeDetails(chromosome, { preserveFocus: true });
    }
  }, 30000);
}

function buildSinglePayload(form) {
  return {
    source: form.source.value,
    accession_id: form.accessionId.value,
    chromosome: form.chromosome.value,
    species: form.species.value,
    output_prefix: form.outputPrefix.value,
  };
}

function buildBatchPayload() {
  return {
    source: "ncbi",
    species: "homo_sapiens",
    chromosomes: [
      "1", "2", "3", "4", "5", "6", "7", "8",
      "9", "10", "11", "12", "13", "14", "15", "16",
      "17", "18", "19", "20", "21", "22", "X", "Y",
    ],
    notes: "Website should submit this through a backend endpoint that validates accession mapping server-side.",
  };
}

function renderPreview(payload) {
  payloadPreview.textContent = JSON.stringify(payload, null, 2);
}

async function fetchJson(path) {
  if (!API_BASE_URL) {
    return null;
  }

  const separator = path.includes("?") ? "&" : "?";
  const cacheBustedPath = `${path}${separator}_ts=${Date.now()}`;
  const response = await fetch(`${API_BASE_URL}${cacheBustedPath}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

async function postJson(path, payload = {}) {
  if (!API_BASE_URL) {
    return null;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.message || `Request failed: ${response.status}`);
  }
  return data;
}

async function loadChromosomeDetails(chromosome, options = {}) {
  if (!options.preserveFocus) {
    setSelectedChromosome(chromosome);
  }
  stopBatchStatusPolling();
  showLoadingChromosome(chromosome);

  if (!API_BASE_URL) {
    if (chromosome !== DEFAULT_CHROMOSOME) {
      showUnavailableChromosome(chromosome);
    }
    return;
  }

  try {
    const [summary, batchStatus, operations] = await Promise.all([
      fetchJson(`/api/chromosomes/${chromosome}/summary`),
      fetchJson(`/api/chromosomes/${chromosome}/batch-status`).catch(() => null),
      fetchJson(`/api/chromosomes/${chromosome}/operations`).catch(() => null),
    ]);

    applyOperations(chromosome, operations);
    applyBatchStatus(chromosome, batchStatus);

    if (summary) {
      applySummary(summary);
    }

    const liveAfterStatus = chromosomeState(chromosome);
    if (!summary || !liveAfterStatus.ready) {
      showUnavailableChromosome(chromosome);
      return;
    }

    const [patterns, regions] = await Promise.all([
      fetchJson(`/api/chromosomes/${chromosome}/patterns`).catch(() => null),
      fetchJson(`/api/chromosomes/${chromosome}/regions?limit=5000`).catch(() => null),
    ]);

    if (patterns && Array.isArray(patterns.items)) {
      applyPatterns(patterns.items);
    } else {
      activePatternItems = [];
      patternRows.length = 0;
      renderPatternTable();
    }

    if (regions && Array.isArray(regions.items)) {
      applyRegions(regions.items);
    } else {
      activeRegionItems = [];
      orfRows.length = 0;
      gcValues.length = 0;
      renderOrfTable();
      renderGcBars();
      renderSelectedChromosomeVisual();
    }

    renderChromosomeGrid();
    scheduleBatchStatusPolling(chromosome);
  } catch (error) {
    console.warn(`Unable to load chromosome ${chromosome} data.`, error);
    showUnavailableChromosome(chromosome);
  }
}

async function hydrateDashboard() {
  if (!API_BASE_URL) {
    return;
  }

  const detailPromise = loadChromosomeDetails(activeChromosome, { preserveFocus: true });

  try {
    const [overview, chromosomes] = await Promise.all([
      fetchJson("/api/status/overview"),
      fetchJson("/api/chromosomes"),
    ]);

    if (overview && overview.queue) {
      queueDepth.textContent = `${overview.queue.depth} pending`;
    }

    if (chromosomes && Array.isArray(chromosomes.items)) {
      applyChromosomeInventory(chromosomes.items);
    }
  } catch (error) {
    console.warn("Dashboard API unavailable, using local placeholder data.", error);
  }
  await detailPromise;
}

function handleChromosomeSelection(chromosome) {
  loadChromosomeDetails(chromosome);
}

async function handleRunFullAnalysis() {
  const item = chromosomeState(activeChromosome);
  if (!item.ready || (item.patternsReady && item.regionsReady) || item.fullAnalysisEligible === false) {
    return;
  }

  runFullAnalysisButton.disabled = true;
  runFullAnalysisHint.textContent = `Submitting full analysis for chromosome ${activeChromosome}...`;

  try {
    const response = await postJson(`/api/chromosomes/${activeChromosome}/analyze`, {
      species: "homo_sapiens",
    });
    const backend = response && response.analysis_backend === "batch"
      ? "AWS Batch on Fargate"
      : "the Lambda queue";
    runFullAnalysisHint.textContent = `Full analysis for chromosome ${activeChromosome} was submitted to ${backend}.`;
  } catch (error) {
    console.warn(`Unable to submit full analysis for chromosome ${activeChromosome}.`, error);
    runFullAnalysisHint.textContent = error.message;
  } finally {
    await hydrateDashboard();
  }
}

window.addEventListener("popstate", () => {
  const nextChromosome = initialChromosomeFromUrl();
  if (nextChromosome !== activeChromosome) {
    loadChromosomeDetails(nextChromosome);
  }
});

singleJobForm.addEventListener("submit", (event) => {
  event.preventDefault();
  renderPreview(buildSinglePayload(event.currentTarget.elements));
});

submitBatchButton.addEventListener("click", () => {
  renderPreview(buildBatchPayload());
});

runFullAnalysisButton.addEventListener("click", () => {
  handleRunFullAnalysis();
});

setSelectedChromosome(activeChromosome);
showLoadingChromosome(activeChromosome);
renderPreview(buildSinglePayload(singleJobForm.elements));
hydrateDashboard();
syncTableScrollIndicators();
