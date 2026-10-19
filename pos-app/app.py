import os
from datetime import datetime
from flask import Flask, jsonify, render_template, request
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


@app.route("/")
def page_kasir():
    return render_template("index.html")


@app.route("/products")
def page_products():
    return render_template("products.html")


@app.route("/laporan")
def page_laporan():
    return render_template("laporan.html")


# API GET PRODUCTS
@app.route("/api/products", methods=["GET"])
def get_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products ORDER BY name ASC;")
        products = cursor.fetchall()
        cursor.close()
        conn.close()

        # Format ulang nilai numeric ke float agar aman untuk JSON
        result = []
        for p in products:
            item = dict(p)
            item["buy_price"] = float(item["buy_price"])
            item["sell_price"] = float(item["sell_price"])
            result.append(item)

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API POST PRODUCT (TAMBAH)
@app.route("/api/products", methods=["POST"])
def add_product():
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (name, buy_price, sell_price, stock) VALUES"
            " (%s, %s, %s, %s);",
            (
                data.get("name"),
                float(data.get("buy_price", 0)),
                float(data.get("sell_price", 0)),
                int(data.get("stock", 0)),
            ),
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": "Produk berhasil ditambahkan"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API PUT PRODUCT (EDIT)
@app.route("/api/products/<int:product_id>", methods=["PUT"])
def update_product(product_id):
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE products SET name = %s, buy_price = %s, sell_price = %s,"
            " stock = %s WHERE id = %s;",
            (
                data.get("name"),
                float(data.get("buy_price", 0)),
                float(data.get("sell_price", 0)),
                int(data.get("stock", 0)),
                product_id,
            ),
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": "Produk berhasil diperbarui"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API DELETE PRODUCT
@app.route("/api/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = %s;", (product_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": "Produk berhasil dihapus"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API TRANSACTIONS
@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    try:
        data = request.json
        cart = data.get("cart", [])
        pay_amount = float(data.get("pay_amount", 0))

        if not cart:
            return jsonify({"error": "Keranjang belanja kosong"}), 400

        total_amount = 0
        total_profit = 0
        conn = get_db_connection()
        cursor = conn.cursor()

        for item in cart:
            cursor.execute(
                "SELECT * FROM products WHERE id = %s;", (item["id"],)
            )
            product = cursor.fetchone()
            if not product or product["stock"] < item["qty"]:
                cursor.close()
                conn.close()
                return jsonify({"error": "Stok barang tidak mencukupi"}), 400

            subtotal = float(product["sell_price"]) * item["qty"]
            profit = (
                float(product["sell_price"]) - float(product["buy_price"])
            ) * item["qty"]
            total_amount += subtotal
            total_profit += profit

        payment_status = (
            "Lunas" if pay_amount >= total_amount else "Belum Dibayar"
        )
        change_amount = max(0.0, pay_amount - total_amount)

        today_prefix = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT COUNT(*) as count FROM transactions WHERE date LIKE %s;",
            (f"{today_prefix}%",),
        )
        queue_row = cursor.fetchone()
        queue_number = queue_row["count"] + 1
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO transactions (queue_number, date, total_amount, total_profit, pay_amount, change_amount, payment_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
        """,
            (
                queue_number,
                date_str,
                total_amount,
                total_profit,
                pay_amount,
                change_amount,
                payment_status,
            ),
        )

        transaction_id = cursor.fetchone()["id"]

        for item in cart:
            cursor.execute(
                "SELECT * FROM products WHERE id = %s;", (item["id"],)
            )
            product = cursor.fetchone()
            subtotal = float(product["sell_price"]) * item["qty"]
            cursor.execute(
                """
                INSERT INTO transaction_details (transaction_id, product_id, product_name, qty, buy_price, sell_price, subtotal)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
                (
                    transaction_id,
                    product["id"],
                    product["name"],
                    item["qty"],
                    product["buy_price"],
                    product["sell_price"],
                    subtotal,
                ),
            )
            cursor.execute(
                "UPDATE products SET stock = stock - %s WHERE id = %s;",
                (item["qty"], product["id"]),
            )

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "message": "Transaksi berhasil diproses",
            "queue_number": queue_number,
            "payment_status": payment_status,
            "change": change_amount,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API REPORTS
@app.route("/api/reports", methods=["GET"])
def get_reports():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC;")
        transactions = cursor.fetchall()

        report_list = []
        total_income = 0
        total_profit = 0

        for t in transactions:
            if t["payment_status"] == "Lunas":
                total_income += float(t["total_amount"])
                total_profit += float(t["total_profit"])

            cursor.execute(
                "SELECT product_name, qty, sell_price, subtotal FROM"
                " transaction_details WHERE transaction_id = %s;",
                (t["id"],),
            )
            details = cursor.fetchall()

            item_list = []
            for d in details:
                di = dict(d)
                di["sell_price"] = float(di["sell_price"])
                di["subtotal"] = float(di["subtotal"])
                item_list.append(di)

            report_list.append({
                "id": t["id"],
                "queue_number": t["queue_number"],
                "date": t["date"],
                "total_amount": float(t["total_amount"]),
                "total_profit": float(t["total_profit"]),
                "pay_amount": float(t["pay_amount"]),
                "change_amount": float(t["change_amount"]),
                "payment_status": t["payment_status"],
                "items": item_list,
            })

        cursor.close()
        conn.close()

        return jsonify({
            "summary": {
                "total_income": total_income,
                "total_profit": total_profit,
                "total_transactions": len(transactions),
            },
            "transactions": report_list,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run()
