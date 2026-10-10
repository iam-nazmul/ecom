# Ecom — Product Specification

A small but production-shaped e-commerce storefront built with Django, server-rendered
Jinja2 templates styled with Tailwind CSS, backed by PostgreSQL, and served by
Gunicorn behind Nginx via Docker Compose.

## 1. Goals

- Customers can browse a catalog, search/filter products, manage a cart, and place an order.
- Staff manage categories, products, stock and orders through the Django admin.
- Staff buy stock from suppliers (purchase orders), keep an auditable stock ledger, record
  expenses and see profit & loss.
- One command (`docker compose up --build`) brings up a working stack.
- The codebase stays simple: no SPA, no JS framework, no payment gateway.

### Non-goals (v1)

- Online card payments (orders use **Cash on Delivery**; status is updated by staff).
- Product variants (size/colour), coupons, reviews, wishlists, multi-currency, i18n.
- Email delivery (the console email backend is fine).

## 2. Tech stack

| Concern        | Choice                                                                 |
|----------------|------------------------------------------------------------------------|
| Language       | Python 3.13                                                            |
| Framework      | Django 5.2 LTS                                                         |
| Templates      | Jinja2 (`django.template.backends.jinja2.Jinja2`) for all storefront pages; Django templates only for the built-in admin |
| CSS            | Tailwind CSS v4, compiled by `@tailwindcss/cli` in a Node build stage   |
| Database       | PostgreSQL 17 (driver: psycopg 3)                                      |
| App server     | Gunicorn, **4 sync workers** (`GUNICORN_WORKERS`, default 4)           |
| Reverse proxy  | Nginx — serves `/static/` and `/media/`, proxies everything else       |
| Orchestration  | Docker Compose: `db`, `web`, `nginx`                                    |

## 3. Domain model

### catalog
- **Category** — `name`, `slug` (unique), `description`, `is_active`.
- **Product** — `category` (FK, PROTECT), `name`, `slug` (unique), `summary` (≤300 chars,
  shown next to the price), `description` (long text; blank lines separate paragraphs),
  `price` (Decimal 10,2), `compare_at_price` (optional, shows a "sale" badge when > price),
  `stock` (positive int), `image` (optional **cover image**), `is_active`, `is_featured`,
  `created_at`, `updated_at`.
- **ProductImage** — `product` (FK, CASCADE, `related_name="images"`), `image`, `alt_text`
  (optional; defaults to the product name), `sort_order`. Ordered by `sort_order`, then id.
  Deleting a row deletes its file.
- **ProductSpec** — `product` (FK, CASCADE, `related_name="specs"`), `group` (optional
  heading, e.g. "Battery"), `name`, `value`, `sort_order`. Ordered by `sort_order`, then id;
  groups appear in the order of their first row.
- **Gallery** = cover image (if any) followed by the product images in order. Product cards
  use the first gallery image; with no images at all a generated placeholder is shown.

### cart
- Session-based (no table). Stored as `{product_id: quantity}` under `session["cart"]`.
- Quantities are clamped to `1..stock`; inactive/deleted products are dropped silently.

### orders
- **Order** — `reference` (unique, 10-char uppercase code), `user` (FK, nullable — guest
  checkout allowed), contact + shipping fields (`full_name`, `email`, `phone`,
  `address_line1`, `address_line2`, `city`, `postal_code`, `country`), `status`
  (`pending` → `processing` → `shipped` → `delivered`, or `cancelled`), `payment_method`
  (`cod`), `subtotal`, `shipping_cost`, `total`, `notes`, `created_at`, `updated_at`.
- **OrderItem** — `order` (FK), `product` (FK, SET_NULL), snapshot of `product_name` and
  `unit_price`, `quantity`.

### reviews
- **Review** — `product` (FK, CASCADE, `related_name="reviews"`), `user` (FK, CASCADE),
  `rating` (1–5, DB check constraint), `title` (optional, ≤120), `body` (10–2000 chars),
  `verified_purchase` — **the reviewer bought the product and received it**: they have an
  order (linked to their account) containing the product whose status is **Delivered**.
  Computed when the review is saved and re-computed whenever one of the user's orders is
  saved or deleted (signals in `apps/reviews/signals.py`), so marking an order Delivered adds
  the badge to existing reviews and cancelling/deleting it removes it. Guest orders and
  pending/processing/shipped orders don't count. Read-only in the admin.
  `is_approved` ("published", default true; staff can hide), `created_at`, `updated_at`.
  Unique per (`product`, `user`): one review per customer per product; re-submitting edits it.
- Only **published** reviews count towards ratings. `Product.objects.for_display()` annotates
  `rating_avg` / `rating_count`.

## 4. Inventory, purchasing & accounting (`apps/inventory`)

### Models
- **Supplier** — `name` (unique), contact, phone, email, address, notes,
  `payment_terms_days` (default 30; 0 = pay on delivery), `is_active`.
- **Purchase** (purchase order, reference `PO-00001`) — `supplier`, `status`
  (`draft` → `ordered` → `received`, or `cancelled`), `order_date`, `expected_date`,
  `received_at`, `invoice_number`, `other_costs` (shipping/customs), notes, and
  `amount_paid` — a read-only cache of the payments allocated to it.
  Derived: items total, total (= items + other costs), balance due, payment status
  (Unpaid / Partly paid / Paid), `due_date` (received date + supplier terms) and overdue
  (balance > 0 after the due date). Items and costs lock once received or cancelled.
- **PurchaseItem** — `product`, `quantity` (≥1), `unit_cost` (supplier price).
- **StockMovement** — append-only ledger: `product`, `kind` (`opening`, `purchase`, `sale`,
  `return`, `adjustment`), signed `quantity`, `unit_cost`, `balance_after`, links to the
  purchase/order, reference, note, user, `created_at`. Σ quantity = Product.stock.
- **StockAdjustment** — manual correction: `product`, signed `quantity`, `reason` (count
  correction, damaged/expired, lost/stolen, opening stock, other), optional `unit_cost`
  (for added stock), note, user. Immutable once saved; cannot remove more than in stock.
- **SupplierPayment** — `supplier`, `date` (not in the future), `amount`, `method` (cash,
  bank transfer, bKash, Nagad, Rocket, cheque, card, other), `reference` (transaction /
  cheque no.), note, user. **PaymentAllocation** — the part of a payment applied to one
  purchase (unique per payment + purchase). The unallocated part of a payment is supplier
  credit.
- **Expense** — `date`, `category` (rent, salaries, utilities, marketing, delivery, fees,
  packaging, other), description, `amount`, paid to.
- **Income** ("other income") — money earned outside product sales: `date`, `category`
  (services, commission & affiliate, supplier rebates, interest, sale of scrap/old equipment,
  rental, other), description, `amount`, received from.
- Added to **Product**: `average_cost` (weighted average, read-only), `reorder_level`
  (default 5). `stock` is read-only in the admin — it changes only via the ledger.
- Added to **Order**: `delivered_at` (set when status becomes Delivered, cleared otherwise).
  Cancelled orders can't be reopened. **OrderItem**: `unit_cost` (cost snapshot at sale;
  null for pre-inventory orders).

### Rules
- **Every stock change** goes through `record_movement()` on a row-locked product, writing
  one ledger row and updating stock + average cost in the same transaction.
- **Weighted-average cost**: incoming stock (purchases, returns, positive adjustments)
  re-averages: `(stock × avg + qty × cost) / (stock + qty)`. Outgoing stock is valued at the
  current average and doesn't change it.
- **Landed cost**: a purchase's `other_costs` are spread across its items in proportion to
  line value and added to their unit cost before averaging.
- **Receiving** (admin action, or "Receive" on the dashboard): only draft/ordered purchases
  with items; adds all items to stock at landed cost; status → received. Not reversible —
  correct mistakes with a stock adjustment.
- **Checkout** records `unit_cost` = current average on each order item and a `sale` movement.
- **Cancelling an order** returns its items to stock (`return` movement at the sale cost), once.
- **Migration**: existing stock got an `opening` row at cost 0; existing delivered orders got
  `delivered_at` = last update. Pre-inventory sales have no cost and are flagged in the P&L.

### Supplier payments (accounts payable, `apps/inventory/payments.py`)
- **Supplier balance** = Σ totals of *received* purchases − Σ payments. Positive = owed to the
  supplier; negative = supplier credit (advance / overpayment). Open purchase orders are
  commitments, not debt.
- **Recording a payment** allocates it (in one transaction, rows locked):
  - *Oldest unpaid first* (default): received purchases by receive date; any excess stays
    as credit (open POs are never prepaid automatically);
  - *a specific purchase* (an open PO = advance payment): that one first, excess oldest-first;
  - *keep as credit*: nothing allocated.
- **Apply credit** allocates unallocated payment amounts (oldest payment first) to unpaid
  received purchases (oldest first).
- **Cancelling a purchase** releases its allocations — the money becomes credit.
- **Deleting a payment** (admin) removes its allocations and recomputes `amount_paid`.
  Payment amount/supplier/date/method are fixed after saving; reference and note are editable.
- Payments are cash movements, not P&L items (stock cost reaches the P&L as COGS when sold).
- Migration: each purchase's old `amount_paid` became a payment (method "other").

### Profit & loss
- Revenue is recognised on **delivery** (cash on delivery): orders with status Delivered and
  `delivered_at` in the period.
- Income = product sales + shipping charged. COGS = Σ `unit_cost` × qty of those orders.
- Gross profit = sales revenue − COGS. Operating profit = gross profit − operating expenses
  (by `Expense.date`) ± stock adjustments at cost (write-offs negative, count gains positive).
  **Net profit = operating profit + other income** (by `Income.date`).
- Total income = product sales + shipping charged + other income. Margins are measured against
  sales revenue (sales + shipping) only.
- Purchases are **not** expenses (stock is an asset until sold); shown for information.
- Report also shows margins, delivered orders, average order value, expenses by category,
  other income by category, monthly breakdown (incl. other income) with bars, top 10 products by profit, open (undelivered) order value,
  and a warning count of sold items with unknown cost.

## 5. Pricing rules

- Subtotal = Σ unit price × quantity (Decimal, never float).
- Shipping = `SHIPPING_FLAT_RATE` (default 5.00) unless subtotal ≥ `FREE_SHIPPING_THRESHOLD`
  (default 5000.00), then 0. Empty cart → 0.
- Currency symbol comes from `CURRENCY_SYMBOL` (default `৳`, Bangladeshi taka).

## 6. Pages & routes

| Route                         | Name                     | Description |
|-------------------------------|--------------------------|-------------|
| `/`                           | `core:home`              | Hero, featured products, newest products, categories |
| `/products/`                  | `catalog:product_list`   | Grid with `?q=` search (name/summary/description), `?category=<slug>`, `?sort=newest|price_asc|price_desc|rating|name` (`rating` = top rated, unrated last), 12 per page |
| `/products/<slug>/`           | `catalog:product_detail` | Image gallery, summary, stock status, add-to-cart with quantity, Description & Specifications section, related products |
| `/category/<slug>/`           | `catalog:category`       | Product list pre-filtered by category |
| `/cart/`                      | `cart:detail`            | Line items, update quantity, remove, totals |
| `/cart/add/<id>/` (POST)      | `cart:add`               | Add product (quantity, default 1) |
| `/cart/update/<id>/` (POST)   | `cart:update`            | Set quantity (0 removes) |
| `/cart/remove/<id>/` (POST)   | `cart:remove`            | Remove line |
| `/checkout/`                  | `orders:checkout`        | Shipping form + summary; places order |
| `/products/<slug>/reviews/` (POST) | `reviews:submit`   | Create or update the logged-in user's review (anonymous → login, then back to `#reviews`) |
| `/products/<slug>/reviews/delete/` (POST) | `reviews:delete` | Delete the logged-in user's own review |
| `/orders/<reference>/`        | `orders:detail`          | Confirmation / order detail (owner or the session that placed it) |
| `/accounts/signup/`           | `accounts:signup`        | Register (username, email, password) and log in |
| `/accounts/login/`            | `accounts:login`         | Log in |
| `/accounts/logout/` (POST)    | `accounts:logout`        | Log out |
| `/accounts/orders/`           | `accounts:orders`        | Logged-in user's order history |
| `/staff/inventory/`           | `inventory:dashboard`    | Staff: KPIs (units, stock value at cost/retail, potential profit, owed to suppliers), low stock, open POs with Receive, stock by product, recent movements |
| `/staff/inventory/products/<id>/` | `inventory:ledger`   | Staff: one product's stock ledger with running balance |
| `/staff/inventory/purchases/<id>/receive/` (POST) | `inventory:receive` | Staff: receive an open purchase into stock |
| `/staff/suppliers/`           | `inventory:suppliers`    | Staff: totals (owed, overdue, credit, open POs) and each supplier's terms, purchased, paid, balance, overdue, last payment |
| `/staff/suppliers/<id>/`      | `inventory:supplier`     | Staff: supplier account — KPIs, purchases with paid/due/due date/overdue, **Record payment** form (POST), payments with allocations, account statement with running balance |
| `/staff/suppliers/<id>/apply-credit/` (POST) | `inventory:apply_credit` | Staff: apply unallocated payments to unpaid purchases |
| `/staff/reports/profit-loss/` | `inventory:profit_loss`  | **Superusers only** (staff get 403; tab hidden): P&L for `?range=this_month|last_month|last_30|last_90|this_year` (default this year) or `?start=YYYY-MM-DD&end=YYYY-MM-DD` |
| `/admin/`                     | —                        | Django admin |
| `/healthz`                    | `core:healthz`           | Returns `ok` (used by container healthcheck) |

## 7. Checkout behaviour

1. Cart must be non-empty, otherwise redirect to cart with a message.
2. Form is prefilled from the logged-in user (name, email).
3. On submit, inside one DB transaction: lock the products (`select_for_update`), verify
   stock for every line, decrement stock, create `Order` + `OrderItem`s with price snapshots.
4. If any line is short on stock, nothing is written; the user is sent back to the cart
   with a message naming the product.
5. On success the cart is cleared, the order reference is remembered in the session, and
   the user is redirected to `orders:detail`.

## 8. Admin

- Category: list name/slug/active, prepopulated slug, search.
- Product: list name/category/price/stock/active/featured with `list_editable` for price,
  stock, active, featured; filters on category/active/featured; search; prepopulated slug;
  cover thumbnail column; fieldsets (basics / pricing & stock / content); inline gallery
  editor (upload, preview, alt text, sort order); inline specifications editor
  (group, name, value, sort order).
- Review: list product/user/rating/title/verified/published/created; `published` editable;
  filter by published/rating/verified/date; search title/body/product/user; bulk
  publish/hide actions; verified flag read-only.
- Inventory: Suppliers (terms, balance, link to statement); Purchases (inline items with
  product autocomplete, totals/payment columns, payments received list + due date, actions
  *Mark ordered* / *Receive into stock* / *Cancel*); Supplier payments (add with "apply to"
  purchase or keep-as-credit, read-only allocations, delete restores balances); Stock movements
  (read-only); Stock adjustments (add-only); Expenses; Other income.
- Order: list reference/name/email/status/total/created; filter by status/date; inline
  read-only items; status editable.

## 9. UI

- Responsive Tailwind layout: sticky header (logo, search, Shop link, cart badge with
  count, account menu), flash messages, footer.
- Product cards: cover image or generated placeholder, name, price, sale badge, out-of-stock badge.
- Product gallery (detail page, 2+ images): large main image, "n / total" counter,
  prev/next buttons, scrollable thumbnail strip, ←/→ keys. Vanilla JS (`static/js/gallery.js`);
  without JS the first image still shows. A single image renders without gallery controls.
- Product details section (`#details`, below the gallery/buy box): **Description**
  (paragraphs) and **Specifications** (card with grouped name/value rows). Side by side on
  large screens (3/5 + 2/5), stacked on mobile; a section with no content is omitted, and the
  whole block is hidden when both are empty. The buy box shows the summary (falling back to
  the first description paragraph) and a jump link to the specifications/description.
- Accessible forms: labels, inline errors, visible focus rings.
- Ratings: product cards show proportional stars + review count (hidden when unrated); the
  buy box shows stars, average and a "n reviews" link to `#reviews`.
- Reviews section (`#reviews`, below details): average, stars, "Based on n reviews", 5→1
  distribution bars; a write/edit card (log-in prompt for guests; star radio input, title,
  review; "Your review" with edit/delete and a notice if moderators hid it); review list with
  stars, title, "First L." author name, date, *Verified purchase* / *You* badges; sort
  (newest / highest / lowest, `?reviews_sort=`) and pagination (5 per page, `?reviews_page=`).
- Staff pages (Inventory, Profit & loss, ledger) share a sub-nav with quick links to add a
  purchase, income, expense or stock adjustment; an *Inventory* header link appears for staff.
- Custom 404 and 500 pages.

## 10. Operations

- Configuration is entirely environment-driven (see `.env.example`).
- `web` container entrypoint: wait for DB (compose healthcheck), `migrate`,
  `collectstatic --clear`, optionally create a superuser from `DJANGO_SUPERUSER_*`,
  optionally seed demo data when `SEED_DEMO_DATA=1` (including 3 generated gallery images
  per demo product that has none; `seed_demo --no-images` skips them; demo specs; and 2–6
  reviews per unreviewed demo product from demo users without usable passwords, plus one
  delivered demo order per reviewer so their badges are real), then start Gunicorn.
- Static files collected into a shared volume and served by Nginx with long cache headers
  (hashed filenames via `ManifestStaticFilesStorage`). Uploads go to a `media` volume.
- Postgres data persists in the `postgres_data` volume.
- Behind a proxy: `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS` from env.

## 11. Acceptance criteria

- `docker compose up --build` → storefront reachable at `http://localhost:8080`.
- Gunicorn reports 4 booted workers in the `web` logs.
- Demo data appears when seeding is enabled; admin login works with the configured superuser.
- A guest can add items to the cart, check out, and see the confirmation page; stock decreases.
- Receiving a purchase raises stock and re-averages cost; checkout and cancellation write the
  ledger; Σ ledger quantity equals stock for every product.
- The P&L for a period shows delivered sales, COGS, expenses, adjustments and net profit.
- Paying a supplier reduces what is owed; partial payments, overdue bills, advances and
  credit are tracked; each purchase's paid amount equals the sum of its allocations.
- A logged-in customer can rate and review a product once, edit or delete it; customers whose order
  containing it was delivered get a *Verified purchase* badge (a "Write a review" link appears
  on delivered orders); hidden reviews don't affect the rating.
- Ordering more than available stock fails without creating an order.
- `docker compose exec web python manage.py test` passes.
