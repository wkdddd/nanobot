const TRAILING_PUNCTUATION_RE = /[,;\uFF0C\uFF1B]+$/;
const LEADING_LIST_MARKER_RE = /^[-*+]\s+/;
const WRAPPER_CHARS = new Set(["`", "'", '"']);

function stripOuterWrapper(value: string): string {
  if (value.length < 2) return value;
  const first = value[0];
  const last = value[value.length - 1];
  if (first === last && WRAPPER_CHARS.has(first)) {
    return value.slice(1, -1).trim();
  }
  return value;
}

function stripTrailingSlashes(value: string): string {
  if (value === "/" || /^[A-Za-z]:\/$/.test(value)) return value;
  return value.replace(/\/+$/g, "");
}

function cleanTargetPathPart(value: string): string {
  let cleaned = value.trim();
  cleaned = cleaned.replace(LEADING_LIST_MARKER_RE, "").trim();
  cleaned = cleaned.replace(TRAILING_PUNCTUATION_RE, "").trim();
  cleaned = stripOuterWrapper(cleaned);
  cleaned = cleaned.replace(TRAILING_PUNCTUATION_RE, "").trim();
  cleaned = stripTrailingSlashes(cleaned.replace(/\\/g, "/"));
  return cleaned;
}

export function parseTargetPathsText(value: string): string[] {
  const seen = new Set<string>();
  const paths: string[] = [];

  for (const part of value.split(/\r?\n|,/)) {
    const cleaned = cleanTargetPathPart(part);
    if (!cleaned || seen.has(cleaned)) continue;
    seen.add(cleaned);
    paths.push(cleaned);
  }

  return paths;
}
