# Ecom — Product Specification

A small but production-shaped e-commerce storefront built with Django, server-rendered
Jinja2 templates styled with Tailwind CSS, backed by PostgreSQL, and served by
Gunicorn behind Nginx via Docker Compose.

## 1. Goals

- Customers can browse a catalog, search/filter products, manage a cart, and place an order.
- Staff manage categories, products, stock and orders through the Django admin.
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
  `verified_purchase` (set on save: the user has a non-cancelled order containing the product),
  `is_approved` ("published", default true; staff can hide), `created_at`, `updated_at`.
  Unique per (`product`, `user`): one review per customer per product; re-submitting edits it.
- Only **published** reviews count towards ratings. `Product.objects.for_display()` annotates
  `rating_avg` / `rating_count`.

## 4. Pricing rules

- Subtotal = Σ unit price × quantity (Decimal, never float).
- Shipping = `SHIPPING_FLAT_RATE` (default 5.00) unless subtotal ≥ `FREE_SHIPPING_THRESHOLD`
  (default 50.00), then 0. Empty cart → 0.
- Currency symbol comes from `CURRENCY_SYMBOL` (default `৳`, Bangladeshi taka).

## 5. Pages & routes

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
| `/admin/`                     | —                        | Django admin |
| `/healthz`                    | `core:healthz`           | Returns `ok` (used by container healthcheck) |

## 6. Checkout behaviour

1. Cart must be non-empty, otherwise redirect to cart with a message.
2. Form is prefilled from the logged-in user (name, email).
3. On submit, inside one DB transaction: lock the products (`select_for_update`), verify
   stock for every line, decrement stock, create `Order` + `OrderItem`s with price snapshots.
4. If any line is short on stock, nothing is written; the user is sent back to the cart
   with a message naming the product.
5. On success the cart is cleared, the order reference is remembered in the session, and
   the user is redirected to `orders:detail`.

## 7. Admin

- Category: list name/slug/active, prepopulated slug, search.
- Product: list name/category/price/stock/active/featured with `list_editable` for price,
  stock, active, featured; filters on category/active/featured; search; prepopulated slug;
  cover thumbnail column; fieldsets (basics / pricing & stock / content); inline gallery
  editor (upload, preview, alt text, sort order); inline specifications editor
  (group, name, value, sort order).
- Review: list product/user/rating/title/verified/published/created; `published` editable;
  filter by published/rating/verified/date; search title/body/product/user; bulk
  publish/hide actions; verified flag read-only.
- Order: list reference/name/email/status/total/created; filter by status/date; inline
  read-only items; status editable.

## 8. UI

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
- Custom 404 and 500 pages.

## 9. Operations

- Configuration is entirely environment-driven (see `.env.example`).
- `web` container entrypoint: wait for DB (compose healthcheck), `migrate`,
  `collectstatic --clear`, optionally create a superuser from `DJANGO_SUPERUSER_*`,
  optionally seed demo data when `SEED_DEMO_DATA=1` (including 3 generated gallery images
  per demo product that has none; `seed_demo --no-images` skips them; demo specs; and 2–6
  reviews per unreviewed demo product from demo users without usable passwords), then start Gunicorn.
- Static files collected into a shared volume and served by Nginx with long cache headers
  (hashed filenames via `ManifestStaticFilesStorage`). Uploads go to a `media` volume.
- Postgres data persists in the `postgres_data` volume.
- Behind a proxy: `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS` from env.

## 10. Acceptance criteria

- `docker compose up --build` → storefront reachable at `http://localhost:8080`.
- Gunicorn reports 4 booted workers in the `web` logs.
- Demo data appears when seeding is enabled; admin login works with the configured superuser.
- A guest can add items to the cart, check out, and see the confirmation page; stock decreases.
- A logged-in customer can rate and review a product once, edit or delete it; buyers get a
  *Verified purchase* badge; hidden reviews don't affect the rating.
- Ordering more than available stock fails without creating an order.
- `docker compose exec web python manage.py test` passes.
