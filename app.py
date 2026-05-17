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
SENDGRID_API_KEY     = os.getenv('SENDGRID_API_KEY', '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')
MAIL_FROM            = os.getenv('MAIL_FROM', 'konpob777@gmail.com')
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
        # OTP table (auth helper — ไม่ใช่ตารางหลักของธุรกิจ)
        conn.execute('''CREATE TABLE IF NOT EXISTS otps (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            expires_at DATETIME NOT NULL
        )''')

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

        conn.execute('''CREATE TABLE IF NOT EXISTS reset_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            expires_at DATETIME NOT NULL
        )''')
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

# ================= FORGOT / RESET PASSWORD =================
def send_reset_email(to_email, reset_link):
    try:
        payload = {
            'personalizations': [{'to': [{'email': to_email}]}],
            'from': {'email': MAIL_FROM, 'name': 'Stock Manager'},
            'subject': 'รีเซ็ตรหัสผ่าน — Stock Manager',
            'content': [{
                'type': 'text/html',
                'value': f'''
                <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;background:#f8f9fe;border-radius:16px">
                    <h2 style="color:#7c5cfc;margin-bottom:8px">🔑 รีเซ็ตรหัสผ่าน</h2>
                    <p style="color:#444;line-height:1.6">คุณได้ขอรีเซ็ตรหัสผ่านสำหรับระบบจัดการร้านค้า</p>
                    <a href="{reset_link}" style="display:inline-block;margin:20px 0;padding:14px 28px;background:linear-gradient(135deg,#7c5cfc,#a78bfa);color:white;text-decoration:none;border-radius:12px;font-weight:bold">
                        ตั้งรหัสผ่านใหม่
                    </a>
                    <p style="color:#888;font-size:13px">ลิงก์หมดอายุใน 30 นาที<br>หากคุณไม่ได้ขอรีเซ็ต กรุณาเพิกเฉยต่ออีเมลนี้</p>
                </div>'''
            }]
        }
        res = requests.post(
            'https://api.sendgrid.com/v3/mail/send',
            json=payload,
            headers={'Authorization': f'Bearer {SENDGRID_API_KEY}', 'Content-Type': 'application/json'},
            timeout=10
        )
        if res.status_code not in (200, 202):
            print(f'SendGrid Error: {res.status_code} {res.text}')
            return False
        return True
    except Exception as e:
        print(f'Mail Error: {e}')
        return False

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    email = (request.json.get('email') or '').strip().lower()
    if not email: return jsonify({'error': 'กรุณากรอกอีเมล'}), 400
    with get_db() as conn:
        store = conn.execute('SELECT StoreID FROM Stores WHERE email=?', (email,)).fetchone()
        if not store:
            # ไม่บอกว่าอีเมลไม่มีในระบบ (security)
            return jsonify({'message': 'ส่งลิงก์รีเซ็ตแล้ว (ถ้าอีเมลนี้มีในระบบ)'}), 200
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=30)
        conn.execute('DELETE FROM reset_tokens WHERE email=?', (email,))
        conn.execute('INSERT INTO reset_tokens (token, email, expires_at) VALUES (?,?,?)',
                     (token, email, expires_at))
        conn.commit()
    reset_link = url_for('reset_password_page', token=token, _external=True)
    ok = send_reset_email(email, reset_link)
    if not ok:
        return jsonify({'error': 'ไม่สามารถส่งอีเมลได้ กรุณาลองใหม่'}), 500
    return jsonify({'message': 'ส่งลิงก์รีเซ็ตแล้ว'})

@app.route('/api/verify-reset-token')
def verify_reset_token():
    token = request.args.get('token', '')
    with get_db() as conn:
        rec = conn.execute('SELECT * FROM reset_tokens WHERE token=?', (token,)).fetchone()
        if not rec: return jsonify({'error': 'token ไม่ถูกต้อง'}), 400
        exp = datetime.strptime(rec['expires_at'].split('.')[0], '%Y-%m-%d %H:%M:%S')
        if exp < datetime.now():
            conn.execute('DELETE FROM reset_tokens WHERE token=?', (token,))
            conn.commit()
            return jsonify({'error': 'ลิงก์หมดอายุแล้ว'}), 400
    return jsonify({'email': rec['email']})

@app.route('/api/reset-password', methods=['POST'])
def do_reset_password():
    d = request.json
    token    = (d.get('token') or '').strip()
    password = (d.get('password') or '').strip()
    if not token or not password:
        return jsonify({'error': 'ข้อมูลไม่ครบ'}), 400
    if len(password) < 6:
        return jsonify({'error': 'รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร'}), 400
    with get_db() as conn:
        rec = conn.execute('SELECT * FROM reset_tokens WHERE token=?', (token,)).fetchone()
        if not rec: return jsonify({'error': 'token ไม่ถูกต้องหรือหมดอายุ'}), 400
        exp = datetime.strptime(rec['expires_at'].split('.')[0], '%Y-%m-%d %H:%M:%S')
        if exp < datetime.now():
            conn.execute('DELETE FROM reset_tokens WHERE token=?', (token,)); conn.commit()
            return jsonify({'error': 'ลิงก์หมดอายุแล้ว กรุณาขอใหม่'}), 400
        conn.execute('UPDATE Stores SET password_hash=? WHERE email=?',
                     (hash_password(password), rec['email']))
        conn.execute('DELETE FROM reset_tokens WHERE token=?', (token,))
        conn.commit()
    return jsonify({'message': 'เปลี่ยนรหัสผ่านสำเร็จ'})

@app.route('/reset-password')
def reset_password_page():
    return render_template('reset_password.html')

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
    mode = request.args.get('mode', 'daily')
    fmt = '%Y-%m-%d' if mode == 'daily' else ('%Y-%m' if mode == 'monthly' else '%Y')
    with get_db() as conn:
        res = conn.execute(
            f"SELECT strftime('{fmt}',OrderTime) as label, SUM(TotalAmount) as total, COUNT(*) as txn_count "
            f"FROM Orders WHERE StoreID=? AND OrderStatus='Paid' GROUP BY label ORDER BY label DESC",
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
            SELECT s.StoreID as id, s.StoreName, s.email, s.created_at,
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

if __name__ == '__main__':
    app.run(debug=True)