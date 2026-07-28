const root = htmlNode;

const COLORS = {
  mars: "#f04438",
  lmcache: "#3b82f6",
  mooncake: "#9b6cff",
  recompute: "#9ca3af",
};

const BAR_COLORS = {
  mars: "#f04438",
  lmcache: "#9ca3af",
  mooncake: "#9ca3af",
  recompute: "#9ca3af",
};

const DISPLAY_NAMES = {
  mars: "MARS",
  lmcache: "LMCache",
  mooncake: "Mooncake",
  recompute: "Recompute",
};

const ORDER = ["mars", "lmcache", "mooncake", "recompute"];

function valuesToArray(values) {
  if (!values) {
    return [];
  }

  if (Array.isArray(values)) {
    return values;
  }

  if (typeof values.toArray === "function") {
    return values.toArray();
  }

  if (typeof values.length === "number") {
    const output = [];

    for (let index = 0; index < values.length; index += 1) {
      output.push(
        typeof values.get === "function"
          ? values.get(index)
          : values[index]
      );
    }

    return output;
  }

  return [];
}

function finiteNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getField(frame, type) {
  if (!frame || !Array.isArray(frame.fields)) {
    return null;
  }

  if (type === "time") {
    return (
      frame.fields.find((field) => field.type === "time") ||
      frame.fields.find((field) =>
        String(field.name || "").toLowerCase().includes("time")
      )
    );
  }

  return (
    frame.fields.find(
      (field) =>
        field.type === "number" &&
        String(field.name || "").toLowerCase() !== "time"
    ) ||
    frame.fields.find((field) => field.type === "number")
  );
}

function getSystem(frame) {
  const numericField = getField(frame, "number");

  const candidates = [
    numericField?.labels?.system,
    frame?.labels?.system,
    frame?.name,
  ];

  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }

    const normalized = String(candidate).trim().toLowerCase();

    if (ORDER.includes(normalized)) {
      return normalized;
    }

    for (const system of ORDER) {
      if (normalized.includes(system)) {
        return system;
      }
    }
  }

  return null;
}

function getRefId(frame) {
  return String(
    frame?.refId ||
      frame?.meta?.refId ||
      frame?.meta?.custom?.refId ||
      ""
  ).toUpperCase();
}

function framesForRef(refId) {
  return (data?.series || []).filter(
    (frame) => getRefId(frame) === refId.toUpperCase()
  );
}

function lastFinite(values) {
  const array = valuesToArray(values);

  for (let index = array.length - 1; index >= 0; index -= 1) {
    const value = finiteNumber(array[index]);

    if (value !== null) {
      return value;
    }
  }

  return null;
}

function latestFromFrames(frames) {
  for (const frame of frames) {
    const numberField = getField(frame, "number");

    if (!numberField) {
      continue;
    }

    const value = lastFinite(numberField.values);

    if (value !== null) {
      return value;
    }
  }

  return null;
}

function seriesMap(frames) {
  const result = {};

  frames.forEach((frame) => {
    const system = getSystem(frame);
    const timeField = getField(frame, "time");
    const numberField = getField(frame, "number");

    if (!system || !numberField) {
      return;
    }

    const rawValues = valuesToArray(numberField.values);
    const rawTimes = timeField
      ? valuesToArray(timeField.values)
      : rawValues.map((_, index) => index);

    const points = [];

    for (let index = 0; index < rawValues.length; index += 1) {
      const value = finiteNumber(rawValues[index]);
      const time = finiteNumber(rawTimes[index]);

      if (value !== null && time !== null) {
        points.push({ time, value });
      }
    }

    if (points.length > 0) {
      result[system] = points;
    }
  });

  return result;
}

function latestBySystem(map) {
  const result = {};

  ORDER.forEach((system) => {
    const points = map[system] || [];

    if (points.length > 0) {
      result[system] = points[points.length - 1].value;
    }
  });

  return result;
}

function formatSeconds(value) {
  if (!Number.isFinite(value)) {
    return "--";
  }

  if (value < 1) {
    return value.toFixed(2);
  }

  return value.toFixed(1);
}

function setImprovement(valueId, detailId, value, frames) {
  const valueNode = root.getElementById(valueId);
  const detailNode = root.getElementById(detailId);

  if (!valueNode) {
    return;
  }

  if (!Number.isFinite(value)) {
    valueNode.textContent = "--";
    return;
  }

  valueNode.textContent = (value * 100).toFixed(1);

  const numericField = frames
    .map((frame) => getField(frame, "number"))
    .find(Boolean);

  const secondBest =
    numericField?.labels?.second_best ||
    frames[0]?.labels?.second_best;

  if (detailNode && secondBest) {
    detailNode.textContent =
      `( MARS compared with ${DISPLAY_NAMES[secondBest] || secondBest} )`;
  }
}

function buildBars(containerId, currentValues) {
  const container = root.getElementById(containerId);

  if (!container) {
    return;
  }

  container.replaceChildren();

  const availableValues = Object.values(currentValues).filter(Number.isFinite);

  if (availableValues.length === 0) {
    container.textContent = "Awaiting Prometheus data";
    return;
  }

  const maximum = Math.max(...availableValues) * 1.08;
  const segmentCount = 30;

  ORDER.forEach((system) => {
    const value = currentValues[system];

    if (!Number.isFinite(value)) {
      return;
    }

    const row = document.createElement("div");
    row.className = "lcd-row";

    const name = document.createElement("div");
    name.className = `lcd-name ${system === "mars" ? "is-mars" : ""}`;
    name.textContent = DISPLAY_NAMES[system];

    const track = document.createElement("div");
    track.className = "lcd-track";
    track.style.setProperty("--bar-color", BAR_COLORS[system]);

    const activeSegments = Math.max(
      1,
      Math.round((value / maximum) * segmentCount)
    );

    for (let index = 0; index < segmentCount; index += 1) {
      const segment = document.createElement("span");

      segment.className =
        index < activeSegments
          ? "lcd-segment active"
          : "lcd-segment";

      track.appendChild(segment);
    }

    const valueNode = document.createElement("div");
    valueNode.className = "lcd-value";
    valueNode.style.setProperty("--bar-color", BAR_COLORS[system]);
    valueNode.innerHTML =
      `${formatSeconds(value)}<span class="lcd-unit">s</span>`;

    row.append(name, track, valueNode);
    container.appendChild(row);
  });
}

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(
    "http://www.w3.org/2000/svg",
    tag
  );

  Object.entries(attributes).forEach(([key, value]) => {
    node.setAttribute(key, String(value));
  });

  return node;
}

function niceMaximum(value) {
  if (!Number.isFinite(value) || value <= 0) {
    return 1;
  }

  const exponent = Math.floor(Math.log10(value));
  const magnitude = 10 ** exponent;
  const normalized = value / magnitude;

  let rounded;

  if (normalized <= 1) {
    rounded = 1;
  } else if (normalized <= 1.5) {
    rounded = 1.5;
  } else if (normalized <= 2) {
    rounded = 2;
  } else if (normalized <= 3) {
    rounded = 3;
  } else if (normalized <= 4) {
    rounded = 4;
  } else if (normalized <= 5) {
    rounded = 5;
  } else if (normalized <= 6) {
    rounded = 6;
  } else if (normalized <= 8) {
    rounded = 8;
  } else {
    rounded = 10;
  }

  return rounded * magnitude;
}

function buildChart(svgId, legendId, series, yAxisTitle) {
  const svg = root.getElementById(svgId);
  const legend = root.getElementById(legendId);

  if (!svg || !legend) {
    return;
  }

  svg.replaceChildren();
  legend.replaceChildren();

  const container = svg.parentElement;
  const rect = container ? container.getBoundingClientRect() : null;
  const width = rect && rect.width > 100 ? Math.round(rect.width) : 760;
  const height = rect && rect.height > 100 ? Math.round(rect.height) : 300;

  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const margin = {
    top: 12,
    right: 14,
    bottom: 34,
    left: 62,
  };

  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;

  const allPoints = Object.values(series).flat();

  if (allPoints.length === 0) {
    const message = svgElement("text", {
      x: width / 2,
      y: height / 2,
      "text-anchor": "middle",
      fill: "rgba(255,255,255,0.45)",
      "font-size": 15,
    });

    message.textContent = "Awaiting Prometheus data";
    svg.appendChild(message);
    return;
  }

  const minimumTime = Math.min(
    ...allPoints.map((point) => point.time)
  );

  const maximumTime = Math.max(
    ...allPoints.map((point) => point.time)
  );

  const maximumObserved = Math.max(
    ...allPoints.map((point) => point.value)
  );

  const yMaximum = niceMaximum(maximumObserved * 1.08);
  const timeRange = Math.max(1, maximumTime - minimumTime);

  const xScale = (time) =>
    margin.left +
    ((time - minimumTime) / timeRange) * plotWidth;

  const yScale = (value) =>
    margin.top +
    plotHeight -
    (value / yMaximum) * plotHeight;

  const defs = svgElement("defs");

  ORDER.forEach((system) => {
    const gradient = svgElement("linearGradient", {
      id: `${svgId}-${system}-gradient`,
      x1: "0",
      y1: "0",
      x2: "0",
      y2: "1",
    });

    const topStop = svgElement("stop", {
      offset: "0%",
      "stop-color": COLORS[system],
      "stop-opacity": system === "mars" ? "0.26" : "0.08",
    });

    const bottomStop = svgElement("stop", {
      offset: "100%",
      "stop-color": COLORS[system],
      "stop-opacity": "0",
    });

    gradient.append(topStop, bottomStop);
    defs.appendChild(gradient);
  });

  svg.appendChild(defs);

  function niceTickStep(max) {
    if (max <= 3) return 0.5;
    if (max <= 8) return 1;
    const raw = max / 6;
    const exp = Math.floor(Math.log10(raw));
    const mag = 10 ** exp;
    const norm = raw / mag;
    if (norm <= 1) return mag;
    if (norm <= 2) return 2 * mag;
    if (norm <= 5) return 5 * mag;
    return 10 * mag;
  }

  const tickStep = niceTickStep(yMaximum);

  for (let value = 0; value <= yMaximum; value = +(value + tickStep).toFixed(2)) {
    const y = yScale(value);

    const gridLine = svgElement("line", {
      x1: margin.left,
      y1: y,
      x2: width - margin.right,
      y2: y,
      stroke: "rgba(255,255,255,0.09)",
      "stroke-width": 1,
    });

    const label = svgElement("text", {
      x: margin.left - 9,
      y: y + 4,
      "text-anchor": "end",
      fill: "rgba(255,255,255,0.53)",
      "font-size": 10,
    });

    label.textContent = `${value % 1 === 0 ? value.toFixed(0) : value.toFixed(1)} s`;

    svg.append(gridLine, label);
  }

  const verticalTicks = 6;

  for (let index = 0; index <= verticalTicks; index += 1) {
    const fraction = index / verticalTicks;
    const x = margin.left + fraction * plotWidth;
    const timestamp = minimumTime + fraction * timeRange;

    const gridLine = svgElement("line", {
      x1: x,
      y1: margin.top,
      x2: x,
      y2: margin.top + plotHeight,
      stroke: "rgba(255,255,255,0.055)",
      "stroke-width": 1,
    });

    const label = svgElement("text", {
      x,
      y: height - 12,
      "text-anchor": "middle",
      fill: "rgba(255,255,255,0.48)",
      "font-size": 9,
    });

    const date = new Date(timestamp);

    label.textContent = Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });

    svg.append(gridLine, label);
  }

  const axisTitle = svgElement("text", {
    x: 13,
    y: height / 2,
    transform: `rotate(-90 13 ${height / 2})`,
    "text-anchor": "middle",
    fill: "rgba(255,255,255,0.58)",
    "font-size": 10,
  });

  axisTitle.textContent = yAxisTitle;
  svg.appendChild(axisTitle);

  const drawingOrder = [
    "recompute",
    "lmcache",
    "mooncake",
    "mars",
  ];

  drawingOrder.forEach((system) => {
    const points = series[system];

    if (!points || points.length === 0) {
      return;
    }

    const lineCoordinates = points
      .map(
        (point) =>
          `${xScale(point.time).toFixed(2)},${yScale(point.value).toFixed(2)}`
      )
      .join(" ");

    const areaCoordinates = [
      `${xScale(points[0].time)},${margin.top + plotHeight}`,
      lineCoordinates,
      `${xScale(points[points.length - 1].time)},${margin.top + plotHeight}`,
    ].join(" ");

    const area = svgElement("polygon", {
      points: areaCoordinates,
      fill: `url(#${svgId}-${system}-gradient)`,
      stroke: "none",
    });

    const line = svgElement("polyline", {
      points: lineCoordinates,
      fill: "none",
      stroke: COLORS[system],
      "stroke-width": system === "mars" ? 3.2 : 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      opacity: system === "mars" ? 1 : 0.82,
    });

    svg.append(area, line);

    const samplingStep = Math.max(
      1,
      Math.ceil(points.length / 20)
    );

    points.forEach((point, index) => {
      if (
        index % samplingStep !== 0 &&
        index !== points.length - 1
      ) {
        return;
      }

      const circle = svgElement("circle", {
        cx: xScale(point.time),
        cy: yScale(point.value),
        r: system === "mars" ? 3.2 : 2.4,
        fill: COLORS[system],
        stroke: "rgba(255,255,255,0.28)",
        "stroke-width": 0.7,
      });

      svg.appendChild(circle);
    });

    const legendEntry = document.createElement("span");
    legendEntry.className = "legend-entry";

    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.setProperty("--legend-color", COLORS[system]);

    const label = document.createElement("span");
    label.textContent = DISPLAY_NAMES[system];

    legendEntry.append(swatch, label);
    legend.appendChild(legendEntry);
  });
}

const ttftImprovementFrames = framesForRef("A");
const e2eImprovementFrames = framesForRef("B");

const ttftImprovement = latestFromFrames(ttftImprovementFrames);
const e2eImprovement = latestFromFrames(e2eImprovementFrames);

setImprovement(
  "ttft-improvement",
  "ttft-improvement-detail",
  ttftImprovement,
  ttftImprovementFrames
);

setImprovement(
  "e2e-improvement",
  "e2e-improvement-detail",
  e2eImprovement,
  e2eImprovementFrames
);

const ttftSeries = seriesMap(framesForRef("C"));
const e2eSeries = seriesMap(framesForRef("D"));

buildBars("ttft-bars", latestBySystem(ttftSeries));
buildBars("e2e-bars", latestBySystem(e2eSeries));

buildChart(
  "ttft-chart",
  "ttft-legend",
  ttftSeries,
  "Mean TTFT (seconds)"
);

buildChart(
  "e2e-chart",
  "e2e-legend",
  e2eSeries,
  "Mean E2E latency (seconds)"
);

const updated = root.getElementById("last-updated");

if (updated) {
  updated.textContent =
    `Updated ${new Date().toLocaleTimeString()}`;
}
