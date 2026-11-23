// Utilidades de interface: seleção, escape de HTML, formatação, avisos.
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const urlSegura = (u) => (/^https:\/\//i.test(u || "") ? u : "#");
export const brl = (v) => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export const dataHora = (v) => (v ? new Date(v).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "–");
export const data = (v) => (v ? new Date(String(v).length === 10 ? v + "T12:00" : v).toLocaleDateString("pt-BR") : "–");
export function numero(t) {
  if (t == null || t === "") return null;
  t = String(t).replace(/R\$|\s/g, "");
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  const n = parseFloat(t);
  return Number.isFinite(n) ? n : null;
}
export function haQuanto(v) {
  if (!v) return "nunca";
  const min = Math.round((Date.now() - new Date(v)) / 60000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  if (min < 1440) return `há ${Math.round(min / 60)} h`;
  return `há ${Math.round(min / 1440)} dia(s)`;
}

let temporizador;
export function aviso(msg, tipo = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast on ${tipo}`;
  clearTimeout(temporizador);
  temporizador = setTimeout(() => (t.className = "toast"), tipo === "erro" ? 5000 : 3000);
}

export function guardar(chave, valor) {
  try { if (valor === undefined) return localStorage.getItem(chave); localStorage.setItem(chave, valor); } catch { return null; }
}

export const ROTULO_STATUS = { encontrada: "Aguardando aprovação", aprovada: "Na fila", publicada: "Publicada", pausada: "Pausada", rejeitada: "Rejeitada", expirada: "Expirada" };
