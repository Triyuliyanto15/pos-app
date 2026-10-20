import os
import json
from datetime import datetime, date
from decimal import Decimal
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "ganti-secret-key-untuk-produksi")
database_url = os.getenv("DATABASE_URL", "sqlite:///mie_ayam.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db = SQLAlchemy(app)

class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    price = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    queue_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    payment_received = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    payment_status = db.Column(db.String(20), nullable=False, default="BELUM LUNAS")
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan", lazy=True)

class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_name = db.Column(db.String(120), nullable=False)
    unit_price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    subtotal = db.Column(db.Integer, nullable=False)

def rupiah(value):
    return "Rp" + f"{int(value):,}".replace(",", ".")
app.jinja_env.filters["rupiah"] = rupiah

DEFAULT_PRODUCTS = [
    ("Mie Ayam", 16000),
    ("Mie Ayam Pangsit", 18000),
    ("Mie Ayam Bakso", 18000),
    ("Mie Ayam Pangsit Bakso", 22000),
    ("Pangsit aja", 16000),
    ("Pangsit Bakso", 18000),
]

with app.app_context():
    db.create_all()
    if Product.query.count() == 0:
        for name, price in DEFAULT_PRODUCTS:
            db.session.add(Product(name=name, price=price))
        db.session.commit()

@app.get("/")
def index():
    products = Product.query.filter_by(active=True).order_by(Product.name).all()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(8).all()
    return render_template("index.html", products=products, recent_orders=recent_orders)

@app.post("/orders")
def create_order():
    queue_name = request.form.get("queue_name", "").strip()
    if not queue_name:
        return "No antrean/nama pelanggan wajib diisi.", 400
    try:
        received = max(0, int(request.form.get("payment_received", "0") or 0))
        cart = json.loads(request.form.get("cart_json", "[]"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return "Data pesanan tidak valid.", 400
    if not isinstance(cart, list) or not cart:
        return "Pilih minimal satu menu.", 400
    order = Order(queue_name=queue_name, payment_received=received)
    total = 0
    for entry in cart:
        try:
            product_id = int(entry.get("product_id"))
            quantity = int(entry.get("quantity", 0))
        except (ValueError, TypeError, AttributeError):
            continue
        product = db.session.get(Product, product_id)
        if not product or not product.active or quantity < 1 or quantity > 99:
            continue
        subtotal = product.price * quantity
        total += subtotal
        order.items.append(OrderItem(product_name=product.name, unit_price=product.price, quantity=quantity, subtotal=subtotal))
    if not order.items:
        return "Item pesanan tidak valid.", 400
    order.total = total
    order.payment_status = "LUNAS" if received >= total else "BELUM LUNAS"
    db.session.add(order)
    db.session.commit()
    return redirect(url_for("receipt", order_id=order.id))

@app.get("/receipt/<int:order_id>")
def receipt(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    return render_template("receipt.html", order=order, change=max(0, order.payment_received-order.total), shortage=max(0, order.total-order.payment_received))

@app.get("/laporan")
def laporan():
    today = date.today()
    day_value = request.args.get("tanggal", today.isoformat())
    month_value = request.args.get("bulan", today.strftime("%Y-%m"))
    try:
        selected_day = date.fromisoformat(day_value)
    except ValueError:
        selected_day = today
    try:
        selected_month = datetime.strptime(month_value, "%Y-%m").date()
    except ValueError:
        selected_month = today
    day_start = datetime.combine(selected_day, datetime.min.time())
    day_end = datetime.combine(selected_day, datetime.max.time())
    month_start = datetime(selected_month.year, selected_month.month, 1)
    if selected_month.month == 12:
        month_end = datetime(selected_month.year + 1, 1, 1)
    else:
        month_end = datetime(selected_month.year, selected_month.month + 1, 1)
    daily_orders = Order.query.filter(Order.created_at >= day_start, Order.created_at <= day_end).order_by(Order.created_at.desc()).all()
    monthly_orders = Order.query.filter(Order.created_at >= month_start, Order.created_at < month_end).order_by(Order.created_at.desc()).all()
    daily_total = sum(o.total for o in daily_orders)
    daily_paid = sum(min(o.payment_received, o.total) for o in daily_orders)
    monthly_total = sum(o.total for o in monthly_orders)
    monthly_paid = sum(min(o.payment_received, o.total) for o in monthly_orders)
    return render_template("reports.html", selected_day=selected_day.isoformat(), selected_month=selected_month.strftime("%Y-%m"),
        daily_orders=daily_orders, monthly_orders=monthly_orders, daily_total=daily_total, daily_paid=daily_paid,
        monthly_total=monthly_total, monthly_paid=monthly_paid)

# API JSON
@app.get("/api/products")
def api_products():
    products = Product.query.filter_by(active=True).order_by(Product.name).all()
    return jsonify([{"id": p.id, "name": p.name, "price": p.price} for p in products])

@app.post("/api/products")
def api_add_product():
    data = request.get_json(silent=True) or request.form
    name = str(data.get("name", "")).strip()
    try:
        price = int(data.get("price", 0))
    except (ValueError, TypeError):
        return jsonify(error="Harga harus berupa angka."), 400
    if not name or price < 0:
        return jsonify(error="Nama menu wajib diisi dan harga tidak boleh negatif."), 400
    if Product.query.filter(func.lower(Product.name) == name.lower()).first():
        return jsonify(error="Nama menu sudah ada."), 409
    product = Product(name=name, price=price)
    db.session.add(product)
    db.session.commit()
    return jsonify(id=product.id, name=product.name, price=product.price), 201

@app.get("/api/orders/<int:order_id>")
def api_order(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify(error="Pesanan tidak ditemukan."), 404
    return jsonify({
        "id": order.id, "queue_name": order.queue_name, "created_at": order.created_at.isoformat(),
        "items": [{"name": i.product_name, "price": i.unit_price, "quantity": i.quantity, "subtotal": i.subtotal} for i in order.items],
        "total": order.total, "payment_received": order.payment_received,
        "change": max(0, order.payment_received-order.total),
        "payment_status": order.payment_status
    })

@app.get("/api/reports")
def api_reports():
    period = request.args.get("period", "daily")
    value = request.args.get("date", date.today().isoformat())
    try:
        if period == "monthly":
            month = datetime.strptime(value[:7], "%Y-%m").date()
            start = datetime(month.year, month.month, 1)
            end = datetime(month.year + 1, 1, 1) if month.month == 12 else datetime(month.year, month.month + 1, 1)
        else:
            d = date.fromisoformat(value[:10])
            start, end = datetime.combine(d, datetime.min.time()), datetime.combine(d, datetime.max.time())
    except ValueError:
        return jsonify(error="Format date gunakan YYYY-MM-DD (daily) atau YYYY-MM (monthly)."), 400
    orders = Order.query.filter(Order.created_at >= start, Order.created_at <= end if period != "monthly" else Order.created_at < end).all()
    return jsonify(period=period, count=len(orders), sales_total=sum(o.total for o in orders),
                   paid_total=sum(min(o.payment_received, o.total) for o in orders),
                   unpaid_orders=sum(o.payment_status != "LUNAS" for o in orders))

@app.get("/health")
def health():
    return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
