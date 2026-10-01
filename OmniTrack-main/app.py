import logging
import os
from decimal import Decimal, InvalidOperation

import pymysql
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "dev-secret-key-change-me"
)

# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------

ALLOWED_ORDER_STATUSES = {
    "Pending",
    "Processing",
    "Shipped",
    "Delivered",
    "Cancelled",
}

LOW_STOCK_THRESHOLD = 10


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

def get_db_connection():
    """Create and return a MySQL database connection."""

    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "sales_management"),
        port=int(os.getenv("DB_PORT", "3306")),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def parse_positive_integer(value, field_name):
    """Validate and convert a positive integer."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid number.")

    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    return number


def parse_non_negative_decimal(value, field_name):
    """Validate and convert a non-negative decimal value."""

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid amount.")

    if number < 0:
        raise ValueError(f"{field_name} cannot be negative.")

    return number


# ---------------------------------------------------------
# 1. DASHBOARD
# ---------------------------------------------------------

@app.route("/")
def dashboard():
    """Display sales dashboard and business KPIs."""

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

            # KPI 1: Total Revenue
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_amount), 0) AS revenue
                FROM orders
                WHERE status != 'Cancelled'
                """
            )
            total_revenue = cursor.fetchone()["revenue"]

            # KPI 2: Pending Orders
            cursor.execute(
                """
                SELECT COUNT(*) AS pending_count
                FROM orders
                WHERE status = 'Pending'
                """
            )
            pending_orders = cursor.fetchone()["pending_count"]

            # KPI 3: Total Units Sold
            cursor.execute(
                """
                SELECT COALESCE(SUM(oi.quantity), 0) AS units_sold
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                WHERE o.status != 'Cancelled'
                """
            )
            total_units = cursor.fetchone()["units_sold"]

            # KPI 4: Registered Customers
            cursor.execute(
                """
                SELECT COUNT(*) AS clients
                FROM customers
                """
            )
            total_clients = cursor.fetchone()["clients"]

            # Low Stock Products
            cursor.execute(
                """
                SELECT *
                FROM products
                WHERE stock_quantity < %s
                ORDER BY stock_quantity ASC
                """,
                (LOW_STOCK_THRESHOLD,),
            )
            low_stock_items = cursor.fetchall()

            # Orders Matrix
            cursor.execute(
                """
                SELECT
                    o.order_id,
                    o.order_date,
                    o.status,
                    o.total_amount,
                    CONCAT(c.first_name, ' ', c.last_name) AS customer_name
                FROM orders o
                JOIN customers c
                    ON o.customer_id = c.customer_id
                ORDER BY o.order_date DESC
                """
            )
            all_orders = cursor.fetchall()

        return render_template(
            "dashboard.html",
            revenue=total_revenue,
            pending=pending_orders,
            units_sold=total_units,
            clients=total_clients,
            low_stock=low_stock_items,
            orders=all_orders,
        )

    finally:
        conn.close()


# ---------------------------------------------------------
# 2. UPDATE ORDER STATUS
# ---------------------------------------------------------

@app.route("/orders/update-status/<int:order_id>", methods=["POST"])
def update_order_status(order_id):
    """Update the lifecycle status of an order."""

    new_status = request.form.get("status", "").strip()

    if new_status not in ALLOWED_ORDER_STATUSES:
        flash("Invalid order status.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orders
                SET status = %s
                WHERE order_id = %s
                """,
                (new_status, order_id),
            )

            if cursor.rowcount == 0:
                flash(f"Order #{order_id} was not found.", "danger")
                conn.rollback()
                return redirect(url_for("dashboard"))

        conn.commit()

        flash(
            f"Order #{order_id} status changed to '{new_status}'.",
            "success",
        )

    except pymysql.MySQLError:
        conn.rollback()
        logger.exception("Failed to update order status.")

        flash(
            "Unable to update the order status. Please try again.",
            "danger",
        )

    finally:
        conn.close()

    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# 3. RESTOCK PRODUCT
# ---------------------------------------------------------

@app.route("/products/restock/<int:product_id>", methods=["POST"])
def restock_product(product_id):
    """Increase product stock quantity."""

    try:
        added_units = parse_positive_integer(
            request.form.get("added_stock"),
            "Added stock",
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE products
                SET stock_quantity = stock_quantity + %s
                WHERE product_id = %s
                """,
                (added_units, product_id),
            )

            if cursor.rowcount == 0:
                flash("Product not found.", "danger")
                conn.rollback()
                return redirect(url_for("dashboard"))

        conn.commit()

        flash(
            "Product stock updated successfully.",
            "success",
        )

    except pymysql.MySQLError:
        conn.rollback()
        logger.exception("Failed to restock product.")

        flash(
            "Unable to update product stock.",
            "danger",
        )

    finally:
        conn.close()

    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# 4. PRODUCT MANAGEMENT
# ---------------------------------------------------------

@app.route("/products", methods=["GET", "POST"])
def manage_products():
    """Create and display products."""

    conn = get_db_connection()

    try:

        if request.method == "POST":

            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()

            try:
                price = parse_non_negative_decimal(
                    request.form.get("price"),
                    "Price",
                )

                stock = parse_positive_integer(
                    request.form.get("stock_quantity"),
                    "Stock quantity",
                )

            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("manage_products"))

            if not name:
                flash("Product name is required.", "danger")
                return redirect(url_for("manage_products"))

            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO products
                            (name, description, price, stock_quantity)
                        VALUES
                            (%s, %s, %s, %s)
                        """,
                        (
                            name,
                            description,
                            price,
                            stock,
                        ),
                    )

                conn.commit()

                flash(
                    f"Product '{name}' added successfully.",
                    "success",
                )

            except pymysql.MySQLError:
                conn.rollback()
                logger.exception("Failed to create product.")

                flash(
                    "Unable to create the product.",
                    "danger",
                )

            return redirect(url_for("manage_products"))

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM products
                ORDER BY name ASC
                """
            )

            all_products = cursor.fetchall()

        return render_template(
            "products.html",
            products=all_products,
        )

    finally:
        conn.close()


# ---------------------------------------------------------
# 5. CUSTOMER / CRM MANAGEMENT
# ---------------------------------------------------------

@app.route("/customers", methods=["GET", "POST"])
def manage_customers():
    """Create and display customers."""

    conn = get_db_connection()

    try:

        if request.method == "POST":

            first_name = request.form.get(
                "first_name",
                "",
            ).strip()

            last_name = request.form.get(
                "last_name",
                "",
            ).strip()

            email = request.form.get(
                "email",
                "",
            ).strip()

            phone = request.form.get(
                "phone",
                "",
            ).strip()

            address = request.form.get(
                "address",
                "",
            ).strip()

            if not first_name or not last_name or not email:
                flash(
                    "First name, last name, and email are required.",
                    "danger",
                )
                return redirect(url_for("manage_customers"))

            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO customers
                            (
                                first_name,
                                last_name,
                                email,
                                phone,
                                address
                            )
                        VALUES
                            (%s, %s, %s, %s, %s)
                        """,
                        (
                            first_name,
                            last_name,
                            email,
                            phone,
                            address,
                        ),
                    )

                conn.commit()

                flash(
                    f"Customer profile for {first_name} created successfully.",
                    "success",
                )

            except pymysql.err.IntegrityError:
                conn.rollback()

                flash(
                    "Email address is already registered.",
                    "danger",
                )

            except pymysql.MySQLError:
                conn.rollback()
                logger.exception("Failed to create customer.")

                flash(
                    "Unable to create customer profile.",
                    "danger",
                )

            return redirect(url_for("manage_customers"))

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM customers
                ORDER BY last_name ASC
                """
            )

            all_customers = cursor.fetchall()

        return render_template(
            "customers.html",
            customers=all_customers,
        )

    finally:
        conn.close()


# ---------------------------------------------------------
# 6. CREATE ORDER / INVOICE
# ---------------------------------------------------------

@app.route("/orders/create", methods=["GET", "POST"])
def create_order():
    """Create a new order and update product inventory."""

    conn = get_db_connection()

    try:

        if request.method == "POST":

            customer_id = request.form.get("customer_id")
            product_id = request.form.get("product_id")

            try:
                quantity = parse_positive_integer(
                    request.form.get("quantity"),
                    "Quantity",
                )
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("create_order"))

            try:

                with conn.cursor() as cursor:

                    # Check product availability
                    cursor.execute(
                        """
                        SELECT
                            price,
                            stock_quantity
                        FROM products
                        WHERE product_id = %s
                        FOR UPDATE
                        """,
                        (product_id,),
                    )

                    product = cursor.fetchone()

                    if not product:
                        flash(
                            "Selected product does not exist.",
                            "danger",
                        )
                        conn.rollback()
                        return redirect(url_for("create_order"))

                    if product["stock_quantity"] < quantity:
                        flash(
                            "Order rejected: insufficient stock.",
                            "danger",
                        )
                        conn.rollback()
                        return redirect(url_for("create_order"))

                    # Calculate order total
                    total_cost = (
                        Decimal(str(product["price"])) * quantity
                    )

                    # Create order
                    cursor.execute(
                        """
                        INSERT INTO orders
                            (
                                customer_id,
                                total_amount,
                                status
                            )
                        VALUES
                            (%s, %s, 'Pending')
                        """,
                        (
                            customer_id,
                            total_cost,
                        ),
                    )

                    order_id = cursor.lastrowid

                    # Create order item
                    cursor.execute(
                        """
                        INSERT INTO order_items
                            (
                                order_id,
                                product_id,
                                quantity,
                                unit_price
                            )
                        VALUES
                            (%s, %s, %s, %s)
                        """,
                        (
                            order_id,
                            product_id,
                            quantity,
                            product["price"],
                        ),
                    )

                    # Reduce inventory
                    cursor.execute(
                        """
                        UPDATE products
                        SET stock_quantity =
                            stock_quantity - %s
                        WHERE product_id = %s
                        """,
                        (
                            quantity,
                            product_id,
                        ),
                    )

                conn.commit()

                flash(
                    f"Order #{order_id} generated successfully.",
                    "success",
                )

                return redirect(url_for("dashboard"))

            except pymysql.MySQLError:
                conn.rollback()
                logger.exception("Failed to create order.")

                flash(
                    "Unable to create order. No inventory changes were saved.",
                    "danger",
                )

                return redirect(url_for("create_order"))

        # GET request
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id,
                    first_name,
                    last_name
                FROM customers
                ORDER BY last_name ASC
                """
            )

            customers = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    product_id,
                    name,
                    price,
                    stock_quantity
                FROM products
                WHERE stock_quantity > 0
                ORDER BY name ASC
                """
            )

            products = cursor.fetchall()

        return render_template(
            "create_order.html",
            customers=customers,
            products=products,
        )

    finally:
        conn.close()


# ---------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "False").lower() == "true",
    )
