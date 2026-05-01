const NUM = String.raw`\d+\s+\d+\/\d+|\d+\/\d+|\d+(?:\.\d+)?`;
const afterLabel = new RegExp(
  String.raw`(?<num>${NUM})\s*(?:["“”″']|\bin(?:ches)?\b|\s)*\s*(?<label>[WwHhDdLl])(?![a-zA-Z])`,
  "g",
);
const beforeLabel = new RegExp(
  String.raw`\b(?<label>width|wide|w|height|high|h|depth|deep|d|length|long|l)\b\s*(?:[:=]|is|of)?\s*(?<num>${NUM})`,
  "gi",
);

const labelMap: Record<string, "width" | "height" | "depth" | "length"> = {
  w: "width",
  width: "width",
  wide: "width",
  h: "height",
  height: "height",
  high: "height",
  d: "depth",
  depth: "depth",
  deep: "depth",
  l: "length",
  length: "length",
  long: "length",
};

export function extractLabeledDimensions(value: unknown) {
  const result = { width: "", height: "", depth: "", length: "" };
  const text = String(value ?? "").trim();
  if (!text) return result;

  for (const match of text.matchAll(afterLabel)) {
    const key = labelMap[String(match.groups?.label ?? "").toLowerCase()];
    if (key && !result[key]) result[key] = String(match.groups?.num ?? "").trim();
  }

  for (const match of text.matchAll(beforeLabel)) {
    const key = labelMap[String(match.groups?.label ?? "").toLowerCase()];
    if (key && !result[key]) result[key] = String(match.groups?.num ?? "").trim();
  }

  return result;
}

export function hasComplete3dDimensions(value: unknown) {
  const parts = extractLabeledDimensions(value);
  return Boolean(parts.width && parts.height && parts.depth);
}
