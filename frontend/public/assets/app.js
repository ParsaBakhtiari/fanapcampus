"use strict";

const state = { token: localStorage.getItem("token"), user: null, cart: { items: [], total: "0", currency: "" }, registerMode: false };
const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

const money = (v, cur = state.cart.currency || "USD") => `${Number(v).toFixed(2)} ${cur}`;

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401 && state.token) { logout(); throw new Error("Session expired, please sign in again"); }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const body = await res.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.blob();
}

/* ------------------------------------------------------------------ auth */
function applyAuthUI() {
  const loggedIn = Boolean(state.user);
  document.querySelectorAll(".auth-only").forEach((n) => (n.hidden = !loggedIn));
  document.querySelectorAll(".guest-only").forEach((n) => (n.hidden = loggedIn));
  document.querySelectorAll(".admin-only").forEach((n) => (n.hidden = !(state.user && state.user.role === "admin")));
  $("#user-email").textContent = state.user ? state.user.email : "";
}

async function loadMe() {
  if (!state.token) { state.user = null; return applyAuthUI(); }
  try { state.user = await api("/api/v1/auth/me"); } catch (_) { state.user = null; state.token = null; localStorage.removeItem("token"); }
  applyAuthUI();
}

function logout() {
  state.token = null; state.user = null; localStorage.removeItem("token");
  applyAuthUI(); location.hash = "#/shop";
}

function openAuth(register = false) {
  state.registerMode = register;
  $("#auth-title").textContent = register ? "Create account" : "Sign in";
  $("#auth-submit").textContent = register ? "Create account" : "Sign in";
  $("#auth-toggle").textContent = register ? "I already have an account" : "Create an account";
  document.querySelector(".register-only").hidden = !register;
  $("#auth-dialog").showModal();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = { email: f.get("email"), password: f.get("password") };
  try {
    if (state.registerMode) await api("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ ...body, full_name: f.get("full_name") || "" }) });
    const tok = await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify(body) });
    state.token = tok.access_token; localStorage.setItem("token", state.token);
    $("#auth-dialog").close(); e.target.reset();
    await loadMe(); await refreshCart(); toast("Welcome!");
    route();
  } catch (err) { toast(err.message, true); }
});
$("#auth-toggle").addEventListener("click", (e) => { e.preventDefault(); openAuth(!state.registerMode); });
$("#auth-cancel").addEventListener("click", () => $("#auth-dialog").close());
$("#login-btn").addEventListener("click", () => openAuth(false));
$("#logout-btn").addEventListener("click", logout);

/* ------------------------------------------------------------------ shop */
async function renderShop() {
  const box = $("#products");
  box.replaceChildren(el("p", { class: "muted" }, "Loading products…"));
  try {
    const products = await api("/api/v1/products");
    if (!products.length) return box.replaceChildren(el("p", { class: "muted" }, "No products yet. An admin can add them in the Admin tab."));
    box.replaceChildren(...products.map((p) =>
      el("article", { class: "card product" },
        p.has_image
          ? el("img", { src: `/api/v1/products/${p.id}/image`, alt: p.name, loading: "lazy" })
          : el("div", { class: "placeholder" }, p.name.slice(0, 1)),
        el("h3", {}, p.name),
        el("p", { class: "muted" }, p.description),
        el("div", { class: "row between" },
          el("strong", {}, money(p.price)),
          el("span", { class: p.stock > 0 ? "muted" : "danger" }, p.stock > 0 ? `${p.stock} in stock` : "Sold out")),
        el("button", { class: "primary", type: "button", disabled: p.stock < 1 ? "" : false, onclick: () => addToCart(p) }, "Add to cart"))));
  } catch (err) { box.replaceChildren(el("p", { class: "danger" }, `Could not load products: ${err.message}`)); }
}

async function addToCart(product) {
  if (!state.user) return openAuth(false);
  const current = state.cart.items.find((i) => i.product_id === product.id);
  try {
    state.cart = await api("/api/v1/cart", { method: "PUT", body: JSON.stringify({ product_id: product.id, quantity: (current ? current.quantity : 0) + 1 }) });
    renderCart(); toast(`${product.name} added to cart`);
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------------------------ cart */
async function refreshCart() {
  if (!state.user) return;
  try { state.cart = await api("/api/v1/cart"); renderCart(); } catch (err) { toast(err.message, true); }
}

function renderCart() {
  $("#cart-count").textContent = state.cart.items.reduce((n, i) => n + i.quantity, 0);
  $("#cart-total").textContent = money(state.cart.total);
  const box = $("#cart-items");
  if (!state.cart.items.length) return box.replaceChildren(el("p", { class: "muted" }, "Your cart is empty."));
  box.replaceChildren(...state.cart.items.map((i) =>
    el("div", { class: "cart-line" },
      el("div", {}, el("strong", {}, i.name), el("div", { class: i.available ? "muted" : "danger" }, `${i.quantity} × ${money(i.unit_price)}${i.available ? "" : " — not enough stock"}`)),
      el("div", { class: "row" },
        el("span", {}, money(i.line_total)),
        el("button", { class: "ghost small", type: "button", onclick: async () => {
          try { state.cart = await api(`/api/v1/cart/items/${i.product_id}`, { method: "DELETE" }); renderCart(); } catch (err) { toast(err.message, true); }
        } }, "Remove")))));
}

$("#cart-btn").addEventListener("click", async () => { await refreshCart(); $("#cart-panel").hidden = false; });
$("#cart-close").addEventListener("click", () => ($("#cart-panel").hidden = true));

$("#checkout-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    const order = await api("/api/v1/checkout", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ shipping_address: new FormData(e.target).get("address") }),
    });
    e.target.reset(); $("#cart-panel").hidden = true;
    await refreshCart();
    toast(`Order ${order.number} placed`);
    location.hash = "#/orders";
  } catch (err) { toast(err.message, true); }
  finally { button.disabled = false; }
});

/* ------------------------------------------------------------------ orders */
async function downloadInvoice(order) {
  try {
    const blob = await api(`/api/v1/orders/${order.id}/invoice`);
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `invoice-${order.number}.pdf` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) { toast(err.message, true); }
}

async function renderOrders() {
  const box = $("#orders");
  if (!state.user) return box.replaceChildren(el("p", { class: "muted" }, "Sign in to see your orders."));
  box.replaceChildren(el("p", { class: "muted" }, "Loading orders…"));
  try {
    const orders = await api("/api/v1/orders");
    if (!orders.length) return box.replaceChildren(el("p", { class: "muted" }, "No orders yet."));
    box.replaceChildren(...orders.map((o) =>
      el("article", { class: "card order" },
        el("div", { class: "row between" },
          el("strong", {}, o.number),
          el("span", { class: `status ${o.status}` }, o.status)),
        el("div", { class: "muted" }, new Date(o.created_at).toLocaleString()),
        el("ul", {}, o.items.map((i) => el("li", {}, `${i.quantity} × ${i.name} — ${money(i.unit_price, o.currency)}`))),
        el("div", { class: "row between" },
          el("strong", {}, money(o.total, o.currency)),
          el("div", { class: "row" },
            el("button", { class: "ghost small", type: "button", onclick: () => downloadInvoice(o) }, "Invoice PDF"),
            o.status === "confirmed"
              ? el("button", { class: "ghost small danger", type: "button", onclick: async () => {
                  try { await api(`/api/v1/orders/${o.id}/cancel`, { method: "POST" }); toast("Order cancelled"); renderOrders(); } catch (err) { toast(err.message, true); }
                } }, "Cancel")
              : "")))));
  } catch (err) { box.replaceChildren(el("p", { class: "danger" }, err.message)); }
}

/* ------------------------------------------------------------------ admin */
async function renderAdmin() {
  if (!state.user || state.user.role !== "admin") { location.hash = "#/shop"; return; }
  try {
    const products = await api("/api/v1/admin/products");
    $("#admin-products").replaceChildren(
      el("tr", {}, el("th", {}, "SKU"), el("th", {}, "Name"), el("th", {}, "Price"), el("th", {}, "Stock"), el("th", {}, "Image")),
      ...products.map((p) => {
        const stockInput = el("input", { type: "number", min: "0", value: p.stock, class: "narrow" });
        const fileInput = el("input", { type: "file", accept: "image/jpeg,image/png,image/webp", class: "file" });
        fileInput.addEventListener("change", async () => {
          const data = new FormData(); data.append("file", fileInput.files[0]);
          try { await api(`/api/v1/admin/products/${p.id}/image`, { method: "POST", body: data }); toast("Image uploaded to OBS"); renderAdmin(); } catch (err) { toast(err.message, true); }
        });
        stockInput.addEventListener("change", async () => {
          try { await api(`/api/v1/admin/products/${p.id}`, { method: "PATCH", body: JSON.stringify({ stock: Number(stockInput.value) }) }); toast("Stock updated"); } catch (err) { toast(err.message, true); }
        });
        return el("tr", {}, el("td", {}, p.sku), el("td", {}, p.name), el("td", {}, Number(p.price).toFixed(2)), el("td", {}, stockInput),
          el("td", {}, p.has_image ? "✓ " : "", fileInput));
      }));
    const orders = await api("/api/v1/admin/orders");
    $("#admin-orders").replaceChildren(
      el("tr", {}, el("th", {}, "Number"), el("th", {}, "Customer"), el("th", {}, "Total"), el("th", {}, "Status"), el("th", {}, "Date")),
      ...orders.map((o) => el("tr", {}, el("td", {}, o.number), el("td", {}, o.user_email), el("td", {}, money(o.total, o.currency)),
        el("td", {}, el("span", { class: `status ${o.status}` }, o.status)), el("td", {}, new Date(o.created_at).toLocaleString()))));
  } catch (err) { toast(err.message, true); }
}

$("#product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/v1/admin/products", { method: "POST", body: JSON.stringify({
      sku: f.get("sku"), name: f.get("name"), description: f.get("description") || "",
      price: Number(f.get("price")).toFixed(2), stock: Number(f.get("stock")) }) });
    e.target.reset(); toast("Product created"); renderAdmin();
  } catch (err) { toast(err.message, true); }
});

/* ------------------------------------------------------------------ router */
function route() {
  const view = (location.hash.replace("#/", "") || "shop").split("/")[0];
  const known = ["shop", "orders", "admin"].includes(view) ? view : "shop";
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${known}`));
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === known));
  ({ shop: renderShop, orders: renderOrders, admin: renderAdmin })[known]();
}

window.addEventListener("hashchange", route);
(async () => { await loadMe(); await refreshCart(); route(); })();
