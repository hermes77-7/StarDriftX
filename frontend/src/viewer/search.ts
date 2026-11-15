export type Coordinates = { ra: number; dec: number };
type Search = { position: Coordinates } | { name: string } | { invalid: true };

const decimal = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:,|\s)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$/;
const sexagesimal = /^(\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?\s*[, ]?\s*([+-]?)(\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?$/;

export function parseSkySearch(raw: string): Search {
  const text = raw.trim();
  const coordinates = decimal.exec(text);
  if (coordinates) {
    const [, x, y] = coordinates;
    const ra = Number(x), dec = Number(y);
    return ra >= 0 && ra <= 360 && Math.abs(dec) <= 90
      ? { position: { ra: ra % 360, dec } }
      : { invalid: true };
  }
  const standard = text
    .replace(/(\d)\s*[hd°m′']/gi, "$1:")
    .replace(/(\d)\s*[s″"]/gi, "$1")
    .replace(/:\s+/g, ":")
    .replace(/^(\d{1,2})\s+(\d{1,2})\s+(\d{1,2}(?:\.\d+)?)\s*[,\s]+\s*([+-]?\d{1,2})\s+(\d{1,2})\s+(\d{1,2}(?:\.\d+)?)$/, "$1:$2:$3 $4:$5:$6");
  const parts = sexagesimal.exec(standard);
  if (parts) {
    const [, h, m, s = "0", sign, d, dm, ds = "0"] = parts;
    if ([m, s, dm, ds].some((value) => Number(value) >= 60)) return { invalid: true };
    const hours = Number(h) + Number(m) / 60 + Number(s) / 3600;
    const degrees = Number(d) + Number(dm) / 60 + Number(ds) / 3600;
    if (hours > 24 || degrees > 90) return { invalid: true };
    return { position: { ra: (hours * 15) % 360, dec: sign === "-" ? -degrees : degrees } };
  }
  return text.length <= 120 && /\p{L}/u.test(text) &&
    (!/^[+\-.\d]/.test(text) || !/[:°,]/.test(text))
    ? { name: text.replace(/\s+/g, " ") } : { invalid: true };
}
