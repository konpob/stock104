import os
import sqlite3
import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from flask import Flask, render_template, request, jsonify, session, redirect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
app.secret_key = 'stock_secret_key_2026' # เปลี่ยนเป็นรหัสลับของคุณ

# ตั้งค่า Rate Limit ป้องกันบอทยิง OTP
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

# ตั้งค่า Database Path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # 1. ตารางร้านค้า
        conn.execute('CREATE TABLE IF NOT EXISTS stores (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE)')
        
        # 2. ตาราง OTP 
        conn.execute('CREATE TABLE IF NOT EXISTS otps (email TEXT PRIMARY KEY, code TEXT, expires_at DATETIME)')
        
        # 3. ตารางสินค้า
        conn.execute('''CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER,
                        barcode TEXT, name TEXT, quantity INTEGER, price REAL DEFAULT 0,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        
        # 4. ตารางการขาย
        conn.execute('''CREATE TABLE IF NOT EXISTS sales (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER,
                        transaction_id TEXT, barcode TEXT, product_name TEXT,
                        quantity INTEGER, price_per_unit REAL, total_price REAL, sale_date DATETIME,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        conn.commit()

init_db()

# ================= 1. ระบบหน้าจอ (Routing) =================

@app.route('/')
def index():
    if 'store_id' in session:
        return redirect('/dashboard')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'store_id' not in session:
        return redirect('/')
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ================= 2. ระบบ API: OTP =================

@app.route('/api/request-otp', methods=['POST'])
@limiter.limit("3 per 5 minute")
def request_otp():
    email = request.json.get('email')
    if not email or not email.endswith('@gmail.com'):
        return jsonify({'error': 'กรุณาใช้บัญชี Google Email'}), 400

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now() + timedelta(minutes=2)

    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO otps (email, code, expires_at) VALUES (?, ?, ?)",
                     (email, otp_code, expires_at))
        conn.commit()

    try:
        msg = MIMEText(f'รหัส OTP ของคุณคือ: {otp_code} (หมดอายุใน 2 นาที)')
        msg['Subject'] = 'รหัสเข้าใช้งานระบบสต็อกร้านค้า'
        msg['From'] = 'konpob777@gmail.com'
        msg['To'] = email
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login('konpob777@gmail.com', 'bsuabflgvlejlmpb') # App Password ของคุณ
        server.send_message(msg)
        server.quit()
        return jsonify({'message': 'ส่ง OTP ไปยังอีเมลแล้ว'})
    except Exception as e:
        return jsonify({'error': 'ไม่สามารถส่งอีเมลได้'}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email')
    code = request.json.get('code')
    with get_db() as conn:
        otp_record = conn.execute("SELECT * FROM otps WHERE email = ?", (email,)).fetchone()
        if not otp_record or otp_record['code'] != code:
            return jsonify({'error': 'รหัส OTP ไม่ถูกต้อง'}), 400
        
        # เช็ควันหมดอายุ (แปลง string จาก DB กลับเป็น datetime)
        exp_time = datetime.strptime(otp_record['expires_at'].split('.')[0], '%Y-%m-%d %H:%M:%S')
        if exp_time < datetime.now():
            return jsonify({'error': 'รหัส OTP หมดอายุแล้ว'}), 400
        
        store = conn.execute("SELECT id FROM stores WHERE email = ?", (email,)).fetchone()
        if not store:
            cursor = conn.execute("INSERT INTO stores (email) VALUES (?)", (email,))
            store_id = cursor.lastrowid
        else:
            store_id = store['id']
            
        session['store_id'] = store_id
        session['email'] = email
        conn.execute("DELETE FROM otps WHERE email = ?", (email,))
        conn.commit()
    return jsonify({'message': 'เข้าสู่ระบบสำเร็จ'})

# ================= 3. ระบบ API: สินค้า & ขาย =================

@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401

    with get_db() as conn:
        if request.method == 'GET':
            rows = conn.execute("SELECT * FROM products WHERE store_id = ?", (store_id,)).fetchall()
            return jsonify([dict(r) for r in rows])
        
        data = request.json
        barcode, name = data.get('barcode'), data.get('name')
        qty = int(data.get('quantity', 0))
        price = float(data.get('price', 0))
        
        existing = conn.execute("SELECT * FROM products WHERE store_id=? AND barcode=?", (store_id, barcode)).fetchone()
        if existing:
            conn.execute("UPDATE products SET name=?, quantity=quantity+?, price=? WHERE id=?", 
                         (name, qty, price, existing['id']))
        else:
            conn.execute("INSERT INTO products (store_id, barcode, name, quantity, price) VALUES (?,?,?,?,?)", 
                         (store_id, barcode, name, qty, price))
        conn.commit()
        return jsonify({'message': 'บันทึกสำเร็จ'})

@app.route('/api/checkout', methods=['POST'])
def checkout():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json 
    with get_db() as conn:
        for item in data['items']:
            conn.execute("UPDATE products SET quantity = quantity - ? WHERE store_id = ? AND barcode = ?", 
                         (item['qty'], store_id, item['barcode']))
            conn.execute("""INSERT INTO sales (store_id, transaction_id, barcode, product_name, quantity, price_per_unit, total_price, sale_date) 
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (store_id, data['transaction_id'], item['barcode'], item['name'], item['qty'], item['price'], item['qty']*item['price'], datetime.now()))
        conn.commit()
    return jsonify({'message': 'ขายสำเร็จและตัดสต็อกแล้ว'})

@app.route('/api/sales-summary', methods=['GET'])
def get_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    
    mode = request.args.get('mode', 'daily')
    fmt = '%Y-%m-%d' if mode == 'daily' else '%Y-%m'
    if mode == 'yearly': fmt = '%Y'
    
    query = f"SELECT strftime('{fmt}', sale_date) as label, SUM(total_price) as total FROM sales WHERE store_id = ? GROUP BY label ORDER BY label DESC"
    with get_db() as conn:
        res = conn.execute(query, (store_id,)).fetchall()
        return jsonify([dict(r) for r in res])
    
    

@app.route('/api/sales-details', methods=['GET'])
def get_sales_details():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    
    date_label = request.args.get('date') # รับวันที่ เช่น '2026-04-16'
    
    with get_db() as conn:
        # ดึงรายการขายทั้งหมดของวันนั้น เรียงตามเวลาล่าสุด
        query = """
            SELECT transaction_id, barcode, product_name, quantity, 
                   price_per_unit, total_price, strftime('%H:%M:%S', sale_date) as time_label
            FROM sales 
            WHERE store_id = ? AND strftime('%Y-%m-%d', sale_date) = ?
            ORDER BY sale_date DESC
        """
        rows = conn.execute(query, (store_id, date_label)).fetchall()
        
        # จัดกลุ่มข้อมูลตาม transaction_id เพื่อแยกเป็นใบเสร็จแต่ละใบ
        receipts = {}
        for r in rows:
            tid = r['transaction_id']
            if tid not in receipts:
                receipts[tid] = {'time': r['time_label'], 'items': [], 'grand_total': 0}
            
            receipts[tid]['items'].append(dict(r))
            receipts[tid]['grand_total'] += r['total_price']
            
        return jsonify(receipts)




# สั่งรันแอป (ไว้ท้ายสุดที่เดียว)
if __name__ == '__main__':
    app.run(debug=True)