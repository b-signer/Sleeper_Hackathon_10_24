// Small helpers shared by the page. Everything user-visible goes through textContent (no innerHTML).

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

export const fmtInt = (n) => (n == null ? "—" : Math.round(Number(n)).toLocaleString("en-US"));

export function fmtNum(n) {
  if (n == null) return "—";
  return Number.isInteger(n) ? fmtInt(n) : Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function fmtUsd(n) {
  if (n == null) return "—";
  return "$" + (n > 0 && n < 0.1 ? n.toFixed(4) : n.toFixed(2));
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const escapePart = (k) => String(k).replace(/~/g, "~0").replace(/\//g, "~1");

// RFC 6901 lookup; undefined if the pointer doesn't resolve.
export function getPointer(doc, pointer) {
  if (!pointer) return doc;
  let cur = doc;
  for (const raw of pointer.split("/").slice(1)) {
    const part = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (cur == null || typeof cur !== "object" || !(part in cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

// Pretty JSON as DOM lines. The line at `highlight` (a JSON Pointer) is marked and returned as `target`.
export function renderJson(value, highlight = null) {
  const pre = h("pre", { class: "json" });
  let target = null;
  const mark = (line, path) => {
    if (highlight != null && path === highlight) {
      line.classList.add("hl");
      target = line;
    }
  };
  const walk = (v, path, indent, key, last) => {
    const pad = "  ".repeat(indent);
    const comma = last ? "" : ",";
    const line = h("div", { class: "jl" }, pad, key != null && [h("span", { class: "jk" }, JSON.stringify(key)), ": "]);
    if (v !== null && typeof v === "object") {
      const isArr = Array.isArray(v);
      const entries = isArr ? v.map((x, i) => [i, x]) : Object.entries(v);
      if (!entries.length) {
        line.append((isArr ? "[]" : "{}") + comma);
        pre.append(line);
        mark(line, path);
        return;
      }
      line.append(isArr ? "[" : "{");
      pre.append(line);
      mark(line, path);
      entries.forEach(([k, x], i) => walk(x, `${path}/${escapePart(k)}`, indent + 1, isArr ? null : k, i === entries.length - 1));
      pre.append(h("div", { class: "jl" }, pad + (isArr ? "]" : "}") + comma));
    } else {
      line.append(h("span", { class: `jv ${v === null ? "null" : typeof v}` }, JSON.stringify(v)), comma);
      pre.append(line);
      mark(line, path);
    }
  };
  walk(value, "", 0, null, true);
  return { el: pre, target };
}
