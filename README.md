# ecom

A small but production-shaped e-commerce store built with **Django 5.2**. Pages are rendered on
the server with Jinja2 templates styled with Tailwind CSS v4. Data lives in PostgreSQL 17, and
Gunicorn runs behind Nginx under Docker Compose.

There is no SPA, no JS framework and no payment gateway. Orders are **Cash on Delivery**.

## Features

- **Storefront**: a catalog with categories, search and filters. Product pages have an image
  gallery, a specifications table, and ratings and reviews (with a "verified purchase" badge).
  The cart is stored in the session, and guests can check out.
- **Accounts**: signup, login and logout, plus an order history for each customer.
- **Orders**: orders move through pending → processing → shipped → delivered, or are
  cancelled. Cancelling an order puts its items back in stock.
- **Inventory**: suppliers, purchase orders with landed cost, and an append-only stock ledger
  that keeps a weighted-average cost. Staff can also record stock adjustments, expenses and
  other income.
- **Supplier payments**: payments are allocated to purchases (oldest unpaid first by default),
  and the app tracks supplier credit, balances and overdue purchases.
- **Profit & loss**: revenue is counted on delivery, and COGS comes from the cost recorded at
  the time of sale. The report breaks results down by month, by expense category and by
  product. Only superusers can see it.
- **Point of sale (optional)**: registers and cash sessions, barcode search, sale discounts,
  split payments (cash, card, bKash, Nagad, Rocket), receipts, voids and a Z-report. POS sales
  go through the same inventory and P&L as online orders. Set `POS_ENABLED=0` to turn it off
  completely.

The full product requirements are in [SPEC.md](SPEC.md).

## Tech stack

| Concern       | Choice                                                    |
|---------------|-----------------------------------------------------------|
| Language      | Python 3.13                                               |
| Framework     | Django 5.2 LTS                                            |
| Templates     | Jinja2 for the storefront (the Django admin keeps DTL)    |
| CSS           | Tailwind CSS v4, built in a Node stage of the Docker image |
| Database      | PostgreSQL 17 (psycopg 3)                                 |
| App server    | Gunicorn, 4 sync workers by default                       |
| Reverse proxy | Nginx, which serves `/static/` and `/media/`              |
| Orchestration | Docker Compose with three services: `db`, `web`, `nginx`  |

## Quick start

You need Docker with Compose v2.

```bash
git clone git@github.com:iam-nazmul/ecom.git
cd ecom
cp .env.example .env          # then edit DJANGO_SECRET_KEY and the passwords
docker compose up --build -d
```

Then open <http://localhost:8080>.

Each time the `web` container starts, it runs migrations and `collectstatic`. When the
`DJANGO_SUPERUSER_*` variables are set, it also creates a superuser, and when
`SEED_DEMO_DATA=1` it loads demo data. With the defaults in `.env.example`:

| What            | Where                                      |
|-----------------|--------------------------------------------|
| Storefront      | <http://localhost:8080/>                   |
| Django admin    | <http://localhost:8080/admin/> (`admin` / `admin12345`) |
| Inventory       | <http://localhost:8080/staff/inventory/>   |
| Suppliers       | <http://localhost:8080/staff/suppliers/>   |
| Profit & loss   | <http://localhost:8080/staff/reports/profit-loss/> |
| Point of sale   | <http://localhost:8080/pos/> (when `POS_ENABLED=1`) |
| Healthcheck     | <http://localhost:8080/healthz>            |

> Change the default superuser password and `DJANGO_SECRET_KEY` before you expose the app
> anywhere.

## Common commands

```bash
docker compose logs -f web                                   # should show 4 "Booting worker" lines
docker compose exec web python manage.py test                # run the test suite
docker compose exec -e POS_ENABLED=0 web python manage.py test   # test with the POS turned off
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py seed_demo           # load demo catalog and data
docker compose exec web python manage.py createsuperuser
docker compose up --build -d                                 # rebuild after changing deps or templates
docker compose down                                          # stop; add -v to wipe DB and media
```

## Configuration

All settings come from environment variables (see [.env.example](.env.example)). Any new
variable must be added to both `.env.example` and [docker-compose.yml](docker-compose.yml).

| Variable                      | Default (`.env.example`)    | Purpose                                   |
|-------------------------------|-----------------------------|-------------------------------------------|
| `DJANGO_DEBUG`                | `0`                         | Debug mode                                |
| `DJANGO_SECRET_KEY`           | —                           | **Must be changed**                       |
| `DJANGO_ALLOWED_HOSTS`        | `localhost,127.0.0.1`       | Comma-separated hosts                     |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://localhost:8080,…`   | Origins allowed to make POST requests     |
| `DJANGO_TIME_ZONE`            | `Asia/Dhaka`                |                                           |
| `POSTGRES_DB` / `_USER` / `_PASSWORD` | `ecom`              | Database credentials                      |
| `GUNICORN_WORKERS`            | `4`                         | Number of Gunicorn worker processes       |
| `NGINX_PORT`                  | `8080`                      | Port published on the host                |
| `DJANGO_SUPERUSER_USERNAME` / `_EMAIL` / `_PASSWORD` | `admin` … | Superuser created on start if set  |
| `SEED_DEMO_DATA`              | `1`                         | Run `seed_demo` on start                  |
| `CURRENCY_SYMBOL`             | `৳`                         | Shown by the `money` filter               |
| `SHIPPING_FLAT_RATE`          | `5.00`                      | Shipping charge per order                 |
| `FREE_SHIPPING_THRESHOLD`     | `5000.00`                   | Subtotal at which shipping becomes free   |
| `POS_ENABLED`                 | `1`                         | `0` removes the POS app entirely          |
| `POS_STORE_NAME` / `_ADDRESS` / `POS_RECEIPT_FOOTER` | —   | Printed on POS receipts                   |
| `POS_MAX_DISCOUNT_PERCENT`    | `20`                        | Highest discount staff can give (superusers can give up to 100) |

## Project layout

```
config/          settings, urls, wsgi, Jinja2 environment
apps/core/       home page, healthcheck, staff menu registry
apps/catalog/    categories, products, gallery images, specifications, seed_demo
apps/cart/       session cart
apps/orders/     orders and the checkout service
apps/accounts/   signup, login, logout, order history
apps/inventory/  suppliers, purchases, stock ledger, adjustments, expenses, income, P&L
apps/pos/        optional point of sale (self-contained)
apps/reviews/    product reviews and ratings
template/        storefront Jinja2 templates and macros
assets/css/      Tailwind source (compiled to static/css/app.css)
static/          static source files
nginx/           Nginx config
```

## Development

The Docker image compiles the CSS itself. If you edit templates outside Docker, run the
Tailwind watcher so new utility classes get built:

```bash
npm install
npm run watch:css
```

Before you change anything, read [CLAUDE.md](CLAUDE.md) for the project conventions. The most
important rules are:

- Templates are Jinja2, not DTL. Use the globals `url()`, `static()` and `csrf_input`.
- Money is always `Decimal`.
- Never set `Product.stock` or `average_cost` directly. Every stock change goes through
  `apps.inventory.services`.
- Nothing outside `apps/pos` may import or link to it. A test enforces this.
- Every app keeps its tests in `apps/<app>/tests.py`. Run the suite with the POS on and off
  before you ship.
