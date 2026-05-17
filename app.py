import os
import sqlite3
import random
import hashlib
import secrets
import requests
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from authlib.integrations.flask_client import OAuth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'fallback-secret')

ADMIN_EMAIL          = os.getenv('ADMIN_EMAIL', '').lower().strip()
SENDGRID_API_KEY     = os.getenv('SENDGRID_API_KEY', '')
MAIL_FROM            = os.getenv('MAIL_FROM', '')
GOOGLE_CLIENT_ID     = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

# ================= GOOGLE OAUTH =================
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

# ================= DATABASE =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'inventory.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    with get_db() as conn:
        # 1. Stores
        conn.execute('''CREATE TABLE IF NOT EXISTS Stores (
            StoreID       INTEGER PRIMARY KEY AUTOINCREMENT,
            StoreName     VARCHAR NOT NULL,
            Phone         VARCHAR,
            Address       TEXT,
            email         TEXT UNIQUE,
            password_hash TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # 2. Products
        conn.execute('''CREATE TABLE IF NOT EXISTS Products (
            ProductID   INTEGER PRIMARY KEY AUTOINCREMENT,
            QRCode      VARCHAR UNIQUE,
            ProductName VARCHAR NOT NULL,
            StockQty    INTEGER DEFAULT 0,
            UnitPrice   DECIMAL DEFAULT 0,
            StoreID     INTEGER NOT NULL,
            FOREIGN KEY(StoreID) REFERENCES Stores(StoreID)
        )''')

        # 3. Orders
        conn.execute('''CREATE TABLE IF NOT EXISTS Orders (
            OrderID     INTEGER PRIMARY KEY AUTOINCREMENT,
            StoreID     INTEGER NOT NULL,
            OrderTime   DATETIME DEFAULT CURRENT_TIMESTAMP,
            TotalAmount DECIMAL DEFAULT 0,
            OrderStatus VARCHAR DEFAULT 'Pending',
            FOREIGN KEY(StoreID) REFERENCES Stores(StoreID)
        )''')

        # 4. Order_Details
        conn.execute('''CREATE TABLE IF NOT EXISTS Order_Details (
            OrderDetailID INTEGER PRIMARY KEY AUTOINCREMENT,
            OrderID       INTEGER NOT NULL,
            ProductID     INTEGER NOT NULL,
            Quantity      INTEGER NOT NULL,
            SubTotal      DECIMAL NOT NULL,
            FOREIGN KEY(OrderID)    REFERENCES Orders(OrderID),
            FOREIGN KEY(ProductID)  REFERENCES Products(ProductID)
        )''')

        # 5. Payments
        conn.execute('''CREATE TABLE IF NOT EXISTS Payments (
            PaymentID     INTEGER PRIMARY KEY AUTOINCREMENT,
            OrderID       INTEGER UNIQUE NOT NULL,
            PaymentMethod VARCHAR NOT NULL,
            AmountPaid    DECIMAL NOT NULL,
            PaymentTime   DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(OrderID) REFERENCES Orders(OrderID)
        )''')

        # ลบตารางเก่าที่ไม่ใช้แล้ว (migration)
        conn.execute('DROP TABLE IF EXISTS otps')
        conn.execute('DROP TABLE IF EXISTS reset_tokens')
        conn.commit()

# ---- Seed mock data (ตารางละ 10 records) ----
def seed_mock_data(store_id):
    with get_db() as conn:
        if conn.execute('SELECT COUNT(*) as c FROM Products WHERE StoreID=?', (store_id,)).fetchone()['c'] > 0:
            return

        # ---- Products (10 records) ----
        products_data = [
            ('8850006100084', 'โค้ก 325ml',        50,  20.0),
            ('8850006100085', 'เป๊ปซี่ 325ml',      40,  20.0),
            ('8850718111014', 'มันฝรั่ง Lays 44g',  60,  25.0),
            ('8850111222333', 'สบู่ Safeguard 80g', 30,  35.0),
            ('8850002111001', 'มาม่า รสหมู 55g',   100,   7.0),
            ('8850006200001', 'นม Dutch Mill 200ml', 45,  15.0),
            ('8850333444555', 'ขนมปังแซนวิช',        20,  30.0),
            ('8850444555666', 'น้ำยาล้างจาน Sunlight 500ml', 25, 55.0),
            ('8850555666777', 'วิตามินซี 1000mg',   15, 120.0),
            ('8850666777888', 'ปากกา Pilot 0.5mm',  80,  15.0),
        ]
        conn.executemany(
            'INSERT OR IGNORE INTO Products (QRCode, ProductName, StockQty, UnitPrice, StoreID) VALUES (?,?,?,?,?)',
            [(qr, name, qty, price, store_id) for qr, name, qty, price in products_data]
        )

        prod_rows = conn.execute(
            'SELECT ProductID, QRCode, ProductName, UnitPrice FROM Products WHERE StoreID=? ORDER BY ProductID',
            (store_id,)
        ).fetchall()

        # ---- Orders + Order_Details + Payments (10 records each) ----
        methods = ['Cash', 'QR PromptPay']
        statuses = ['Paid', 'Paid', 'Paid', 'Paid', 'Paid', 'Paid', 'Paid', 'Paid', 'Cancelled', 'Pending']

        for i in range(10):
            order_time = datetime.now() - timedelta(days=i * 2, hours=random.randint(0, 8))
            status = statuses[i]

            # สุ่มสินค้า 1-3 รายการต่อบิล
            if not prod_rows: break
            chosen = random.sample(prod_rows, k=random.randint(1, min(3, len(prod_rows))))
            total_amount = sum(p['UnitPrice'] * random.randint(1, 4) for p in chosen)

            order_id = conn.execute(
                'INSERT INTO Orders (StoreID, OrderTime, TotalAmount, OrderStatus) VALUES (?,?,?,?)',
                (store_id, order_time, round(total_amount, 2), status)
            ).lastrowid

            # Order_Details
            for prod in chosen:
                qty = random.randint(1, 4)
                subtotal = round(prod['UnitPrice'] * qty, 2)
                conn.execute(
                    'INSERT INTO Order_Details (OrderID, ProductID, Quantity, SubTotal) VALUES (?,?,?,?)',
                    (order_id, prod['ProductID'], qty, subtotal)
                )

            # Payments — เฉพาะบิลที่ Paid
            if status == 'Paid':
                conn.execute(
                    'INSERT INTO Payments (OrderID, PaymentMethod, AmountPaid, PaymentTime) VALUES (?,?,?,?)',
                    (order_id, random.choice(methods), round(total_amount, 2), order_time + timedelta(minutes=random.randint(1, 10)))
                )

        conn.commit()

init_db()

# ================= STORE ROUTES =================
@app.route('/')
def index():
    if 'store_id' in session: return redirect('/dashboard')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'store_id' not in session: return redirect('/')
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.pop('store_id', None); session.pop('email', None)
    return redirect('/')

# ================= AUTH API (Register / Login) =================
def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    email    = (d.get('email') or '').strip().lower()
    password = (d.get('password') or '').strip()
    if not email or not password:
        return jsonify({'error': 'กรุณากรอกอีเมลและรหัสผ่าน'}), 400
    if len(password) < 6:
        return jsonify({'error': 'รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร'}), 400
    with get_db() as conn:
        if conn.execute('SELECT StoreID FROM Stores WHERE email=?', (email,)).fetchone():
            return jsonify({'error': 'อีเมลนี้มีบัญชีอยู่แล้ว กรุณาเข้าสู่ระบบ'}), 400
        store_id = conn.execute(
            'INSERT INTO Stores (StoreName, Phone, Address, email, password_hash) VALUES (?,?,?,?,?)',
            (email.split('@')[0], '', '', email, hash_password(password))
        ).lastrowid
        conn.commit()
    session['store_id'] = store_id; session['email'] = email
    seed_mock_data(store_id)
    return jsonify({'message': 'สมัครสำเร็จ', 'is_new_store': True})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json
    email    = (d.get('email') or '').strip().lower()
    password = (d.get('password') or '').strip()
    if not email or not password:
        return jsonify({'error': 'กรุณากรอกอีเมลและรหัสผ่าน'}), 400
    with get_db() as conn:
        store = conn.execute('SELECT * FROM Stores WHERE email=?', (email,)).fetchone()
        if not store:
            return jsonify({'error': 'ไม่พบบัญชีนี้ กรุณาสมัครใหม่'}), 400
        if store['password_hash'] != hash_password(password):
            return jsonify({'error': 'รหัสผ่านไม่ถูกต้อง'}), 400
        session['store_id'] = store['StoreID']; session['email'] = email
        # ถ้าชื่อร้านยังเป็น default (email prefix) = ยังไม่ได้กรอก
        is_new = not store['StoreName'] or store['StoreName'] == email.split('@')[0]
    return jsonify({'message': 'เข้าสู่ระบบสำเร็จ', 'is_new_store': is_new})

@app.route('/api/update-store-info', methods=['POST'])
def update_store_info():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    d = request.json
    name = (d.get('store_name') or '').strip()
    if not name: return jsonify({'error': 'กรุณาระบุชื่อร้านค้า'}), 400
    with get_db() as conn:
        conn.execute(
            'UPDATE Stores SET StoreName=?, Phone=?, Address=? WHERE StoreID=?',
            (name, d.get('phone', ''), d.get('address', ''), store_id)
        )
        conn.commit()
    return jsonify({'message': 'บันทึกข้อมูลร้านค้าสำเร็จ'})


# ================= PRODUCTS API =================
@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            rows = conn.execute(
                'SELECT * FROM Products WHERE StoreID=? ORDER BY ProductName', (store_id,)
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        d = request.json
        ex = conn.execute(
            'SELECT * FROM Products WHERE StoreID=? AND QRCode=?', (store_id, d.get('QRCode'))
        ).fetchone()
        if ex:
            conn.execute(
                'UPDATE Products SET ProductName=?, StockQty=StockQty+?, UnitPrice=? WHERE ProductID=?',
                (d['ProductName'], int(d.get('StockQty', 0)), float(d.get('UnitPrice', 0)), ex['ProductID'])
            )
        else:
            conn.execute(
                'INSERT INTO Products (QRCode, ProductName, StockQty, UnitPrice, StoreID) VALUES (?,?,?,?,?)',
                (d.get('QRCode'), d['ProductName'], int(d.get('StockQty', 0)), float(d.get('UnitPrice', 0)), store_id)
            )
        conn.commit(); return jsonify({'message': 'บันทึกสำเร็จ'})

@app.route('/api/products/<int:pid>', methods=['PUT', 'DELETE'])
def handle_product_detail(pid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'DELETE':
            conn.execute('DELETE FROM Products WHERE ProductID=? AND StoreID=?', (pid, store_id))
            conn.commit(); return jsonify({'message': 'ลบสำเร็จ'})
        d = request.json
        conn.execute(
            'UPDATE Products SET ProductName=?, UnitPrice=?, StockQty=? WHERE ProductID=? AND StoreID=?',
            (d['ProductName'], d['UnitPrice'], d['StockQty'], pid, store_id)
        )
        conn.commit(); return jsonify({'message': 'อัปเดตสำเร็จ'})

# ================= CHECKOUT (Orders + Order_Details + Payments) =================
@app.route('/api/checkout', methods=['POST'])
def checkout():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    d = request.json  # { items:[{QRCode, ProductName, qty, price}], payment_method }
    with get_db() as conn:
        method = d.get('payment_method', 'Cash')
        is_qr  = method == 'QR PromptPay'
        status = 'Pending' if is_qr else 'Paid'
        total  = sum(i['qty'] * i['price'] for i in d['items'])

        order_id = conn.execute(
            'INSERT INTO Orders (StoreID, OrderTime, TotalAmount, OrderStatus) VALUES (?,?,?,?)',
            (store_id, datetime.now(), round(total, 2), status)
        ).lastrowid

        for item in d['items']:
            prod = conn.execute(
                'SELECT ProductID FROM Products WHERE StoreID=? AND QRCode=?', (store_id, item['QRCode'])
            ).fetchone()
            if prod:
                conn.execute(
                    'UPDATE Products SET StockQty=StockQty-? WHERE ProductID=? AND StoreID=?',
                    (item['qty'], prod['ProductID'], store_id)
                )
                conn.execute(
                    'INSERT INTO Order_Details (OrderID, ProductID, Quantity, SubTotal) VALUES (?,?,?,?)',
                    (order_id, prod['ProductID'], item['qty'], round(item['qty'] * item['price'], 2))
                )

        # เงินสด → บันทึก Payment ทันที, QR → รอยืนยัน
        if not is_qr:
            conn.execute(
                'INSERT INTO Payments (OrderID, PaymentMethod, AmountPaid, PaymentTime) VALUES (?,?,?,?)',
                (order_id, method, round(total, 2), datetime.now())
            )
        conn.commit()
    return jsonify({'message': 'ขายสำเร็จ', 'order_id': order_id, 'total_amount': round(total, 2), 'status': status})

# ================= DASHBOARD SUMMARY =================
@app.route('/api/dashboard-summary')
def dashboard_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        today_sales = conn.execute(
            "SELECT COALESCE(SUM(TotalAmount),0) as v FROM Orders WHERE StoreID=? AND OrderStatus='Paid' AND strftime('%Y-%m-%d',OrderTime)=?",
            (store_id, today)
        ).fetchone()['v']
        today_txn = conn.execute(
            "SELECT COUNT(*) as c FROM Orders WHERE StoreID=? AND OrderStatus='Paid' AND strftime('%Y-%m-%d',OrderTime)=?",
            (store_id, today)
        ).fetchone()['c']
        low_stock = conn.execute(
            'SELECT COUNT(*) as c FROM Products WHERE StoreID=? AND StockQty<=5', (store_id,)
        ).fetchone()['c']
        total_products = conn.execute(
            'SELECT COUNT(*) as c FROM Products WHERE StoreID=?', (store_id,)
        ).fetchone()['c']
        top_products = conn.execute(
            '''SELECT p.ProductName, SUM(od.Quantity) as sold
               FROM Order_Details od
               JOIN Products p ON od.ProductID=p.ProductID
               JOIN Orders o ON od.OrderID=o.OrderID
               WHERE p.StoreID=? AND o.OrderStatus='Paid'
               GROUP BY p.ProductID ORDER BY sold DESC LIMIT 5''', (store_id,)
        ).fetchall()
        low_items = conn.execute(
            'SELECT ProductName as name, StockQty as quantity FROM Products WHERE StoreID=? AND StockQty<=5 ORDER BY StockQty ASC LIMIT 5',
            (store_id,)
        ).fetchall()
        return jsonify({
            'today_sales':    today_sales,
            'today_txn':      today_txn,
            'low_stock':      low_stock,
            'total_products': total_products,
            'top_products':   [dict(r) for r in top_products],
            'low_items':      [dict(r) for r in low_items],
        })

# ================= SALES SUMMARY (รายงาน) =================
@app.route('/api/sales-summary')
def get_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    mode  = request.args.get('mode', 'daily')
    from_ = request.args.get('from', '')
    to_   = request.args.get('to', '')
    month = request.args.get('month', '')
    fmt   = '%Y-%m-%d' if mode == 'daily' else '%Y-%m'
    with get_db() as conn:
        if mode == 'daily' and from_ and to_:
            res = conn.execute(
                f"SELECT strftime('{fmt}',OrderTime) as label, SUM(TotalAmount) as total, COUNT(*) as txn_count "
                f"FROM Orders WHERE StoreID=? AND OrderStatus='Paid' "
                f"AND date(OrderTime) BETWEEN ? AND ? GROUP BY label ORDER BY label ASC",
                (store_id, from_, to_)
            ).fetchall()
        elif mode == 'monthly' and month:
            res = conn.execute(
                f"SELECT strftime('{fmt}',OrderTime) as label, SUM(TotalAmount) as total, COUNT(*) as txn_count "
                f"FROM Orders WHERE StoreID=? AND OrderStatus='Paid' "
                f"AND strftime('%Y-%m',OrderTime)=? GROUP BY label ORDER BY label ASC",
                (store_id, month)
            ).fetchall()
        else:
            res = conn.execute(
                f"SELECT strftime('{fmt}',OrderTime) as label, SUM(TotalAmount) as total, COUNT(*) as txn_count "
                f"FROM Orders WHERE StoreID=? AND OrderStatus='Paid' GROUP BY label ORDER BY label DESC LIMIT 30",
                (store_id,)
            ).fetchall()
        return jsonify([dict(r) for r in res])

# ================= SALES DETAILS (บิลรายวัน) =================
@app.route('/api/sales-details')
def get_sales_details():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    date_label = request.args.get('date')
    with get_db() as conn:
        orders = conn.execute(
            "SELECT o.OrderID, o.TotalAmount, o.OrderTime, p.PaymentMethod "
            "FROM Orders o LEFT JOIN Payments p ON o.OrderID=p.OrderID "
            "WHERE o.StoreID=? AND o.OrderStatus='Paid' AND strftime('%Y-%m-%d',o.OrderTime)=? "
            "ORDER BY o.OrderTime DESC",
            (store_id, date_label)
        ).fetchall()
        result = {}
        for o in orders:
            oid = o['OrderID']
            items = conn.execute(
                '''SELECT pr.ProductName, od.Quantity, pr.UnitPrice, od.SubTotal
                   FROM Order_Details od JOIN Products pr ON od.ProductID=pr.ProductID
                   WHERE od.OrderID=?''', (oid,)
            ).fetchall()
            result[f'ORDER-{oid}'] = {
                'time': o['OrderTime'][11:19],
                'payment_method': o['PaymentMethod'] or '-',
                'grand_total': o['TotalAmount'],
                'items': [{'product_name': i['ProductName'], 'quantity': i['Quantity'],
                           'price_per_unit': i['UnitPrice'], 'total_price': i['SubTotal']} for i in items]
            }
        return jsonify(result)

# ================= FINANCIAL SUMMARY =================
@app.route('/api/financial-summary')
def financial_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    with get_db() as conn:
        income = conn.execute(
            "SELECT COALESCE(SUM(TotalAmount),0) as v FROM Orders WHERE StoreID=? AND OrderStatus='Paid' AND strftime('%Y-%m',OrderTime)=?",
            (store_id, month)
        ).fetchone()['v']
        cancelled = conn.execute(
            "SELECT COUNT(*) as c FROM Orders WHERE StoreID=? AND OrderStatus='Cancelled' AND strftime('%Y-%m',OrderTime)=?",
            (store_id, month)
        ).fetchone()['c']
        total_orders = conn.execute(
            "SELECT COUNT(*) as c FROM Orders WHERE StoreID=? AND strftime('%Y-%m',OrderTime)=?",
            (store_id, month)
        ).fetchone()['c']
        by_method = conn.execute(
            "SELECT p.PaymentMethod, SUM(p.AmountPaid) as total FROM Payments p "
            "JOIN Orders o ON p.OrderID=o.OrderID "
            "WHERE o.StoreID=? AND strftime('%Y-%m',p.PaymentTime)=? GROUP BY p.PaymentMethod",
            (store_id, month)
        ).fetchall()
        return jsonify({
            'month': month,
            'total_income': income,
            'total_orders': total_orders,
            'cancelled_orders': cancelled,
            'net_profit': income,
            'by_method': [dict(r) for r in by_method],
        })

# ================= ORDERS API =================
@app.route('/api/orders')
def get_orders():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT o.*, p.PaymentMethod, p.AmountPaid, p.PaymentTime
               FROM Orders o LEFT JOIN Payments p ON o.OrderID=p.OrderID
               WHERE o.StoreID=? ORDER BY o.OrderTime DESC LIMIT 50''',
            (store_id,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/orders/<int:oid>/cancel', methods=['POST'])
def cancel_order(oid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        order = conn.execute('SELECT * FROM Orders WHERE OrderID=? AND StoreID=?', (oid, store_id)).fetchone()
        if not order: return jsonify({'error': 'ไม่พบออร์เดอร์'}), 404
        if order['OrderStatus'] != 'Pending':
            return jsonify({'error': 'ยกเลิกได้เฉพาะออร์เดอร์ที่ยังไม่ชำระ'}), 400
        conn.execute("UPDATE Orders SET OrderStatus='Cancelled' WHERE OrderID=?", (oid,))
        conn.commit()
    return jsonify({'message': 'ยกเลิกสำเร็จ'})

@app.route('/api/orders/<int:oid>/pay', methods=['POST'])
def confirm_order_paid(oid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        order = conn.execute('SELECT * FROM Orders WHERE OrderID=? AND StoreID=?', (oid, store_id)).fetchone()
        if not order: return jsonify({'error': 'ไม่พบออร์เดอร์'}), 404
        if order['OrderStatus'] != 'Pending':
            return jsonify({'error': 'ออร์เดอร์นี้ไม่ได้รอชำระ'}), 400
        conn.execute("UPDATE Orders SET OrderStatus='Paid' WHERE OrderID=?", (oid,))
        conn.execute(
            'INSERT INTO Payments (OrderID, PaymentMethod, AmountPaid, PaymentTime) VALUES (?,?,?,?)',
            (oid, 'QR PromptPay', order['TotalAmount'], datetime.now())
        )
        conn.commit()
    return jsonify({'message': 'ยืนยันการชำระสำเร็จ'})

# ================= ADMIN: Google OAuth =================
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/login')
def admin_login():
    if session.get('is_admin'): return redirect('/admin')
    return render_template('admin_login.html', error=None)

@app.route('/admin/login/google')
def admin_login_google():
    redirect_uri = url_for('admin_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/admin/callback')
def admin_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            resp = google.get('https://openidconnect.googleapis.com/v1/userinfo')
            user_info = resp.json()
        email = user_info.get('email', '').lower().strip()
        if email != ADMIN_EMAIL:
            return render_template('admin_login.html',
                error=f'อีเมล {email} ไม่มีสิทธิ์เข้าใช้งาน Admin Panel')
        session['is_admin']      = True
        session['admin_email']   = email
        session['admin_name']    = user_info.get('name', email)
        session['admin_picture'] = user_info.get('picture', '')
        return redirect('/admin')
    except Exception as e:
        print(f'OAuth Error: {e}')
        return render_template('admin_login.html', error='เกิดข้อผิดพลาดในการเข้าสู่ระบบ กรุณาลองใหม่')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None); session.pop('admin_email', None)
    session.pop('admin_name', None); session.pop('admin_picture', None)
    return redirect('/admin/login')

@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('admin.html',
        admin_name    = session.get('admin_name', 'Admin'),
        admin_email   = session.get('admin_email', ''),
        admin_picture = session.get('admin_picture', ''),
    )

# ================= ADMIN API =================
@app.route('/admin/api/overview')
@admin_required
def admin_overview():
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        return jsonify({
            'total_stores':   conn.execute('SELECT COUNT(*) as c FROM Stores WHERE email IS NOT NULL').fetchone()['c'],
            'total_sales':    conn.execute("SELECT COALESCE(SUM(TotalAmount),0) as v FROM Orders WHERE OrderStatus='Paid'").fetchone()['v'],
            'total_products': conn.execute('SELECT COUNT(*) as c FROM Products').fetchone()['c'],
            'total_orders':   conn.execute('SELECT COUNT(*) as c FROM Orders').fetchone()['c'],
            'today_sales':    conn.execute(
                "SELECT COALESCE(SUM(TotalAmount),0) as v FROM Orders WHERE OrderStatus='Paid' AND strftime('%Y-%m-%d',OrderTime)=?",
                (today,)
            ).fetchone()['v'],
            'sales_by_day':   [dict(r) for r in conn.execute(
                "SELECT strftime('%Y-%m-%d',OrderTime) as label, SUM(TotalAmount) as total "
                "FROM Orders WHERE OrderStatus='Paid' GROUP BY label ORDER BY label DESC LIMIT 14"
            ).fetchall()],
            'top_stores':     [dict(r) for r in conn.execute(
                '''SELECT s.StoreName, s.email,
                   COALESCE(SUM(o.TotalAmount),0) as revenue,
                   COUNT(DISTINCT o.OrderID) as txn
                   FROM Stores s LEFT JOIN Orders o ON s.StoreID=o.StoreID AND o.OrderStatus='Paid'
                   WHERE s.email IS NOT NULL
                   GROUP BY s.StoreID ORDER BY revenue DESC LIMIT 10'''
            ).fetchall()],
        })

@app.route('/admin/api/stores')
@admin_required
def admin_stores():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT s.StoreID as id, s.StoreName, s.Phone, s.Address, s.email, s.created_at,
                COUNT(DISTINCT p.ProductID) as product_count,
                COUNT(DISTINCT o.OrderID) as txn_count,
                COALESCE(SUM(CASE WHEN o.OrderStatus='Paid' THEN o.TotalAmount ELSE 0 END),0) as revenue
            FROM Stores s
            LEFT JOIN Products p ON s.StoreID=p.StoreID
            LEFT JOIN Orders o ON s.StoreID=o.StoreID
            WHERE s.email IS NOT NULL
            GROUP BY s.StoreID ORDER BY s.created_at DESC
        ''').fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/admin/api/stores/<int:store_id>', methods=['PUT'])
@admin_required
def admin_edit_store(store_id):
    d = request.json
    name = (d.get('store_name') or '').strip()
    if not name: return jsonify({'error': 'กรุณาระบุชื่อร้านค้า'}), 400
    with get_db() as conn:
        store = conn.execute('SELECT * FROM Stores WHERE StoreID=?', (store_id,)).fetchone()
        if not store: return jsonify({'error': 'ไม่พบร้านค้า'}), 404
        if d.get('password'):
            if len(d['password']) < 6:
                return jsonify({'error': 'รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร'}), 400
            conn.execute(
                'UPDATE Stores SET StoreName=?, Phone=?, Address=?, password_hash=? WHERE StoreID=?',
                (name, d.get('phone',''), d.get('address',''), hash_password(d['password']), store_id)
            )
        else:
            conn.execute(
                'UPDATE Stores SET StoreName=?, Phone=?, Address=? WHERE StoreID=?',
                (name, d.get('phone',''), d.get('address',''), store_id)
            )
        conn.commit()
    return jsonify({'message': 'อัปเดตสำเร็จ'})

@app.route('/admin/api/stores/<int:store_id>', methods=['DELETE'])
@admin_required
def admin_delete_store(store_id):
    with get_db() as conn:
        conn.execute('DELETE FROM Payments WHERE OrderID IN (SELECT OrderID FROM Orders WHERE StoreID=?)', (store_id,))
        conn.execute('DELETE FROM Order_Details WHERE OrderID IN (SELECT OrderID FROM Orders WHERE StoreID=?)', (store_id,))
        conn.execute('DELETE FROM Orders WHERE StoreID=?', (store_id,))
        conn.execute('DELETE FROM Products WHERE StoreID=?', (store_id,))
        conn.execute('DELETE FROM Stores WHERE StoreID=?', (store_id,))
        conn.commit()
    return jsonify({'message': 'ลบร้านค้าสำเร็จ'})

@app.route('/admin/api/sql', methods=['POST'])
@admin_required
def admin_sql():
    query = request.json.get('query', '').strip()
    if not query: return jsonify({'error': 'กรุณาระบุ SQL Query'}), 400
    try:
        with get_db() as conn:
            cursor = conn.execute(query)
            if query.upper().lstrip().startswith(('SELECT', 'PRAGMA')):
                rows = cursor.fetchall()
                if not rows: return jsonify({'columns': [], 'rows': [], 'message': 'ไม่มีข้อมูล', 'affected': 0})
                return jsonify({'columns': list(rows[0].keys()), 'rows': [list(r) for r in rows], 'affected': len(rows), 'message': ''})
            conn.commit()
            return jsonify({'columns': [], 'rows': [], 'message': f'สำเร็จ: {cursor.rowcount} แถวถูกเปลี่ยนแปลง', 'affected': cursor.rowcount})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/admin/api/tables')
@admin_required
def admin_tables():
    with get_db() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return jsonify({t['name']: conn.execute(f"SELECT COUNT(*) as c FROM '{t['name']}'").fetchone()['c'] for t in tables})

@app.route('/report/pdf')
def report_pdf():
    store_id = session.get('store_id')
    if not store_id: return redirect('/')
    from_ = request.args.get('from', '')
    to_   = request.args.get('to', '')
    rtype = request.args.get('type', 'summary')
    if not from_ or not to_:
        return 'กรุณาระบุวันที่', 400
    with get_db() as conn:
        store = conn.execute('SELECT * FROM Stores WHERE StoreID=?', (store_id,)).fetchone()
        store_name = store['StoreName'] if store else 'ร้านค้า'
        store_phone = store['Phone'] or ''
        store_address = store['Address'] or ''

        # ดึงยอดรายวัน
        daily = conn.execute(
            "SELECT strftime('%Y-%m-%d',OrderTime) as date, "
            "COUNT(*) as txn_count, SUM(TotalAmount) as total "
            "FROM Orders WHERE StoreID=? AND OrderStatus='Paid' "
            "AND date(OrderTime) BETWEEN ? AND ? "
            "GROUP BY date ORDER BY date ASC",
            (store_id, from_, to_)
        ).fetchall()

        grand_total = sum(r['total'] for r in daily)
        grand_txn   = sum(r['txn_count'] for r in daily)

        # ถ้า detail ดึงบิลแต่ละใบด้วย
        orders_detail = []
        if rtype == 'detail':
            orders = conn.execute(
                "SELECT o.OrderID, o.OrderTime, o.TotalAmount, p.PaymentMethod "
                "FROM Orders o LEFT JOIN Payments p ON o.OrderID=p.OrderID "
                "WHERE o.StoreID=? AND o.OrderStatus='Paid' "
                "AND date(o.OrderTime) BETWEEN ? AND ? "
                "ORDER BY o.OrderTime ASC",
                (store_id, from_, to_)
            ).fetchall()
            for o in orders:
                items = conn.execute(
                    "SELECT pr.ProductName, od.Quantity, od.SubTotal "
                    "FROM Order_Details od JOIN Products pr ON od.ProductID=pr.ProductID "
                    "WHERE od.OrderID=?", (o['OrderID'],)
                ).fetchall()
                orders_detail.append({'order': dict(o), 'items': [dict(i) for i in items]})

    # สร้าง HTML
    daily_rows = ''.join(f"""
        <tr>
            <td>{r['date']}</td>
            <td class="center">{r['txn_count']}</td>
            <td class="right">฿{r['total']:,.2f}</td>
        </tr>""" for r in daily)

    detail_html = ''
    if rtype == 'detail' and orders_detail:
        detail_html = '<div class="page-break"></div><h2>รายละเอียดทุกบิล</h2>'
        for od in orders_detail:
            o = od['order']
            detail_html += f"""
            <div class="receipt-block">
                <div class="receipt-header">
                    <span>บิล #{o['OrderID']} — {o['OrderTime'][:16]}</span>
                    <span>💳 {o['PaymentMethod'] or '-'}</span>
                </div>
                <table class="inner-table">
                    {''.join(f"<tr><td>{i['ProductName']}</td><td class='center'>×{i['Quantity']}</td><td class='right'>฿{i['SubTotal']:,.2f}</td></tr>" for i in od['items'])}
                    <tr class='total-row'><td colspan='2'><b>รวม</b></td><td class='right'><b>฿{o['TotalAmount']:,.2f}</b></td></tr>
                </table>
            </div>"""

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<title>รายงานยอดขาย — {store_name}</title>
<style>
    @page {{ size: A4; margin: 20mm 15mm; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Sarabun', 'Kanit', sans-serif; font-size: 13px; color: #1a1a2e; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .no-print {{ display: block; }}
    @media print {{ .no-print {{ display: none !important; }} .page-break {{ page-break-before: always; }} }}
    /* Print button */
    .print-btn {{ position: fixed; bottom: 24px; right: 24px; background: #7c3aed; color: white; border: none; border-radius: 14px; padding: 14px 28px; font-size: 15px; font-weight: 700; cursor: pointer; box-shadow: 0 8px 24px rgba(124,92,252,.4); z-index: 999; }}
    /* Header */
    .doc-header {{ border-bottom: 3px solid #7c3aed; padding-bottom: 14px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: flex-start; }}
    .store-name {{ font-size: 22px; font-weight: 900; color: #7c3aed; }}
    .store-sub {{ font-size: 11px; color: #6b7280; margin-top: 3px; }}
    .report-title {{ text-align: right; }}
    .report-title h1 {{ font-size: 16px; font-weight: 900; color: #1a1a2e; }}
    .report-title p {{ font-size: 11px; color: #6b7280; margin-top: 2px; }}
    /* Summary cards */
    .summary-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
    .summary-card {{ background: #f5f3ff; border-radius: 10px; padding: 12px 14px; text-align: center; border: 1px solid #e9e5ff; }}
    .summary-card .label {{ font-size: 10px; color: #8b7cf8; font-weight: 700; text-transform: uppercase; }}
    .summary-card .value {{ font-size: 18px; font-weight: 900; color: #7c3aed; margin-top: 4px; }}
    /* Table */
    h2 {{ font-size: 14px; font-weight: 900; color: #1a1a2e; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
    thead tr {{ background: #7c3aed; color: white; }}
    th {{ padding: 9px 12px; font-size: 11px; font-weight: 700; text-align: left; }}
    td {{ padding: 8px 12px; font-size: 12px; border-bottom: 1px solid #f3f4f6; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
    .center {{ text-align: center; }}
    .right {{ text-align: right; }}
    .total-row td {{ background: #f5f3ff !important; font-weight: 700; border-top: 2px solid #7c3aed; }}
    /* Footer */
    .doc-footer {{ margin-top: 20px; padding-top: 10px; border-top: 1px solid #e5e7eb; display: flex; justify-content: space-between; font-size: 10px; color: #9ca3af; }}
    /* Detail */
    .receipt-block {{ margin-bottom: 12px; break-inside: avoid; }}
    .receipt-header {{ background: #f5f3ff; padding: 7px 12px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; color: #7c3aed; }}
    .inner-table {{ width: 100%; border-collapse: collapse; border: 1px solid #e9e5ff; border-top: none; border-radius: 0 0 8px 8px; overflow: hidden; }}
    .inner-table td {{ padding: 6px 12px; font-size: 12px; border-bottom: 1px solid #f3f4f6; }}
</style>
</head>
<body>
<button class="print-btn no-print" onclick="window.print()">🖨️ พิมพ์ / บันทึก PDF</button>

<div class="doc-header">
    <div>
        <div class="store-name">🏪 {store_name}</div>
        <div class="store-sub">{store_phone}{(' | ' + store_address) if store_address else ''}</div>
    </div>
    <div class="report-title">
        <h1>รายงานยอดขาย</h1>
        <p>{'สรุปรายวัน' if rtype == 'summary' else 'รายละเอียดทุกบิล'}</p>
        <p>ช่วง: {from_} ถึง {to_}</p>
    </div>
</div>

<div class="summary-grid">
    <div class="summary-card">
        <div class="label">รายได้รวม</div>
        <div class="value">฿{grand_total:,.0f}</div>
    </div>
    <div class="summary-card">
        <div class="label">จำนวนออร์เดอร์</div>
        <div class="value">{grand_txn}</div>
    </div>
    <div class="summary-card">
        <div class="label">เฉลี่ย/บิล</div>
        <div class="value">฿{(grand_total/grand_txn if grand_txn else 0):,.0f}</div>
    </div>
</div>

<h2>สรุปยอดขายรายวัน</h2>
<table>
    <thead>
        <tr><th>วันที่</th><th class="center">จำนวนออร์เดอร์</th><th class="right">ยอดขาย</th></tr>
    </thead>
    <tbody>
        {daily_rows}
        <tr class="total-row">
            <td><b>รวมทั้งหมด</b></td>
            <td class="center"><b>{grand_txn}</b></td>
            <td class="right"><b>฿{grand_total:,.2f}</b></td>
        </tr>
    </tbody>
</table>

{detail_html}

<div class="doc-footer">
    <span>สร้างเมื่อ: {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
    <span>Stock Manager — {store_name}</span>
</div>

<script>
    // auto print เมื่อโหลดหน้า (optional ให้ user เลือกเอง)
    // window.onload = () => setTimeout(() => window.print(), 500);
</script>
</body>
</html>"""
    return html

if __name__ == '__main__':
    app.run(debug=True)