// Point-of-sale terminal. The server re-prices everything; totals here are for display.
(() => {
  const root = document.getElementById("pos");
  if (!root) return;
  const $ = (sel) => root.querySelector(sel);
  const page = document;
  const cfg = root.dataset;
  const CASH = "cash";
  const fmt = (cents) => `${cents < 0 ? "-" : ""}${cfg.currency}${(Math.abs(cents) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const toCents = (value) => Math.round(parseFloat(value || "0") * 100) || 0;

  const state = {
    products: new Map(),   // id -> product (latest seen)
    cart: [],              // [{id, qty}]
    payments: [],          // split payments already added [{method, amount(cents), reference}]
    method: CASH,
    category: "",
    discountMode: "percent", // which discount box the cashier typed in last; the other follows it
  };

  // ---------------------------------------------------------------- persistence
  try {
    const saved = JSON.parse(localStorage.getItem(cfg.storageKey) || "null");
    if (saved) {
      state.cart = saved.cart || [];
      (saved.products || []).forEach((p) => state.products.set(p.id, p));
    }
  } catch { /* storage unavailable */ }
  const persist = () => {
    try {
      const products = state.cart.map((l) => state.products.get(l.id)).filter(Boolean);
      localStorage.setItem(cfg.storageKey, JSON.stringify({ cart: state.cart, products }));
    } catch { /* ignore */ }
  };

  // ---------------------------------------------------------------- products
  const status = $("[data-status]");
  const grid = $("[data-products]");
  let searchTimer;

  async function loadProducts(q = "", addExact = false) {
    const params = new URLSearchParams({ q, category: state.category });
    status.textContent = "Loading…";
    try {
      const res = await fetch(`${cfg.productsUrl}?${params}`, { headers: { Accept: "application/json" } });
      const data = await res.json();
      data.results.forEach((p) => state.products.set(p.id, p));
      if (data.exact) state.products.set(data.exact.id, data.exact);
      if (addExact && data.exact) {
        addToCart(data.exact.id);
        $("#pos-search").value = "";
        return loadProducts("");
      }
      if (addExact && data.results.length === 1) {
        addToCart(data.results[0].id);
        $("#pos-search").value = "";
        return loadProducts("");
      }
      renderProducts(data.results);
      status.textContent = data.results.length ? `${data.results.length} product${data.results.length === 1 ? "" : "s"}` : "No products found.";
    } catch {
      status.textContent = "Couldn't load products. Check the connection.";
    }
  }

  function renderProducts(list) {
    grid.replaceChildren(...list.map((p) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.disabled = p.stock <= 0;
      btn.className = "card flex h-full w-full flex-col overflow-hidden text-left transition hover:shadow-md hover:ring-brand-500 focus-visible:outline-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-50";
      btn.innerHTML = `
        <span class="flex aspect-[4/3] w-full items-center justify-center overflow-hidden bg-gray-100 text-2xl font-black text-gray-400"></span>
        <span class="flex flex-1 flex-col gap-0.5 p-3">
          <span class="line-clamp-2 text-sm font-medium"></span>
          <span class="mt-auto flex items-center justify-between pt-1">
            <span class="font-semibold"></span>
            <span class="text-xs"></span>
          </span>
        </span>`;
      const image = btn.firstElementChild;
      if (p.image) {
        const img = document.createElement("img");
        img.src = p.image; img.alt = ""; img.loading = "lazy"; img.className = "h-full w-full object-cover";
        image.append(img);
      } else {
        image.textContent = p.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 3).toUpperCase();
      }
      btn.querySelector(".line-clamp-2").textContent = p.name;
      btn.querySelector(".font-semibold").textContent = fmt(toCents(p.price));
      const stockEl = btn.querySelector(".text-xs");
      stockEl.textContent = p.stock > 0 ? `${p.stock} in stock` : "Out of stock";
      stockEl.className = `text-xs ${p.stock <= 0 ? "text-red-600" : p.stock <= 5 ? "text-amber-700" : "text-gray-500"}`;
      btn.setAttribute("aria-label", `Add ${p.name}, ${fmt(toCents(p.price))}, ${p.stock} in stock`);
      btn.addEventListener("click", () => addToCart(p.id));
      li.append(btn);
      return li;
    }));
  }

  $("[data-search]").addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(searchTimer);
    loadProducts($("#pos-search").value.trim(), true);  // Enter / scanner: add exact match
  });
  $("#pos-search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadProducts(event.target.value.trim()), 250);
  });
  root.querySelectorAll("[data-category]").forEach((btn) => btn.addEventListener("click", () => {
    state.category = btn.dataset.category;
    root.querySelectorAll("[data-category]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    loadProducts($("#pos-search").value.trim());
  }));

  // ---------------------------------------------------------------- cart
  function addToCart(id, qty = 1) {
    const product = state.products.get(id);
    if (!product) return;
    const line = state.cart.find((l) => l.id === id);
    const next = (line ? line.qty : 0) + qty;
    if (next > product.stock) return showError(`Only ${product.stock} of ${product.name} in stock.`);
    if (line) line.qty = next; else state.cart.push({ id, qty });
    showError("");
    render();
  }

  function setQty(id, qty) {
    const product = state.products.get(id);
    qty = Math.max(0, Math.min(parseInt(qty, 10) || 0, product ? product.stock : 0));
    state.cart = qty ? state.cart.map((l) => (l.id === id ? { ...l, qty } : l)) : state.cart.filter((l) => l.id !== id);
    render();
  }

  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  const maxPercent100 = () => Math.round(parseFloat(cfg.maxDiscount) * 100);
  // Discount percentage in hundredths (12.5% -> 1250), clamped to the cashier's limit.
  const percent100 = () => clamp(Math.round((parseFloat($("[data-discount]").value) || 0) * 100), 0, maxPercent100());

  // All in cents; mirrors the server's rounding (half up) so the totals shown are the totals charged.
  function totals() {
    const subtotal = state.cart.reduce((sum, l) => sum + toCents(state.products.get(l.id)?.price) * l.qty, 0);
    const discount = state.discountMode === "amount"
      ? clamp(toCents($("[data-discount-value]").value), 0, Math.round((subtotal * maxPercent100()) / 10000))
      : Math.round((subtotal * percent100()) / 10000);
    const maxAdjustment = cfg.maxAdjustment === "" ? Infinity : toCents(cfg.maxAdjustment);
    const adjustment = clamp(toCents($("[data-adjustment]").value), 0, Math.min(subtotal - discount, maxAdjustment));
    return { subtotal, discount, adjustment, maxAdjustment, total: subtotal - discount - adjustment };
  }

  // ---------------------------------------------------------------- payments
  const amountInput = $("[data-amount]");

  function paidSoFar() {
    return state.payments.reduce((sum, p) => sum + p.amount, 0);
  }

  function currentEntry(due) {
    // An empty amount means "the rest, exactly".
    const typed = amountInput.value.trim();
    const amount = typed === "" ? Math.max(due - paidSoFar(), 0) : toCents(typed);
    return { method: state.method, amount, reference: $("[data-reference]").value.trim() };
  }

  function allPayments(due) {
    const entry = currentEntry(due);
    return entry.amount > 0 ? [...state.payments, entry] : [...state.payments];
  }

  function coverage(due, adjustment = 0) {
    const payments = allPayments(due);
    const nonCash = payments.filter((p) => p.method !== CASH).reduce((s, p) => s + p.amount, 0);
    const cash = payments.filter((p) => p.method === CASH).reduce((s, p) => s + p.amount, 0);
    const cashDue = due - nonCash;
    const change = cash - Math.max(cashDue, 0);
    // An adjustment only writes off what the customer can't pay; it is never handed back as change.
    const adjustedChange = adjustment > 0 && cash > cashDue;
    return { payments, ok: nonCash <= due && cash >= cashDue && !adjustedChange, change, short: Math.max(cashDue - cash, 0), overCard: nonCash > due, adjustedChange };
  }

  root.querySelectorAll("[data-method]").forEach((btn) => btn.addEventListener("click", () => {
    state.method = btn.dataset.method;
    root.querySelectorAll("[data-method]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    $("[data-amount-label]").textContent = state.method === CASH ? "Cash received" : "Amount";
    $("[data-reference-wrap]").classList.toggle("hidden", state.method === CASH);
    render();
  }));

  $("[data-add-payment]").addEventListener("click", () => {
    const { total } = totals();
    const entry = currentEntry(total);
    if (entry.amount <= 0) return showError("Enter an amount to split.");
    state.payments.push(entry);
    amountInput.value = ""; $("[data-reference]").value = "";
    render();
  });

  function renderQuick(due) {
    const quick = $("[data-quick]");
    const remaining = Math.max(due - paidSoFar(), 0);
    if (state.method !== CASH || !remaining) return quick.replaceChildren();
    const notes = [100, 200, 500, 1000, 2000, 5000].map((n) => n * 100);
    const options = [remaining, ...notes.map((n) => Math.ceil(remaining / n) * n)]
      .filter((v, i, arr) => v >= remaining && arr.indexOf(v) === i).slice(0, 4);
    quick.replaceChildren(...options.map((cents, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "rounded-md bg-gray-100 px-2.5 py-1 text-xs font-semibold hover:bg-gray-200";
      b.textContent = i === 0 ? `Exact ${fmt(cents)}` : fmt(cents);
      b.addEventListener("click", () => { amountInput.value = (cents / 100).toFixed(2); render(); });
      return b;
    }));
  }

  // ---------------------------------------------------------------- render
  function render() {
    const cartEl = $("[data-cart]");
    const t = totals();
    $("[data-empty]").classList.toggle("hidden", state.cart.length > 0);
    cartEl.replaceChildren(...state.cart.map((l) => {
      const p = state.products.get(l.id);
      const li = document.createElement("li");
      li.className = "flex items-center gap-2 px-4 py-2.5";
      li.innerHTML = `
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium"></p>
          <p class="text-xs text-gray-500"></p>
        </div>
        <div class="flex items-center rounded-lg ring-1 ring-gray-300">
          <button type="button" class="px-2 py-1 text-lg leading-none" data-dec aria-label="Decrease">−</button>
          <input type="number" min="0" class="w-12 border-0 p-1 text-center text-sm focus:ring-0" data-qty aria-label="Quantity">
          <button type="button" class="px-2 py-1 text-lg leading-none" data-inc aria-label="Increase">+</button>
        </div>
        <p class="w-20 text-right text-sm font-semibold tabular-nums"></p>
        <button type="button" class="text-gray-400 hover:text-red-600" data-remove aria-label="Remove">✕</button>`;
      li.querySelector("p.truncate").textContent = p ? p.name : `#${l.id}`;
      li.querySelector("p.text-xs").textContent = p ? `${fmt(toCents(p.price))} each` : "";
      li.querySelector("[data-qty]").value = l.qty;
      li.querySelector("p.w-20").textContent = fmt(toCents(p?.price) * l.qty);
      li.querySelector("[data-dec]").addEventListener("click", () => setQty(l.id, l.qty - 1));
      li.querySelector("[data-inc]").addEventListener("click", () => addToCart(l.id));
      li.querySelector("[data-qty]").addEventListener("change", (e) => setQty(l.id, e.target.value));
      li.querySelector("[data-remove]").addEventListener("click", () => setQty(l.id, 0));
      return li;
    }));

    $("[data-subtotal]").textContent = fmt(t.subtotal);
    $("[data-discount-amount]").textContent = t.discount ? `−${fmt(t.discount)}` : fmt(0);
    $("[data-adjustment-row]").hidden = !t.adjustment;
    $("[data-adjustment-amount]").textContent = `−${fmt(t.adjustment)}`;
    $("[data-total]").textContent = fmt(t.total);
    // Keep the other discount box in step with the one being typed in.
    if (state.discountMode === "amount") {
      $("[data-discount]").value = t.subtotal ? +((t.discount * 100) / t.subtotal).toFixed(2) : 0;
    } else {
      $("[data-discount-value]").value = (t.discount / 100).toFixed(2);
    }

    $("[data-payments]").replaceChildren(...state.payments.map((p, i) => {
      const li = document.createElement("li");
      li.className = "flex items-center justify-between rounded-md bg-gray-50 px-2 py-1 text-xs";
      li.innerHTML = `<span></span><button type="button" class="text-gray-400 hover:text-red-600" aria-label="Remove payment">✕</button>`;
      li.firstElementChild.textContent = `${p.method.toUpperCase()} ${fmt(p.amount)}${p.reference ? ` · ${p.reference}` : ""}`;
      li.lastElementChild.addEventListener("click", () => { state.payments.splice(i, 1); render(); });
      return li;
    }));

    const cover = coverage(t.total, t.adjustment);
    const changeEl = $("[data-change]");
    changeEl.classList.toggle("hidden", !(cover.ok && cover.change > 0));
    changeEl.textContent = `Change due: ${fmt(cover.change)}`;
    renderQuick(t.total);
    // Customer is a little short: offer to write the shortfall off as an adjustment.
    const writeOff = $("[data-write-off]");
    const canWriteOff = cover.short > 0 && !cover.overCard && t.adjustment + cover.short <= t.maxAdjustment;
    writeOff.hidden = !canWriteOff;
    writeOff.textContent = `Write off ${fmt(cover.short)} as adjustment`;
    writeOff.dataset.amount = t.adjustment + cover.short;

    const charge = $("[data-charge]");
    charge.disabled = !state.cart.length || !cover.ok;
    charge.textContent = !state.cart.length ? "Charge"
      : cover.overCard ? "Card / mobile exceeds total"
      : cover.adjustedChange ? "No change with an adjustment"
      : cover.short ? `Short by ${fmt(cover.short)}`
      : `Charge ${fmt(t.total)}`;
    persist();
  }

  function showError(message) {
    const el = $("[data-error]");
    el.textContent = message;
    el.classList.toggle("hidden", !message);
  }

  $("[data-discount]").addEventListener("input", () => { state.discountMode = "percent"; render(); });
  $("[data-discount-value]").addEventListener("input", () => { state.discountMode = "amount"; render(); });
  $("[data-adjustment]").addEventListener("input", render);
  $("[data-write-off]").addEventListener("click", (event) => {
    $("[data-adjustment]").value = (event.currentTarget.dataset.amount / 100).toFixed(2);
    render();
  });
  amountInput.addEventListener("input", render);
  $("[data-clear]").addEventListener("click", () => { resetSale(); });

  function resetSale() {
    state.cart = []; state.payments = [];
    amountInput.value = ""; $("[data-reference]").value = "";
    $("[data-discount]").value = "0"; $("[data-discount-value]").value = "0"; $("[data-adjustment]").value = "0";
    state.discountMode = "percent";
    $("[data-customer-name]").value = ""; $("[data-customer-phone]").value = "";
    showError("");
    render();
  }

  // ---------------------------------------------------------------- checkout
  $("[data-charge]").addEventListener("click", async () => {
    const t = totals();
    const cover = coverage(t.total, t.adjustment);
    if (!cover.ok || !state.cart.length) return;
    const charge = $("[data-charge]");
    charge.disabled = true; charge.textContent = "Processing…";
    try {
      const res = await fetch(cfg.checkoutUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrf },
        body: JSON.stringify({
          lines: state.cart,
          payments: cover.payments.map((p) => ({ method: p.method, amount: (p.amount / 100).toFixed(2), reference: p.reference })),
          ...(state.discountMode === "amount"
            ? { discount_amount: (t.discount / 100).toFixed(2) }
            : { discount_percent: (percent100() / 100).toFixed(2) }),
          adjustment: (t.adjustment / 100).toFixed(2),
          customer_name: $("[data-customer-name]").value,
          customer_phone: $("[data-customer-phone]").value,
        }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "The sale could not be completed.");
      const dialog = page.querySelector("[data-done]");
      dialog.querySelector("[data-done-number]").textContent = data.sale;
      dialog.querySelector("[data-done-total]").textContent = fmt(toCents(data.total));
      const change = toCents(data.change);
      dialog.querySelector("[data-done-change]").textContent = change ? `Change: ${fmt(change)}` : "No change due";
      dialog.querySelector("[data-done-receipt]").href = `${data.receipt_url}?print=1`;
      resetSale();
      loadProducts($("#pos-search").value.trim());  // refresh stock
      dialog.showModal();
    } catch (err) {
      showError(err.message);
      render();
    }
  });

  page.querySelector("[data-new-sale]")?.addEventListener("click", () => {
    page.querySelector("[data-done]").close();
    $("#pos-search").focus();
  });

  render();
  loadProducts();
})();
