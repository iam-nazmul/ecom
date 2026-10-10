# CLAUDE.md

Guidance for working in this repository. The product requirements live in [SPEC.md](SPEC.md) —
read it before changing behaviour, and update it when behaviour changes.

## Stack

Django 5.2 LTS · Jinja2 templates · Tailwind CSS v4 · PostgreSQL 17 · Gunicorn (4 workers) ·
Nginx · Docker Compose. Python 3.13.

## Layout

```
config/            settings, urls, wsgi, jinja2 environment (config/jinja2.py)
apps/core/         home page, healthcheck
apps/catalog/      Category, Product, ProductImage (gallery), ProductSpec (specifications),
                   listing/detail views, seed_demo command
apps/cart/         session cart (apps/cart/cart.py) + views
apps/orders/       Order, OrderItem, checkout service (apps/orders/services.py)
apps/accounts/     signup / login / logout / order history
apps/inventory/    Supplier, Purchase(+Item), StockMovement ledger, StockAdjustment, Expense, Income;
                   services.py = ALL stock/cost rules + profit_and_loss(); demo.py = demo data;
                   payments.py = supplier payments, allocation, balances, statements
apps/reviews/      Review model, rating summary + review context (apps/reviews/services.py), submit/delete views
template/          ALL storefront templates (Jinja2), incl. macros/ and errors
assets/css/        Tailwind source (input.css) → compiled to static/css/app.css
static/            source static files (STATICFILES_DIRS); app.css is generated, git-ignored
nginx/             nginx.conf
gunicorn.conf.py   Gunicorn config (workers from GUNICORN_WORKERS, default 4)
entrypoint.sh      migrate, collectstatic, optional superuser + seed, then exec CMD
```

## Commands

```bash
cp .env.example .env                    # first time
docker compose up --build -d            # build + run → http://localhost:8080
docker compose logs -f web              # should show 4 "Booting worker" lines
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py seed_demo
docker compose exec web python manage.py createsuperuser
docker compose down                     # add -v to wipe the database/media volumes
```

Tailwind during development (outside Docker): `npm install && npm run watch:css`.
The Docker image builds the CSS itself in a Node stage, so `npm` is not needed to run the stack.

## Conventions

- **Templates are Jinja2, not DTL.** Put them under `template/<app>/...`. Do not use `{% load %}`,
  `{% url %}` or `{% csrf_token %}`. Use the globals defined in `config/jinja2.py`:
  `url('ns:name', arg, kw=val)`, `static('path')`, `get_messages(request)`, `get_cart(request)`,
  `query_with(request, page=2)`, and the `money` filter. Forms use `{{ csrf_input }}`.
  Reuse `template/macros/forms.html` and `template/macros/product.html`.
- The Django admin still uses Django templates; that's why `TEMPLATES` has a second
  `DjangoTemplates` backend. Don't remove it.
- Money is always `Decimal`; never use float. Pricing rules live in `apps/orders/services.py`
  and settings (`SHIPPING_FLAT_RATE`, `FREE_SHIPPING_THRESHOLD`, `CURRENCY_SYMBOL`).
- **Never assign `Product.stock` or `average_cost` directly.** Every stock change goes through
  `apps.inventory.services.record_movement()` on a product locked with `select_for_update()`
  (use `lock_products()`), inside `transaction.atomic()`. It writes the ledger row and
  re-averages cost. Higher-level helpers: `receive_purchase`, `record_sale`,
  `restock_cancelled_order`, `apply_adjustment`. Ledger rows are never edited or deleted.
- Revenue is recognised on `Order.delivered_at` (set in `Order.save()`); COGS uses the
  `OrderItem.unit_cost` snapshot. Purchases are inventory, not expenses. Sales and shipping
  income come only from delivered orders; `Income` is for everything else (never record
  product sales there, or they'd be counted twice).
- Supplier money goes through `apps/inventory/payments.py` (`record_payment`, `apply_credit`,
  `delete_payment`, `release_allocations`). Never set `Purchase.amount_paid` directly — it is a
  cache of `PaymentAllocation` rows.
- Costs use 4 decimal places (`average_cost`, `unit_cost`); money shown via `|money` (2 dp).
- Staff-only views use `@staff_member_required`; financial views (P&L) use `superuser_required`
  in `apps/inventory/views.py` (403 for plain staff). Summary money cards on the inventory
  dashboard and suppliers page are wrapped in `{% if request.user.is_superuser %}`.
- Fetch products for rendering with `Product.objects.for_display()` (active + category +
  prefetched `images`). Templates use `product.gallery` / `product.cover_url`, never
  `product.image.url` directly, so gallery-only products still get a cover.
- The product page is rendered by `render_product_detail()` in `apps/catalog/views.py` so other
  apps (e.g. reviews re-rendering a form with errors) produce the same page. Rating macros live
  in `template/macros/reviews.html`; the reviews section is `template/reviews/_section.html`.
- "Verified purchase" = a **Delivered** order for that user containing the product. It is stored
  on `Review` and kept in sync by `refresh_verified_purchases()` via `Order` save/delete signals
  (run `on_commit`). Change order status with `.save()`, not `QuerySet.update()`, or the badge
  won't follow; in tests wrap status changes in `self.captureOnCommitCallbacks(execute=True)`.
- Page-specific JS goes in `static/js/` and is included via `{% block scripts %}`; keep it
  vanilla and progressive (the page must work without it).
- State-changing views are POST-only (`@require_POST`), including logout.
- Style with Tailwind utility classes in templates; shared component classes (`btn`, `input`,
  `card`, …) live in `assets/css/input.css` under `@layer components`. After adding new class
  names, the CSS must be rebuilt (rebuild the image or run the watcher).
- Configuration comes from environment variables only (`config/settings.py`); add new ones to
  `.env.example` and `docker-compose.yml`.
- Each app has its tests in `apps/<app>/tests.py`. Add tests for any new behaviour.
  `response.context` is `None` for Jinja2-rendered pages in the test client — assert on the
  HTML (`assertContains`) or on the database instead.

## Gotchas

- `STORAGES` uses `ManifestStaticFilesStorage` when `DEBUG=False`, so a missing `collectstatic`
  raises errors on page render. The entrypoint runs it on every start; tests use plain storage.
- The static and media volumes are shared between `web` (writes) and `nginx` (reads, `:ro`).
- After changing `requirements.txt`, `package.json` or any template classes, rebuild:
  `docker compose up --build -d`.
