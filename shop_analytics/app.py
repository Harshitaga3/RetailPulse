import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

from flask import (  # noqa: E402
    Flask, abort, flash, redirect, render_template, request, session, url_for,
)

from config import Config  # noqa: E402
from models import (  # noqa: E402
    ORDER_STATUSES, Category, Customer, Order, OrderItem, Payment, Product, db,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card", "Net Banking", "Cash on Delivery"]
COUPONS = {"WELCOME10": Decimal("0.10"), "FESTIVE15": Decimal("0.15")}
MAX_QTY_PER_ITEM = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TWO_PLACES = Decimal("0.01")


# ---------- helpers ----------
def money(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@app.template_filter("inr")
def inr(value):
    return "₹{:,.2f}".format(value or 0)


def cart_lines():
    """Return (lines, subtotal) for the session cart. Drops items that no longer exist."""
    cart = session.get("cart", {})
    if not cart:
        return [], Decimal("0")
    ids = [int(i) for i in cart]
    products = Product.query.filter(Product.id.in_(ids), Product.is_active.is_(True)).all()
    lines, subtotal = [], Decimal("0")
    for p in products:
        qty = cart[str(p.id)]
        total = p.price * qty
        lines.append({"product": p, "qty": qty, "total": total})
        subtotal += total
    return lines, subtotal


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    return {"cart_count": sum(cart.values()), "is_admin": session.get("is_admin", False)}


# ---------- storefront ----------
@app.route("/")
def shop():
    category_id = request.args.get("category", type=int)
    query = Product.query.filter_by(is_active=True)
    if category_id:
        query = query.filter_by(category_id=category_id)
    products = query.order_by(Product.name).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template("shop.html", products=products, categories=categories, active=category_id)


@app.post("/cart/add/<int:product_id>")
def cart_add(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        abort(404)
    cart = session.get("cart", {})
    current = cart.get(str(product_id), 0)
    if current + 1 > min(product.stock, MAX_QTY_PER_ITEM):
        flash(f"Only {min(product.stock, MAX_QTY_PER_ITEM)} of {product.name} can be ordered.", "error")
    else:
        cart[str(product_id)] = current + 1
        session["cart"] = cart
        flash(f"{product.name} added to your cart.", "ok")
    return redirect(request.referrer or url_for("shop"))


@app.route("/cart")
def cart_view():
    lines, subtotal = cart_lines()
    return render_template("cart.html", lines=lines, subtotal=subtotal)


@app.post("/cart/update/<int:product_id>")
def cart_update(product_id):
    cart = session.get("cart", {})
    product = db.session.get(Product, product_id)
    qty = request.form.get("qty", type=int)
    if not product or qty is None:
        abort(400)
    if qty <= 0:
        cart.pop(str(product_id), None)
    else:
        limit = min(product.stock, MAX_QTY_PER_ITEM)
        if qty > limit:
            flash(f"Only {limit} of {product.name} can be ordered.", "error")
            qty = limit
        cart[str(product_id)] = qty
    session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    lines, subtotal = cart_lines()
    if not lines:
        flash("Your cart is empty.", "error")
        return redirect(url_for("shop"))

    if request.method == "GET":
        return render_template("checkout.html", lines=lines, subtotal=subtotal,
                               methods=PAYMENT_METHODS, form={})

    form = request.form
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    city = form.get("city", "").strip()
    state = form.get("state", "").strip()
    method = form.get("payment_method", "")
    coupon = form.get("coupon", "").strip().upper()

    errors = []
    if len(name) < 2:
        errors.append("Enter your full name.")
    if not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    if not city:
        errors.append("Enter your city.")
    if not state:
        errors.append("Enter your state.")
    if method not in PAYMENT_METHODS:
        errors.append("Choose a payment method.")
    if coupon and coupon not in COUPONS:
        errors.append(f"Coupon {coupon} is not valid.")
    for line in lines:
        if line["qty"] > line["product"].stock:
            errors.append(f"Only {line['product'].stock} of {line['product'].name} left in stock.")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("checkout.html", lines=lines, subtotal=subtotal,
                               methods=PAYMENT_METHODS, form=form), 400

    try:
        customer = Customer.query.filter_by(email=email).first()
        if customer:
            customer.name, customer.city, customer.state = name, city, state
        else:
            customer = Customer(name=name, email=email, city=city, state=state)
            db.session.add(customer)

        discount = money(subtotal * COUPONS[coupon]) if coupon else Decimal("0.00")
        total = money(subtotal - discount)

        order = Order(customer=customer, coupon_code=coupon or None,
                      discount_amount=discount, total_amount=total, status="placed")
        for line in lines:
            p = line["product"]
            order.items.append(OrderItem(product_id=p.id, quantity=line["qty"],
                                         unit_price=p.price, unit_cost=p.cost))
            p.stock -= line["qty"]
        cod = method == "Cash on Delivery"
        order.payment = Payment(method=method, amount=total,
                                status="pending" if cod else "paid",
                                paid_at=None if cod else datetime.utcnow())
        db.session.add(order)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Checkout failed")
        flash("Something went wrong while placing your order. Please try again.", "error")
        return render_template("checkout.html", lines=lines, subtotal=subtotal,
                               methods=PAYMENT_METHODS, form=form), 500

    session.pop("cart", None)
    return redirect(url_for("order_success", order_id=order.id))


@app.route("/order/<int:order_id>")
def order_success(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    return render_template("success.html", order=order)


# ---------- admin ----------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == app.config["ADMIN_PASSWORD"]:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        flash("Wrong password.", "error")
    return render_template("admin_login.html")


@app.post("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("shop"))


@app.route("/admin")
@admin_required
def admin():
    products = Product.query.order_by(Product.id.desc()).all()
    categories = Category.query.order_by(Category.name).all()
    orders = Order.query.order_by(Order.order_date.desc()).limit(25).all()
    return render_template("admin.html", products=products, categories=categories,
                           orders=orders, statuses=ORDER_STATUSES)


@app.post("/admin/products")
@admin_required
def admin_add_product():
    name = request.form.get("name", "").strip()
    category_name = request.form.get("category", "").strip()
    try:
        price = money(request.form.get("price", ""))
        cost = money(request.form.get("cost", ""))
        stock = int(request.form.get("stock", ""))
    except Exception:
        flash("Price, cost and stock must be numbers.", "error")
        return redirect(url_for("admin"))
    if not name or not category_name or price <= 0 or cost < 0 or stock < 0:
        flash("Fill in every field with valid values.", "error")
        return redirect(url_for("admin"))
    if cost > price:
        flash("Cost is higher than price. Check the numbers.", "error")
        return redirect(url_for("admin"))

    category = Category.query.filter_by(name=category_name).first()
    if not category:
        category = Category(name=category_name)
        db.session.add(category)
    db.session.add(Product(name=name, category=category, price=price, cost=cost, stock=stock))
    db.session.commit()
    flash(f"{name} added.", "ok")
    return redirect(url_for("admin"))


@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def admin_update_status(order_id):
    order = db.session.get(Order, order_id)
    status = request.form.get("status")
    if not order or status not in ORDER_STATUSES:
        abort(400)
    order.status = status
    now = datetime.utcnow()
    if status == "shipped" and not order.shipped_at:
        order.shipped_at = now
    if status == "delivered":
        order.shipped_at = order.shipped_at or now
        order.delivered_at = now
        if order.payment and order.payment.status == "pending":
            order.payment.status = "paid"
            order.payment.paid_at = now
    db.session.commit()
    flash(f"Order #{order.id} marked {status}.", "ok")
    return redirect(url_for("admin"))


# ---------- CLI ----------
SEED = {
    "Electronics": [("Wireless Earbuds", 1999, 1100, 80), ("Power Bank 10000mAh", 1299, 700, 120),
                    ("Smart Watch", 3499, 2100, 40)],
    "Fashion": [("Cotton Kurta", 899, 380, 150), ("Denim Jacket", 2299, 1200, 60),
                ("Canvas Sneakers", 1499, 700, 90)],
    "Home & Kitchen": [("Steel Water Bottle", 549, 220, 200), ("Non-stick Pan", 1199, 560, 70),
                       ("LED Desk Lamp", 799, 350, 110)],
    "Beauty": [("Vitamin C Serum", 699, 260, 130), ("Herbal Shampoo", 399, 150, 180)],
    "Books": [("SQL for Analysts", 599, 280, 100), ("Storytelling with Data", 749, 400, 80)],
}


@app.cli.command("init-db")
def init_db():
    """Create all tables."""
    db.create_all()
    table_names = sorted(db.metadata.tables)
    print(f"Database: {db.engine.url}")
    print(f"Tables created: {', '.join(table_names)}")


@app.cli.command("seed-products")
def seed_products():
    """Load a starter catalog (skips if products already exist)."""
    db.create_all()
    if Product.query.first():
        print("Products already exist; nothing to do.")
        return
    for cat_name, items in SEED.items():
        cat = Category(name=cat_name)
        db.session.add(cat)
        for name, price, cost, stock in items:
            db.session.add(Product(name=name, category=cat, price=price, cost=cost, stock=stock))
    db.session.commit()
    print("Starter catalog loaded.")


if __name__ == "__main__":
    app.run(debug=True)
