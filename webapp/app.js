const chromosomeStatus = [
  "1", "2", "3", "4", "5", "6", "7", "8",
  "9", "10", "11", "12", "13", "14", "15", "16",
  "17", "18", "19", "20", "21", "22", "X", "Y",
].map((chromosome) => ({
  chromosome,
  ready: true,
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

const singleJobForm = document.getElementById("singleJobForm");
const payloadPreview = document.getElementById("payloadPreview");
const submitBatchButton = document.getElementById("submitBatchButton");
const chromosomeGrid = document.getElementById("chromosomeGrid");
const summaryCards = document.getElementById("summaryCards");
const patternTable = document.getElementById("patternTable");
const orfTable = document.getElementById("orfTable");
const gcBars = document.getElementById("gcBars");
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
let activeOrfItems = [];
let activeCpgItems = [];
let zoomAbortController = null;
let isZoomedIn = false; // true while zoomed view is showing — blocks background re-renders
let activePatternItems = patternRows.map((row) => ({
  pattern_name: row.name,
  pattern_type: row.type,
  hit_count: row.hits,
}));
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

function formatCoord(bp) {
  const n = Number(bp);
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} Mb`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} kb`;
  return `${n} bp`;
}

function orfDensityColor(intensity) {
  // background beige (#f8f4ee) → dashboard accent purple (#7167c7)
  const r = Math.round(248 + intensity * (113 - 248));
  const g = Math.round(244 + intensity * (103 - 244));
  const b = Math.round(238 + intensity * (199 - 238));
  return `rgb(${r},${g},${b})`;
}

function buildOrfDensityBands(regions, chromosomeLength) {
  if (!regions.length || !chromosomeLength) return null;
  const counts = regions.map((r) => Number(r.orf_count || 0));
  const maxOrf = Math.max(1, ...counts);
  const minOrf = Math.min(...counts);
  const orfRange = Math.max(1, maxOrf - minOrf);
  const centStart = Math.max(0.1, centromereRatio(activeChromosome) - 0.02);
  const centEnd = Math.min(0.9, centromereRatio(activeChromosome) + 0.02);
  return regions.map((r) => {
    const start = Number(r.window_start) / chromosomeLength;
    const end = Math.min(1, Number(r.window_end) / chromosomeLength);
    const count = Number(r.orf_count || 0);
    const inCentromere = start >= centStart && end <= centEnd + 0.001;
    const intensity = (count - minOrf) / orfRange;
    return {
      start,
      end,
      fill: inCentromere ? CENTROMERE_FILL : orfDensityColor(intensity),
      stroke: inCentromere ? "#b63f37" : "rgba(113,103,199,0.22)",
      orfCount: count,
      motifHits: Number(r.motif_hits || 0),
      gcContent: Number(r.gc_content || 0),
      windowStart: Number(r.window_start),
      windowEnd: Number(r.window_end),
    };
  });
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

function setSelectedChromosome(chromosome) {
  activeChromosome = chromosome;
  selectedChromosomeLabel.textContent = chromosome;
  singleJobForm.elements.chromosome.value = chromosome;
  const url = new URL(window.location.href);
  url.searchParams.set("chr", chromosome);
  window.history.replaceState({}, "", url);
  renderChromosomeGrid();
  renderChromosomeAtlas();
  renderSelectedChromosomeVisual();
}

function selectedChromosomeMessage(item) {
  if (item.ready && item.patternsReady && item.regionsReady) {
    return "Live sequence, pattern, and region analysis loaded";
  }
  if (item.ready && item.fullAnalysisStatus === "batch_required") {
    return "Sequence landed. This chromosome will use AWS Batch on Fargate for full analysis.";
  }
  if (item.ready && item.fullAnalysisEligible === false && item.fullAnalysisStatus === "too_large") {
    return "Sequence landed. This chromosome is too large for the current Lambda full-analysis runtime.";
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

function formatBatchProgress(bs) {
  if (!bs || bs.status === "no_job" || bs.status === "not_configured") return null;
  const { status, elapsed_minutes, progress_pct, expected_minutes } = bs;
  if (status === "SUCCEEDED") return "Batch job completed — refresh to load results.";
  if (status === "FAILED") return "Batch job failed. Check CloudWatch logs.";
  if (status === "RUNNING") {
    const elapsed = elapsed_minutes ? `${elapsed_minutes} min` : "";
    const pct = progress_pct != null ? ` · ~${progress_pct}%` : "";
    const eta = progress_pct != null && expected_minutes && elapsed_minutes
      ? ` · ETA ~${Math.max(0, Math.round(expected_minutes - elapsed_minutes))} min left`
      : "";
    return `Running on Batch${elapsed ? ` (${elapsed}${pct}${eta})` : ""}`;
  }
  if (status === "STARTING") return "Batch container starting…";
  if (status === "RUNNABLE") return "Batch job queued — waiting for Fargate capacity…";
  if (status === "SUBMITTED" || status === "PENDING") return "Batch job submitted…";
  return `Batch: ${status}`;
}

function latestOutputLabel(item) {
  if (!item.latestOutputAt) {
    return "No output landed yet";
  }
  return `Latest output: ${item.latestOutputAt}`;
}

function updateSelectionMeta(item) {
  selectedSequenceStatus.textContent = statusLabel(item.ready);
  selectedPatternStatus.textContent = statusLabel(item.patternsReady);
  selectedRegionStatus.textContent = statusLabel(item.regionsReady);

  selectedSequenceDetail.textContent = item.ready
    ? latestOutputLabel(item)
    : "Sequence parquet has not landed in S3 yet";
  const batchMsg = item.batchStatus ? formatBatchProgress(item.batchStatus) : null;
  selectedPatternDetail.textContent = item.patternsReady
    ? "Pattern leaderboard is queryable in Athena"
    : batchMsg || "Pattern analysis has not completed yet";
  selectedRegionDetail.textContent = item.regionsReady
    ? "Region windows and GC bars are available"
    : batchMsg || "Region summaries are not available yet";

  const fullyAnalyzed = item.patternsReady && item.regionsReady;
  const fullAnalysisEligible = item.fullAnalysisEligible !== false;
  selectedFullAnalysisStatus.textContent = fullyAnalyzed
    ? "Complete"
    : item.fullAnalysisStatus === "batch_required"
      ? "Batch"
    : fullAnalysisEligible
      ? "Eligible"
      : "Blocked";
  selectedFullAnalysisDetail.textContent = item.fullAnalysisReason
    || "Lambda eligibility has not been evaluated yet.";

  runFullAnalysisButton.disabled = !item.ready || fullyAnalyzed || !fullAnalysisEligible;
  runFullAnalysisButton.textContent = item.fullAnalysisStatus === "batch_required"
    ? "Run Full Analysis on Batch"
    : "Run Full Analysis";
  runFullAnalysisHint.textContent = fullyAnalyzed
    ? `Chromosome ${item.chromosome} already has full analysis outputs.`
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
    const outline = chromosome === activeChromosome ? SELECTED_ACCENT : "rgba(24, 34, 45, 0.45)";
    const outlineWidth = chromosome === activeChromosome ? 2.5 : 1.1;
    const readyFill = live.ready ? "rgba(11, 110, 79, 0.14)" : "rgba(191, 95, 47, 0.12)";

    svgParts.push(`<defs><clipPath id="${clipId}"><rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="${radius}" ry="${radius}" /></clipPath></defs>`);
    svgParts.push(`<rect x="${x - 3}" y="${y - 6}" width="${barWidth + 6}" height="${barHeight + 12}" rx="${radius + 4}" fill="${readyFill}" />`);
    svgParts.push(`<rect x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="${radius}" ry="${radius}" fill="#f8f4ee" stroke="${outline}" stroke-width="${outlineWidth}" />`);

    bands.forEach((band) => {
      const bandY = y + (band.start * barHeight);
      const bandHeight = Math.max(2, (band.end - band.start) * barHeight);
      svgParts.push(
        `<rect x="${x}" y="${bandY}" width="${barWidth}" height="${bandHeight}" fill="${band.fill}" stroke="${band.stroke}" stroke-width="0.45" clip-path="url(#${clipId})" />`,
      );
    });

    if (live.patternsReady && live.regionsReady) {
      svgParts.push(`<circle cx="${x + barWidth / 2}" cy="${y + barHeight + 12}" r="3.8" fill="#0b6e4f" />`);
    } else if (live.ready) {
      svgParts.push(`<circle cx="${x + barWidth / 2}" cy="${y + barHeight + 12}" r="3.8" fill="#bf5f2f" />`);
    }

    svgParts.push(
      `<text x="${x + barWidth / 2}" y="${height - 14}" text-anchor="middle" fill="${chromosome === activeChromosome ? SELECTED_ACCENT : "#5b6672"}" font-size="12" font-family="Space Grotesk" font-weight="700">${chromosome}</text>`,
    );
    // Transparent click-capture rect over the full bar column
    svgParts.push(
      `<rect x="${x - 3}" y="0" width="${barWidth + 6}" height="${height}" fill="transparent" pointer-events="all" data-chr="${chromosome}" style="cursor:pointer" />`,
    );
  });

  svgParts.push("</svg>");
  chromosomeAtlas.innerHTML = svgParts.join("");

  // Make each chromosome bar clickable to select that chromosome
  chromosomeAtlas.querySelectorAll("[data-chr]").forEach((el) => {
    el.addEventListener("click", () => {
      const chr = el.getAttribute("data-chr");
      if (chr) handleChromosomeSelection(chr);
    });
  });
}

function renderSelectedChromosomeVisual() {
  const item = chromosomeState(activeChromosome);
  const length = chromosomeLengthValue(activeChromosome);
  const patterns = activePatternItems.slice(0, 3);
  const regions = activeRegionItems.slice(0, 24);
  const width = 760;
  const height = 456;
  const ideogramX = 44;
  const ideogramY = 64;
  const ideogramWidth = 672;
  const ideogramHeight = 30;
  const clipId = `focus-clip-${activeChromosome}`;
  const orfBands = buildOrfDensityBands(activeRegionItems, length);
  const bands = orfBands || buildChromosomeBands(activeChromosome);
  const maxWindowEnd = regions.length
    ? Math.max(...regions.map((region) => Number(region.window_end || 0)))
    : 0;
  const minWindowStart = regions.length
    ? Math.min(...regions.map((region) => Number(region.window_start || 0)))
    : 0;
  const trackX = 44;
  const trackY = 196;
  const trackWidth = 672;
  const trackHeight = 108;
  const svgParts = [
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Selected chromosome ideogram and analysis track">`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="26" fill="rgba(255,255,255,0.5)" />`,
    `<text x="${ideogramX}" y="34" fill="#18222d" font-size="24" font-family="Space Grotesk" font-weight="700">Chromosome ${activeChromosome}</text>`,
    `<text x="${ideogramX}" y="52" fill="#5b6672" font-size="13" font-family="IBM Plex Sans">${formatMb(length)} reference span · ${item.patternsReady ? "patterns ready" : "patterns pending"} · ${item.regionsReady ? "regions ready" : "regions pending"}</text>`,
    `<defs><clipPath id="${clipId}"><rect x="${ideogramX}" y="${ideogramY}" width="${ideogramWidth}" height="${ideogramHeight}" rx="15" ry="15" /></clipPath></defs>`,
    `<rect x="${ideogramX}" y="${ideogramY}" width="${ideogramWidth}" height="${ideogramHeight}" rx="15" ry="15" fill="#f8f4ee" stroke="rgba(24,34,45,0.4)" stroke-width="1.3" />`,
  ];

  bands.forEach((band) => {
    const bandX = ideogramX + (band.start * ideogramWidth);
    const bandWidth = Math.max(2, (band.end - band.start) * ideogramWidth);
    const dataAttrs = band.windowStart != null
      ? `class="orf-band" style="cursor:pointer" data-wstart="${band.windowStart}" data-wend="${band.windowEnd}" data-orf="${band.orfCount}" data-motif="${band.motifHits}" data-gc="${band.gcContent}"`
      : `class="orf-band"`;
    svgParts.push(
      `<rect x="${bandX.toFixed(1)}" y="${ideogramY}" width="${bandWidth.toFixed(1)}" height="${ideogramHeight}" fill="${band.fill}" stroke="${band.stroke}" stroke-width="0.45" clip-path="url(#${clipId})" ${dataAttrs} />`,
    );
  });
  if (orfBands) {
    const cr = centromereRatio(activeChromosome);
    const cx = ideogramX + (cr - 0.02) * ideogramWidth;
    const cw = Math.max(8, 0.04 * ideogramWidth);
    svgParts.push(`<rect x="${cx.toFixed(1)}" y="${ideogramY - 2}" width="${cw.toFixed(1)}" height="${ideogramHeight + 4}" rx="5" fill="${CENTROMERE_FILL}" opacity="0.85" clip-path="url(#${clipId})" pointer-events="none" />`);
  }

  if (regions.length && length > 0 && maxWindowEnd > minWindowStart) {
    const highlightStart = ideogramX + (minWindowStart / length) * ideogramWidth;
    const highlightWidth = Math.max(18, ((maxWindowEnd - minWindowStart) / length) * ideogramWidth);
    svgParts.push(`<rect x="${highlightStart}" y="${ideogramY - 6}" width="${highlightWidth}" height="${ideogramHeight + 12}" rx="12" fill="rgba(113,103,199,0.16)" stroke="${SELECTED_ACCENT}" stroke-width="1.4" pointer-events="none" />`);
    svgParts.push(`<path d="M ${highlightStart + 8} ${ideogramY + ideogramHeight + 8} C ${highlightStart + 18} 136, ${trackX + 24} 142, ${trackX + 24} ${trackY}" fill="none" stroke="rgba(24,34,45,0.35)" stroke-width="1.4" pointer-events="none" />`);
    svgParts.push(`<path d="M ${highlightStart + highlightWidth - 8} ${ideogramY + ideogramHeight + 8} C ${highlightStart + highlightWidth - 18} 136, ${trackX + trackWidth - 24} 142, ${trackX + trackWidth - 24} ${trackY}" fill="none" stroke="rgba(24,34,45,0.35)" stroke-width="1.4" pointer-events="none" />`);
  }

  svgParts.push(`<text x="${ideogramX}" y="${ideogramY + 56}" fill="#5b6672" font-size="12" font-family="IBM Plex Sans">0 Mb</text>`);
  svgParts.push(`<text x="${ideogramX + ideogramWidth}" y="${ideogramY + 56}" text-anchor="end" fill="#5b6672" font-size="12" font-family="IBM Plex Sans">${formatMb(length)}</text>`);
  svgParts.push(`<rect x="${trackX}" y="${trackY}" width="${trackWidth}" height="${trackHeight}" rx="18" fill="rgba(255,255,255,0.72)" stroke="rgba(24,34,45,0.16)" stroke-width="1.2" />`);
  svgParts.push(`<text x="${trackX}" y="${trackY - 14}" fill="#bf5f2f" font-size="12" font-family="IBM Plex Sans" font-weight="700">ANALYSIS WINDOW TRACK</text>`);

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
      const regionX = trackX + ((start - minWindowStart) / (maxWindowEnd - minWindowStart || 1)) * trackWidth;
      const regionWidth = Math.max(14, ((end - start) / (maxWindowEnd - minWindowStart || 1)) * trackWidth);
      const columnHeight = 18 + (gc * 1.1);
      const regionY = trackY + trackHeight - columnHeight - 14;
      const labelY = trackY + trackHeight - 4;

      svgParts.push(`<rect x="${regionX}" y="${regionY}" width="${regionWidth}" height="${columnHeight}" rx="8" fill="${gcColor(gc)}" opacity="0.88" />`);

      if (motifHits > 0) {
        svgParts.push(`<circle cx="${regionX + regionWidth / 2}" cy="${regionY - 8}" r="${Math.min(6, 2 + motifHits / 2)}" fill="#bf5f2f" opacity="0.78" />`);
      }

      if (orfCount > 0) {
        const flagX = regionX + regionWidth - 4;
        const flagY = regionY - 16;
        svgParts.push(`<path d="M ${flagX} ${flagY} l 10 4 l -10 4 z" fill="${SELECTED_ACCENT}" />`);
        svgParts.push(`<line x1="${flagX}" y1="${flagY - 2}" x2="${flagX}" y2="${regionY}" stroke="${SELECTED_ACCENT}" stroke-width="1.2" />`);
      }

      if (index < 4) {
        svgParts.push(`<text x="${regionX + regionWidth / 2}" y="${labelY}" text-anchor="middle" fill="#5b6672" font-size="10" font-family="IBM Plex Sans">${(start / 1000000).toFixed(1)}</text>`);
      }

      if (repeatBases > 0) {
        const repeatWidth = Math.min(regionWidth, Math.max(5, repeatBases / 900));
        svgParts.push(`<rect x="${regionX}" y="${trackY + 10}" width="${repeatWidth}" height="8" rx="4" fill="rgba(24,34,45,0.18)" />`);
      }
    });

    const topPatternBadges = patterns.map((pattern, index) => {
      const x = trackX + (index * 182);
      const label = `${pattern.pattern_name} · ${pattern.hit_count}`;
      return [
        `<rect x="${x}" y="326" width="170" height="28" rx="14" fill="rgba(255,255,255,0.84)" stroke="rgba(24,34,45,0.12)" />`,
        `<text x="${x + 12}" y="344" fill="#18222d" font-size="12" font-family="IBM Plex Sans">${escapeHtml(label)}</text>`,
      ].join("");
    });
    svgParts.push(...topPatternBadges);
  }

  // CpG motif density track (full-chromosome coordinates)
  const cpgY = 378;
  const cpgH = 52;
  const cpgMax = Math.max(1, ...activeRegionItems.map((r) => Number(r.motif_hits || 0)));
  svgParts.push(`<text x="${trackX}" y="${cpgY - 8}" fill="#0d9488" font-size="12" font-family="IBM Plex Sans" font-weight="700">CpG MOTIFS (DENSITY)</text>`);
  svgParts.push(`<rect x="${trackX}" y="${cpgY}" width="${trackWidth}" height="${cpgH}" rx="12" fill="rgba(240,253,252,0.88)" stroke="rgba(13,148,136,0.3)" stroke-width="1.2" />`);
  if (activeRegionItems.length && length > 0) {
    activeRegionItems.forEach((region) => {
      const rStart = Number(region.window_start || 0);
      const rEnd = Number(region.window_end || 0);
      const motif = Number(region.motif_hits || 0);
      if (motif === 0) return;
      const rx = trackX + (rStart / length) * trackWidth;
      const rw = Math.max(2, ((rEnd - rStart) / length) * trackWidth);
      const intensity = motif / cpgMax;
      const barH = Math.round(6 + intensity * (cpgH - 10));
      svgParts.push(`<rect x="${rx.toFixed(1)}" y="${cpgY + cpgH - barH}" width="${rw.toFixed(1)}" height="${barH}" rx="3" fill="rgba(13,148,136,${(0.25 + intensity * 0.75).toFixed(2)})" class="cpg-band" style="cursor:pointer" data-wstart="${rStart}" data-wend="${rEnd}" data-motif="${motif}" />`);
    });
  } else {
    svgParts.push(`<text x="${trackX + 24}" y="${cpgY + 32}" fill="#5b6672" font-size="13" font-family="IBM Plex Sans">CpG density will populate after full analysis.</text>`);
  }
  svgParts.push(`<text x="${trackX}" y="${cpgY + cpgH + 14}" fill="#5b6672" font-size="11" font-family="IBM Plex Sans">0</text>`);
  svgParts.push(`<text x="${trackX + trackWidth}" y="${cpgY + cpgH + 14}" text-anchor="end" fill="#5b6672" font-size="11" font-family="IBM Plex Sans">${formatMb(length)}</text>`);

  svgParts.push("</svg>");
  isZoomedIn = false; // overview is now showing
  selectedChromosomeVisual.innerHTML = svgParts.join("");
  attachGenomeTrackEvents(activeChromosome);

  if (!regions.length) {
    selectedChromosomeVisualNote.textContent = `Chromosome ${activeChromosome} does not have region-level outputs yet, so the lower lens is waiting on full analysis.`;
    return;
  }

  const firstRegion = regions[0];
  const lastRegion = regions[regions.length - 1];
  selectedChromosomeVisualNote.textContent = `The lower lens covers ${formatWindowRange(Number(firstRegion.window_start || 0), Number(lastRegion.window_end || 0))} with GC-colored windows, motif markers, ORF flags, and repeat-density tags for chromosome ${activeChromosome}.`;
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
    const card = document.createElement("article");
    card.className = "summary-card";
    card.innerHTML = `
      <span class="metric-label">Chromosome ${item.chromosome}</span>
      <strong>${item.length}</strong>
      <p>GC content: ${item.gc}</p>
      <p>Patterns: ${item.patterns}</p>
      <p>ORF status: ${item.orfs}</p>
    `;
    summaryCards.appendChild(card);
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
}

function renderGcBars() {
  gcBars.innerHTML = "";

  if (!gcValues.length) {
    const note = document.createElement("p");
    note.className = "table-empty";
    note.textContent = `No GC windows available for chromosome ${activeChromosome} yet.`;
    gcBars.appendChild(note);
    return;
  }

  gcValues.forEach((value, index) => {
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.max(18, value * 2.2)}px`;
    bar.title = `${activeChromosome} window ${index + 1}: ${value}% GC`;
    gcBars.appendChild(bar);
  });
}

function applySummary(summary) {
  activeSummary = summary;
  // Always update the inventory (drives status cards and eligibility)
  chromosomeInventory.set(summary.chromosome, {
    ...chromosomeState(summary.chromosome),
    sequenceLength: summary.sequence_length,
    avgGcContent: summary.avg_gc_content,
    fullAnalysisEligible: summary.full_analysis_eligible,
    fullAnalysisStatus: summary.full_analysis_status,
    fullAnalysisReason: summary.full_analysis_reason,
    fullAnalysisMaxBases: summary.full_analysis_max_bases,
  });

  // Skip the summary card if patterns are marked ready but count is 0 —
  // this means Athena MSCK propagation hasn't finished yet. Keep showing
  // the previous card data instead of overwriting with stale zeros.
  const hasRealData = summary.pattern_hit_count && summary.pattern_hit_count !== "0";
  const patternsKnownReady = summary.patterns_ready && summary.regions_ready;
  if (patternsKnownReady && !hasRealData) {
    // Show a "loading" placeholder so previous chromosome's data doesn't show
    chromosomeSummaries.length = 0;
    chromosomeSummaries.push({
      chromosome: summary.chromosome,
      length: summary.sequence_length ? `${Number(summary.sequence_length).toLocaleString()} bp` : "n/a",
      gc: summary.avg_gc_content ? `${summary.avg_gc_content}%` : "n/a",
      patterns: "Loading from Athena…",
      orfs: "Loading from Athena…",
    });
    renderSummaryCards();
    if (!isZoomedIn) {
      renderSelectedChromosomeVisual();
      // Set AFTER render — renderSelectedChromosomeVisual overwrites the note text
      if (selectedChromosomeVisualNote) {
        selectedChromosomeVisualNote.textContent =
          "Loading analysis data from Athena — pattern and region windows will appear shortly.";
      }
    }
    return;
  }

  chromosomeSummaries.length = 0;
  chromosomeSummaries.push({
    chromosome: summary.chromosome,
    length: summary.sequence_length ? `${Number(summary.sequence_length).toLocaleString()} bp` : "n/a",
    gc: summary.avg_gc_content ? `${summary.avg_gc_content}%` : "n/a",
    patterns: summary.pattern_hit_count ? `${summary.pattern_hit_count} hits` : "n/a",
    orfs: summary.orf_count ? `${summary.orf_count} window ORFs` : "n/a",
  });
  renderSummaryCards();
  if (!isZoomedIn) renderSelectedChromosomeVisual();
}

function applyPatterns(items) {
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
  if (!isZoomedIn) renderSelectedChromosomeVisual();
}

function applyRegions(items) {
  activeRegionItems = items.slice();
  orfRows.length = 0;
  gcValues.length = 0;

  items.forEach((item) => {
    orfRows.push({
      window: `${activeChromosome}:${item.window_start}-${item.window_end}`,
      gc: item.gc_content,
      orfs: item.orf_count,
    });
    gcValues.push(Number(item.gc_content || 0));
  });

  renderOrfTable();
  renderGcBars();
  if (!isZoomedIn) renderSelectedChromosomeVisual();
}

function showUnavailableChromosome(chromosome) {
  // Never wipe the visualization while the user is in a zoomed view
  if (isZoomedIn) return;
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

function applyOrfs(items) {
  activeOrfItems = items.slice();
}

function applyCpgs(items) {
  activeCpgItems = items.slice();
}

function getOrCreateTooltip() {
  let tip = document.getElementById("genomeTooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "genomeTooltip";
    tip.className = "genome-tooltip";
    document.body.appendChild(tip);
    // Hide tooltip on scroll or any click outside the lens
    window.addEventListener("scroll", () => { tip.style.display = "none"; }, { passive: true });
    document.addEventListener("click", () => { tip.style.display = "none"; }, { capture: true, passive: true });
  }
  return tip;
}

function attachGenomeTrackEvents(chromosome) {
  try {
  const tip = getOrCreateTooltip();
  const moveTip = (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY - 8}px`; };
  const hideTip = () => { tip.style.display = "none"; };

  // Band hover tooltips
  const chrLen = chromosomeLengthValue(chromosome) || 1;

  // Build a sorted lookup of regions for coordinate → window mapping
  const regionLookup = activeRegionItems
    .filter(r => r.window_start != null)
    .map(r => ({ ws: +r.window_start, we: +r.window_end, orf: +r.orf_count || 0, motif: +r.motif_hits || 0, gc: +r.gc_content || 0 }))
    .sort((a, b) => a.ws - b.ws);

  // CpG density bands — hover tooltip + click-to-zoom (these are full-width, reliable targets)
  selectedChromosomeVisual.querySelectorAll(".cpg-band").forEach((el) => {
    el.style.cursor = "pointer";
    el.addEventListener("mouseenter", (e) => {
      tip.innerHTML = `<strong>${formatCoord(+el.dataset.wstart)} – ${formatCoord(+el.dataset.wend)}</strong><br>CpG hits: <b>${el.dataset.motif}</b><br><small style="color:#0d9488">Click to zoom</small>`;
      tip.style.display = "block";
      moveTip(e);
    });
    el.addEventListener("mousemove", moveTip);
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("click", () => {
      hideTip();
      const ws = +el.dataset.wstart, we = +el.dataset.wend, span = we - ws;
      zoomToGenomeRegion(chromosome, Math.max(0, ws - span), Math.min(chrLen, we + span));
    });
  });

  // Use the container div for reliable clicks — abort previous listeners first
  if (selectedChromosomeVisual._lensAbort) selectedChromosomeVisual._lensAbort.abort();
  const lensCtrl = new AbortController();
  selectedChromosomeVisual._lensAbort = lensCtrl;
  const sig = lensCtrl.signal;

  if (!regionLookup.length) return;

  const IX = 44, IW = 672, IY = 64, IH = 30;

  // Always query the CURRENT svg at event time — a stale closure reference returns
  // zero bounding rect if innerHTML was replaced, breaking inIdeogram/regionAtClientX
  function currentSvg() { return selectedChromosomeVisual.querySelector("svg"); }

  function regionAtClientX(clientX) {
    const s = currentSvg(); if (!s) return regionLookup[0];
    const r = s.getBoundingClientRect();
    const scale = r.width / 760;
    const frac = Math.max(0, Math.min(1, (clientX - r.left - IX * scale) / (IW * scale)));
    const genomicPos = frac * chrLen;
    let best = regionLookup[0];
    let bestD = Infinity;
    for (const reg of regionLookup) {
      const mid = (reg.ws + reg.we) / 2;
      const d = Math.abs(mid - genomicPos);
      if (d < bestD) { bestD = d; best = reg; } else if (d > bestD) break;
    }
    return best;
  }

  function inIdeogram(e) {
    const s = currentSvg(); if (!s) return false;
    const r = s.getBoundingClientRect();
    if (!r.width) return false;
    const scale = r.width / 760;
    const svgX = (e.clientX - r.left) / scale;
    const svgY = (e.clientY - r.top) / scale;
    return svgX >= IX && svgX <= IX + IW && svgY >= IY - 12 && svgY <= IY + IH + 12;
  }

  selectedChromosomeVisual.addEventListener("mousemove", (e) => {
    if (!inIdeogram(e)) { hideTip(); return; }
    const reg = regionAtClientX(e.clientX);
    if (!reg) return;
    selectedChromosomeVisual.style.cursor = "pointer";
    tip.innerHTML = `<strong>${formatCoord(reg.ws)} – ${formatCoord(reg.we)}</strong><br>ORFs: <b>${reg.orf}</b> &nbsp;·&nbsp; CpG hits: <b>${reg.motif}</b> &nbsp;·&nbsp; GC: <b>${reg.gc.toFixed(1)}%</b><br><small style="color:#7167c7">Click to zoom into this region</small>`;
    tip.style.display = "block";
    moveTip(e);
  }, { signal: sig });

  selectedChromosomeVisual.addEventListener("mouseleave", () => {
    hideTip();
    selectedChromosomeVisual.style.cursor = "";
  }, { signal: sig });

  selectedChromosomeVisual.addEventListener("click", (e) => {
    if (!inIdeogram(e)) return;
    hideTip();
    const reg = regionAtClientX(e.clientX);
    if (!reg) return;
    const span = reg.we - reg.ws;
    selectedChromosomeVisualNote.textContent = `Zooming into ${formatCoord(reg.ws)} – ${formatCoord(reg.we + span)}…`;
    zoomToGenomeRegion(chromosome, Math.max(0, reg.ws - span), Math.min(chrLen, reg.we + span));
  }, { signal: sig });
  } catch (err) {
    console.warn("attachGenomeTrackEvents failed:", err);
  }
}

async function zoomToGenomeRegion(chromosome, regionStart, regionEnd) {
  if (!API_BASE_URL) return;

  // Cancel any previous in-flight background fetch for another region
  if (zoomAbortController) zoomAbortController.abort();
  zoomAbortController = new AbortController();
  const signal = zoomAbortController.signal;

  const tip = getOrCreateTooltip();
  tip.style.display = "none";

  // Render immediately — no wait for Athena
  applyOrfs([]);
  applyCpgs([]);
  renderZoomedGenomeTrack(chromosome, regionStart, regionEnd);

  // Background fetch: individual ORF + CpG positions
  try {
    const headers = {};
    const [orfRes, cpgRes] = await Promise.all([
      fetch(`${API_BASE_URL}/api/chromosomes/${chromosome}/orfs?start=${regionStart}&end=${regionEnd}`, { signal })
        .then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(`${API_BASE_URL}/api/chromosomes/${chromosome}/cpg?start=${regionStart}&end=${regionEnd}`, { signal })
        .then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    if (signal.aborted) return; // User zoomed out — discard results

    const orfs = orfRes && Array.isArray(orfRes.items) ? orfRes.items : [];
    const cpgs = cpgRes && Array.isArray(cpgRes.items) ? cpgRes.items : [];
    if (orfs.length || cpgs.length) {
      applyOrfs(orfs);
      applyCpgs(cpgs);
      renderZoomedGenomeTrack(chromosome, regionStart, regionEnd);
    }
  } catch (err) {
    if (!signal.aborted) console.warn("ORF/CpG detail fetch failed:", err);
  }
}

function renderZoomedGenomeTrack(chromosome, viewStart, viewEnd) {
  const span = viewEnd - viewStart || 1;
  const orfs = activeOrfItems;
  const cpgs = activeCpgItems;
  const lx = 44;
  const tw = 672;
  const orfH = 110;
  const cpgH = 80;
  const svgH = 456;

  const svgParts = [
    `<svg viewBox="0 0 760 ${svgH}" role="img" aria-label="Zoomed region">`,
    `<rect x="0" y="0" width="760" height="${svgH}" rx="26" fill="rgba(255,255,255,0.5)" />`,
    `<text x="${lx}" y="30" fill="#18222d" font-size="18" font-family="Space Grotesk" font-weight="700">Chr${escapeHtml(chromosome)}: ${formatCoord(viewStart)} – ${formatCoord(viewEnd)}</text>`,
    `<text x="${lx}" y="48" fill="#5b6672" font-size="12" font-family="IBM Plex Sans">${orfs.length} candidate ORFs · ${cpgs.length} CpG sites · ${formatMb(span)} span</text>`,
    `<rect x="${lx}" y="56" width="100" height="26" rx="13" fill="${SELECTED_ACCENT}" class="zoom-out-btn" style="cursor:pointer" />`,
    `<text x="${lx + 50}" y="73" text-anchor="middle" fill="white" font-size="12" font-family="IBM Plex Sans" class="zoom-out-btn" style="cursor:pointer;pointer-events:none">← Zoom out</text>`,
  ];

  // ORF track
  const orfY = 94;
  svgParts.push(`<text x="${lx}" y="${orfY - 8}" fill="#7167c7" font-size="12" font-family="IBM Plex Sans" font-weight="700">CANDIDATE ORFs (${orfs.length})</text>`);
  svgParts.push(`<rect x="${lx}" y="${orfY}" width="${tw}" height="${orfH}" rx="12" fill="rgba(248,244,238,0.9)" stroke="rgba(113,103,199,0.3)" stroke-width="1.2" />`);
  svgParts.push(`<line x1="${lx}" y1="${orfY + orfH / 2}" x2="${lx + tw}" y2="${orfY + orfH / 2}" stroke="rgba(113,103,199,0.18)" stroke-width="1" stroke-dasharray="4,4" />`);
  svgParts.push(`<text x="${lx + tw + 8}" y="${orfY + 24}" fill="#7167c7" font-size="11" font-family="IBM Plex Sans">+</text>`);
  svgParts.push(`<text x="${lx + tw + 8}" y="${orfY + orfH - 8}" fill="#7167c7" font-size="11" font-family="IBM Plex Sans">−</text>`);

  orfs.forEach((orf) => {
    const s = Number(orf.pos_start || 0);
    const e = Number(orf.pos_end || 0);
    const x = lx + ((s - viewStart) / span) * tw;
    const w = Math.max(2, ((e - s) / span) * tw);
    const isPlus = (orf.strand || "+") !== "-";
    const ry = isPlus ? orfY + 8 : orfY + orfH / 2 + 4;
    const rh = orfH / 2 - 14;
    svgParts.push(`<rect x="${x.toFixed(1)}" y="${ry}" width="${w.toFixed(1)}" height="${rh}" rx="3" fill="${SELECTED_ACCENT}" opacity="0.82" class="zoom-orf" data-start="${s}" data-end="${e}" data-strand="${escapeHtml(orf.strand || '+')}" data-len="${orf.hit_length || ''}" />`);
  });

  // CpG track
  const cpgY = orfY + orfH + 24;
  svgParts.push(`<text x="${lx}" y="${cpgY - 8}" fill="#0d9488" font-size="12" font-family="IBM Plex Sans" font-weight="700">CpG MOTIFS (${cpgs.length})</text>`);
  svgParts.push(`<rect x="${lx}" y="${cpgY}" width="${tw}" height="${cpgH}" rx="12" fill="rgba(240,253,252,0.9)" stroke="rgba(13,148,136,0.3)" stroke-width="1.2" />`);
  if (cpgs.length === 0) {
    svgParts.push(`<text x="${lx + 20}" y="${cpgY + cpgH / 2 + 4}" fill="#5b6672" font-size="13" font-family="IBM Plex Sans">No CpG sites detected in this region — try dragging a wider selection</text>`);
  }
  cpgs.forEach((cpg) => {
    const x = lx + ((Number(cpg.pos_start || 0) - viewStart) / span) * tw;
    svgParts.push(`<rect x="${x.toFixed(1)}" y="${cpgY + 6}" width="4" height="${cpgH - 12}" rx="2" fill="rgba(13,148,136,0.72)" class="zoom-cpg" data-start="${cpg.pos_start}" />`);
  });

  // Coordinate axis
  const axisY = cpgY + cpgH + 20;
  svgParts.push(`<line x1="${lx}" y1="${axisY}" x2="${lx + tw}" y2="${axisY}" stroke="rgba(24,34,45,0.18)" stroke-width="1" />`);
  for (let i = 0; i <= 4; i++) {
    const pos = viewStart + (i / 4) * span;
    const x = lx + (i / 4) * tw;
    svgParts.push(`<line x1="${x.toFixed(1)}" y1="${axisY}" x2="${x.toFixed(1)}" y2="${axisY + 4}" stroke="rgba(24,34,45,0.3)" stroke-width="1" />`);
    svgParts.push(`<text x="${x.toFixed(1)}" y="${axisY + 15}" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}" fill="#5b6672" font-size="11" font-family="IBM Plex Sans">${formatCoord(pos)}</text>`);
  }

  svgParts.push("</svg>");
  isZoomedIn = true; // block background renders while zoomed
  selectedChromosomeVisual.innerHTML = svgParts.join("");
  selectedChromosomeVisualNote.textContent = `Zoomed: ${formatCoord(viewStart)} – ${formatCoord(viewEnd)} · ${orfs.length} candidate ORFs · ${cpgs.length} CpG sites`;

  // Zoom-out button restores overview
  selectedChromosomeVisual.querySelectorAll(".zoom-out-btn").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      // Cancel any background ORF/CpG fetch so it can't overwrite the overview
      if (zoomAbortController) { zoomAbortController.abort(); zoomAbortController = null; }
      activeOrfItems = [];
      activeCpgItems = [];
      renderSelectedChromosomeVisual();
    });
  });

  // Tooltips for zoomed ORFs
  const tip = getOrCreateTooltip();
  const moveTip = (e) => { tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY - 8}px`; };
  const hideTip = () => { tip.style.display = "none"; };

  selectedChromosomeVisual.querySelectorAll(".zoom-orf").forEach((el) => {
    el.addEventListener("mouseenter", (e) => {
      tip.innerHTML = `ORF ${formatCoord(+el.dataset.start)} – ${formatCoord(+el.dataset.end)}<br>Length: ${el.dataset.len ? `${Number(el.dataset.len).toLocaleString()} bp` : "n/a"} &nbsp;·&nbsp; Strand: ${el.dataset.strand}`;
      tip.style.display = "block";
      moveTip(e);
    });
    el.addEventListener("mousemove", moveTip);
    el.addEventListener("mouseleave", hideTip);
  });

  selectedChromosomeVisual.querySelectorAll(".zoom-cpg").forEach((el) => {
    el.addEventListener("mouseenter", (e) => {
      tip.innerHTML = `CpG at ${formatCoord(+el.dataset.start)}`;
      tip.style.display = "block";
      moveTip(e);
    });
    el.addEventListener("mousemove", moveTip);
    el.addEventListener("mouseleave", hideTip);
  });
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

  const response = await fetch(`${API_BASE_URL}${path}`);
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

async function loadChromosomeDetails(chromosome) {
  setSelectedChromosome(chromosome);
  const live = chromosomeState(chromosome);

  if (!API_BASE_URL) {
    if (chromosome !== DEFAULT_CHROMOSOME) {
      showUnavailableChromosome(chromosome);
    }
    return;
  }

  if (!live.ready) {
    showUnavailableChromosome(chromosome);
    return;
  }

  // Clear stale pattern badges from any previous chromosome immediately
  activePatternItems = [];
  patternRows.length = 0;
  renderPatternTable();

  // Show immediate loading feedback
  selectedChromosomeStatus.textContent = `Loading chromosome ${chromosome} data…`;
  selectedChromosomeVisualNote.textContent = `Fetching analysis data for chromosome ${chromosome}…`;

  // Use individual .catch() so one slow/failed endpoint doesn't clear all data
  const [summary, patterns, regions] = await Promise.all([
    fetchJson(`/api/chromosomes/${chromosome}/summary`).catch(e => { console.warn("summary failed", e); return null; }),
    fetchJson(`/api/chromosomes/${chromosome}/patterns`).catch(e => { console.warn("patterns failed", e); return null; }),
    fetchJson(`/api/chromosomes/${chromosome}/regions`).catch(e => { console.warn("regions failed", e); return null; }),
  ]);

  if (!summary && !patterns && !regions) {
    // All three failed — chromosome truly unavailable
    showUnavailableChromosome(chromosome);
    return;
  }

  if (summary) applySummary(summary);
  // Only replace existing data with non-empty results — empty arrays from Athena cold/miss
  // must not clear a visualization that is already showing correctly
  if (patterns && Array.isArray(patterns.items) && patterns.items.length > 0) applyPatterns(patterns.items);
  if (regions && Array.isArray(regions.items) && regions.items.length > 0) applyRegions(regions.items);

  renderChromosomeGrid();
  startBatchPollingIfNeeded(chromosome);
}

async function hydrateDashboard() {
  if (!API_BASE_URL) {
    return;
  }

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

  await loadChromosomeDetails(activeChromosome);
}

let batchPollTimer = null;

function stopBatchPolling() {
  if (batchPollTimer) { clearInterval(batchPollTimer); batchPollTimer = null; }
}

async function pollBatchStatus(chromosome) {
  if (!API_BASE_URL) return;
  try {
    const bs = await fetchJson(`/api/chromosomes/${chromosome}/batch-status`);
    const item = chromosomeState(chromosome);
    if (!item) return;

    chromosomeInventory.set(chromosome, { ...item, batchStatus: bs });

    if (chromosome === activeChromosome) {
      updateSelectionMeta(chromosomeState(chromosome));
    }

    // Stop polling once done or failed
    if (bs.status === "SUCCEEDED" || bs.status === "FAILED" || bs.status === "no_job") {
      stopBatchPolling();
      if (bs.status === "SUCCEEDED") {
        // Reload chromosome data — results should now be in S3/Athena
        // Trigger MSCK REPAIR + cache clearing — /sync waits for Athena before returning
        // Show progress in the status cards so the user knows what's happening
        const syncMsg = "Syncing Athena partitions… (~30s)";
        if (selectedPatternDetail) selectedPatternDetail.textContent = syncMsg;
        if (selectedRegionDetail) selectedRegionDetail.textContent = syncMsg;
        if (API_BASE_URL) {
          fetch(`${API_BASE_URL}/api/chromosomes/${chromosome}/sync`, { method: "POST" })
            .then(() => {
              if (selectedPatternDetail) selectedPatternDetail.textContent = "Sync complete — loading results…";
              if (selectedRegionDetail) selectedRegionDetail.textContent = "Sync complete — loading results…";
              setTimeout(() => hydrateDashboard(), 5000);
            })
            .catch(e => { console.warn("sync failed", e); setTimeout(() => hydrateDashboard(), 10000); });
        } else {
          setTimeout(() => hydrateDashboard(), 10000);
        }
      }
    }
  } catch (err) {
    console.warn("Batch status poll failed:", err);
  }
}

function startBatchPollingIfNeeded(chromosome) {
  stopBatchPolling();
  const item = chromosomeState(chromosome);
  if (!item || !item.ready) return;
  if (item.patternsReady && item.regionsReady) return;
  if (!API_BASE_URL) return;

  // Start polling immediately then every 30s
  pollBatchStatus(chromosome);
  batchPollTimer = setInterval(() => pollBatchStatus(chromosome), 30000);
}

function handleChromosomeSelection(chromosome) {
  stopBatchPolling();
  if (zoomAbortController) { zoomAbortController.abort(); zoomAbortController = null; }
  // Clear stale pattern badges from the previous chromosome
  activePatternItems = [];
  patternRows.length = 0;
  renderPatternTable();
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
    if (backend === "AWS Batch on Fargate") {
      // Give Batch 2 seconds to register the job, then start polling
      const chr = activeChromosome;
      setTimeout(() => startBatchPollingIfNeeded(chr), 2000);
    }
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
renderSummaryCards();
renderPatternTable();
renderOrfTable();
renderGcBars();
renderChromosomeAtlas();
renderSelectedChromosomeVisual();
renderPreview(buildSinglePayload(singleJobForm.elements));
hydrateDashboard();
