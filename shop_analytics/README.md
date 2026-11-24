# RetailPulse: e-commerce app for an end-to-end analytics project

Flask storefront that writes orders to MySQL. Phase 2 adds a star-schema analytics layer, Phase 3 a data generator, Phase 4 a Power BI dashboard.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then edit the values
```

On Windows PowerShell, copy the example with:

```powershell
Copy-Item .env.example .env
```

Create the database in MySQL first:

```sql
CREATE DATABASE shop_analytics CHARACTER SET utf8mb4;
```

Then:

```bash
flask --app app init-db           # creates the tables
flask --app app seed-products     # loads a starter catalog
flask --app app run --debug
```

Open http://127.0.0.1:5000. Admin page: /admin/login (password from `.env`).

If `DATABASE_URL` is not set, the app uses a local SQLite file so you can try it without MySQL.

Run Flask commands from the `shop_analytics` folder. The `init-db` command prints the active database URL and confirms the created table names.

## What gets captured (for analysis later)

- `customers`: name, email, city, state, signup date
- `products` and `categories`: price **and cost** (so profit and margin can be computed)
- `orders`: date, status, coupon, discount, total, shipped/delivered timestamps
- `order_items`: quantity, with price and cost copied at purchase time
- `payments`: method, status

Coupons to test: `WELCOME10` (10%), `FESTIVE15` (15%).
