from flask import Flask, render_template, request, redirect, url_for, flash
import pymysql

app = Flask(__name__)
app.secret_key = 'sales_management_secret_key'

def get_db_connection():
    return pymysql.connect(
        host='localhost',
        user='root',         
        password='root',  # <- Replace with your real MySQL password
        database='sales_management',
        cursorclass=pymysql.cursors.DictCursor
    )

# --- 1. ENHANCED DASHBOARD OVERVIEW ---
@app.route('/')
def dashboard():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # KPI 1: Total Revenue
        cursor.execute("SELECT COALESCE(SUM(total_amount), 0) AS revenue FROM orders WHERE status != 'Cancelled'")
        total_revenue = cursor.fetchone()['revenue']
        
        # KPI 2: Active Pending Orders
        cursor.execute("SELECT COUNT(*) AS pending_count FROM orders WHERE status = 'Pending'")
        pending_orders = cursor.fetchone()['pending_count']

        # KPI 3: Total Items Transacted
        cursor.execute("SELECT COALESCE(SUM(quantity), 0) AS units_sold FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.status != 'Cancelled'")
        total_units = cursor.fetchone()['units_sold']

        # KPI 4: Registered Customer Base
        cursor.execute("SELECT COUNT(*) AS clients FROM customers")
        total_clients = cursor.fetchone()['clients']
        
        # Fetch Low Stock items
        cursor.execute("SELECT * FROM products WHERE stock_quantity < 10")
        low_stock_items = cursor.fetchall()
        
        # Fetch Comprehensive Orders Matrix
        query = """
            SELECT o.order_id, o.order_date, o.status, o.total_amount, 
                   CONCAT(c.first_name, ' ', c.last_name) AS customer_name
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            ORDER BY o.order_date DESC
        """
        cursor.execute(query)
        all_orders = cursor.fetchall()
        
    conn.close()
    return render_template('dashboard.html', 
                           revenue=total_revenue, 
                           pending=pending_orders, 
                           units_sold=total_units,
                           clients=total_clients,
                           low_stock=low_stock_items, 
                           orders=all_orders)

# --- 2. UPDATE ORDER LIFECYCLE STATE ---
@app.route('/orders/update-status/<int:order_id>', methods=['POST'])
def update_order_status(order_id):
    new_status = request.form['status']
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (new_status, order_id))
    conn.commit()
    conn.close()
    flash(f"Order #{order_id} status transitioned to '{new_status}' successfully.", 'success')
    return redirect('/')

# --- 3. INSTANT DASHBOARD RESTOCK ENTRY ---
@app.route('/products/restock/<int:product_id>', methods=['POST'])
def restock_product(product_id):
    added_units = int(request.form['added_stock'])
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE products SET stock_quantity = stock_quantity + %s WHERE product_id = %s", (added_units, product_id))
    conn.commit()
    conn.close()
    flash("Warehouse operational supply updated successfully.", 'success')
    return redirect('/')

# --- 4. INVENTORY CONTROL ---
@app.route('/products', methods=['GET', 'POST'])
def manage_products():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form['description'].strip()
        price = request.form['price']
        stock = request.form['stock_quantity']
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO products (name, description, price, stock_quantity) VALUES (%s, %s, %s, %s)", (name, description, price, stock))
        conn.commit()
        flash(f"Product '{name}' added successfully.", 'success')
        return redirect('/products')
        
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM products ORDER BY name ASC")
        all_products = cursor.fetchall()
    conn.close()
    return render_template('products.html', products=all_products)

# --- 5. CRM CONTROL ---
@app.route('/customers', methods=['GET', 'POST'])
def manage_customers():
    conn = get_db_connection()
    if request.method == 'POST':
        first_name = request.form['first_name'].strip()
        last_name = request.form['last_name'].strip()
        email = request.form['email'].strip()
        phone = request.form['phone'].strip()
        address = request.form['address'].strip()
        try:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO customers (first_name, last_name, email, phone, address) VALUES (%s, %s, %s, %s, %s)", (first_name, last_name, email, phone, address))
            conn.commit()
            flash(f"Customer profile for {first_name} generated.", 'success')
        except pymysql.err.IntegrityError:
            flash("Email address already registered.", 'danger')
        return redirect('/customers')
        
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM customers ORDER BY last_name ASC")
        all_customers = cursor.fetchall()
    conn.close()
    return render_template('customers.html', customers=all_customers)

# --- 6. INVOICE SYSTEM ---
@app.route('/orders/create', methods=['GET', 'POST'])
def create_order():
    conn = get_db_connection()
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        product_id = request.form['product_id']
        quantity = int(request.form['quantity'])
        with conn.cursor() as cursor:
            cursor.execute("SELECT price, stock_quantity FROM products WHERE product_id = %s", (product_id,))
            product = cursor.fetchone()
            if not product or product['stock_quantity'] < quantity:
                flash("Order rejection: Insufficient product units available.", 'danger')
                conn.close()
                return redirect('/orders/create')
            total_cost = quantity * product['price']
            cursor.execute("INSERT INTO orders (customer_id, total_amount, status) VALUES (%s, %s, 'Pending')", (customer_id, total_cost))
            order_id = cursor.lastrowid
            cursor.execute("INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (%s, %s, %s, %s)", (order_id, product_id, quantity, product['price']))
            cursor.execute("UPDATE products SET stock_quantity = stock_quantity - %s WHERE product_id = %s", (quantity, product_id))
        conn.commit()
        conn.close()
        flash(f"Order #{order_id} generated.", 'success')
        return redirect('/')
        
    with conn.cursor() as cursor:
        cursor.execute("SELECT customer_id, first_name, last_name FROM customers")
        customers = cursor.fetchall()
        cursor.execute("SELECT product_id, name, price, stock_quantity FROM products WHERE stock_quantity > 0")
        products = cursor.fetchall()
    conn.close()
    return render_template('create_order.html', customers=customers, products=products)

if __name__ == '__main__':
    app.run(debug=True, port=5001)