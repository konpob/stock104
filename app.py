import os
import sqlite3
import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from authlib.integrations.flask_client import OAuth

# โหลด .env ก่อนทุกอย่าง
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'fallback-secret')

# ================= CONFIG จาก .env =================
ADMIN_EMAIL          = os.getenv('ADMIN_EMAIL', '').lower().strip()
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
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS stores (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('CREATE TABLE IF NOT EXISTS otps (email TEXT PRIMARY KEY, code TEXT NOT NULL, expires_at DATETIME NOT NULL)')
        conn.execute('''CREATE TABLE IF NOT EXISTS categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        name TEXT NOT NULL, description TEXT,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS suppliers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        name TEXT NOT NULL, contact_phone TEXT, contact_email TEXT,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        category_id INTEGER, supplier_id INTEGER,
                        barcode TEXT, name TEXT NOT NULL,
                        quantity INTEGER DEFAULT 0, price REAL DEFAULT 0,
                        FOREIGN KEY(store_id) REFERENCES stores(id),
                        FOREIGN KEY(category_id) REFERENCES categories(id),
                        FOREIGN KEY(supplier_id) REFERENCES suppliers(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        supplier_id INTEGER NOT NULL, order_date DATETIME NOT NULL,
                        total_amount REAL DEFAULT 0, status TEXT DEFAULT 'pending',
                        FOREIGN KEY(store_id) REFERENCES stores(id),
                        FOREIGN KEY(supplier_id) REFERENCES suppliers(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS purchase_order_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
                        product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, cost_price REAL NOT NULL,
                        FOREIGN KEY(order_id) REFERENCES purchase_orders(id),
                        FOREIGN KEY(product_id) REFERENCES products(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        amount REAL NOT NULL, description TEXT NOT NULL, expense_date DATETIME NOT NULL,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sales (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                        transaction_id TEXT NOT NULL, barcode TEXT, product_name TEXT NOT NULL,
                        quantity INTEGER NOT NULL, price_per_unit REAL NOT NULL,
                        total_price REAL NOT NULL, sale_date DATETIME NOT NULL,
                        FOREIGN KEY(store_id) REFERENCES stores(id))''')
        conn.commit()

def seed_mock_data(store_id):
    with get_db() as conn:
        if conn.execute('SELECT COUNT(*) as c FROM categories WHERE store_id=?', (store_id,)).fetchone()['c'] > 0:
            return
        categories = [
            (store_id,'เครื่องดื่ม','น้ำอัดลม น้ำผลไม้ ชา กาแฟ'),
            (store_id,'ขนมขบเคี้ยว','มันฝรั่ง ป๊อปคอร์น บิสกิต'),
            (store_id,'ของใช้ส่วนตัว','สบู่ แชมพู ยาสีฟัน'),
            (store_id,'อาหารสำเร็จรูป','บะหมี่กึ่งสำเร็จรูป โจ๊กซอง'),
            (store_id,'นม & ผลิตภัณฑ์นม','นมสด นมUHT โยเกิร์ต'),
            (store_id,'เบเกอรี่','ขนมปัง เค้ก คุกกี้'),
            (store_id,'ผลิตภัณฑ์ทำความสะอาด','น้ำยาล้างจาน ผงซักฟอก'),
            (store_id,'ยาและวิตามิน','ยาสามัญประจำบ้าน วิตามินซี'),
            (store_id,'เครื่องเขียน','ปากกา ดินสอ สมุด'),
            (store_id,'อื่นๆ','สินค้าเบ็ดเตล็ดทั่วไป'),
        ]
        conn.executemany('INSERT INTO categories (store_id,name,description) VALUES (?,?,?)', categories)
        suppliers = [
            (store_id,'บริษัท ไทยเบฟเวอเรจ จำกัด','02-111-1111','thai_bev@supplier.com'),
            (store_id,'บริษัท เนสท์เล่ (ไทย) จำกัด','02-222-2222','nestle@supplier.com'),
            (store_id,'บริษัท พีแอนด์จี จำกัด','02-333-3333','pg@supplier.com'),
            (store_id,'บริษัท มาม่า จำกัด','02-444-4444','mama@supplier.com'),
            (store_id,'บริษัท ดัชมิลล์ จำกัด','02-555-5555','dutch@supplier.com'),
            (store_id,'บริษัท เบทาโกร จำกัด','02-666-6666','betagro@supplier.com'),
            (store_id,'บริษัท ไลอ้อน (ประเทศไทย) จำกัด','02-777-7777','lion@supplier.com'),
            (store_id,'บริษัท ยูนิชาร์ม จำกัด','02-888-8888','unicharm@supplier.com'),
            (store_id,'บริษัท ดอยคำ จำกัด','02-999-9999','doikham@supplier.com'),
            (store_id,'บริษัท สยามแม็คโคร จำกัด','02-000-0000','makro@supplier.com'),
        ]
        conn.executemany('INSERT INTO suppliers (store_id,name,contact_phone,contact_email) VALUES (?,?,?,?)', suppliers)
        cat_ids = [r['id'] for r in conn.execute('SELECT id FROM categories WHERE store_id=? ORDER BY id',(store_id,)).fetchall()]
        sup_ids = [r['id'] for r in conn.execute('SELECT id FROM suppliers WHERE store_id=? ORDER BY id',(store_id,)).fetchall()]
        products = [
            (store_id,cat_ids[0],sup_ids[0],'8850006100084','โค้ก 325ml',50,20.0),
            (store_id,cat_ids[0],sup_ids[0],'8850006100085','เป๊ปซี่ 325ml',40,20.0),
            (store_id,cat_ids[1],sup_ids[1],'8850718111014','มันฝรั่ง Lays 44g',60,25.0),
            (store_id,cat_ids[2],sup_ids[2],'8850111222333','สบู่ Safeguard 80g',30,35.0),
            (store_id,cat_ids[3],sup_ids[3],'8850002111001','มาม่า รสหมู 55g',100,7.0),
            (store_id,cat_ids[4],sup_ids[4],'8850006200001','นม Dutch Mill 200ml',45,15.0),
            (store_id,cat_ids[5],sup_ids[5],'8850333444555','ขนมปังแซนวิช',20,30.0),
            (store_id,cat_ids[6],sup_ids[6],'8850444555666','น้ำยาล้างจาน Sunlight 500ml',25,55.0),
            (store_id,cat_ids[7],sup_ids[7],'8850555666777','วิตามินซี 1000mg',15,120.0),
            (store_id,cat_ids[8],sup_ids[8],'8850666777888','ปากกา Pilot 0.5mm',80,15.0),
        ]
        conn.executemany('INSERT INTO products (store_id,category_id,supplier_id,barcode,name,quantity,price) VALUES (?,?,?,?,?,?,?)', products)
        prod_ids = [r['id'] for r in conn.execute('SELECT id FROM products WHERE store_id=? ORDER BY id',(store_id,)).fetchall()]
        po_data = [(store_id,sup_ids[i%len(sup_ids)],datetime.now()-timedelta(days=30-i*3),round(random.uniform(500,5000),2),'received' if i<7 else 'pending') for i in range(10)]
        conn.executemany('INSERT INTO purchase_orders (store_id,supplier_id,order_date,total_amount,status) VALUES (?,?,?,?,?)', po_data)
        po_ids = [r['id'] for r in conn.execute('SELECT id FROM purchase_orders WHERE store_id=? ORDER BY id',(store_id,)).fetchall()]
        conn.executemany('INSERT INTO purchase_order_items (order_id,product_id,quantity,cost_price) VALUES (?,?,?,?)',
                         [(po_ids[i],prod_ids[i%len(prod_ids)],random.randint(10,50),round(random.uniform(5,100),2)) for i in range(10)])
        expense_data = [('ค่าเช่าพื้นที่',5000),('ค่าไฟฟ้า',1200),('ค่าน้ำประปา',300),('ค่าอินเทอร์เน็ต',599),
                        ('ค่าแรงพนักงาน',9000),('ค่าบรรจุภัณฑ์ถุง',450),('ค่าซ่อมแอร์',1800),
                        ('ค่าทำความสะอาด',500),('ค่าโฆษณา Facebook',300),('ค่าน้ำมันรถ',800)]
        conn.executemany('INSERT INTO expenses (store_id,amount,description,expense_date) VALUES (?,?,?,?)',
                         [(store_id,amt,desc,datetime.now()-timedelta(days=i*3)) for i,(desc,amt) in enumerate(expense_data)])
        sales_data = [(store_id,f'TXN-SEED-{i+1:03d}',products[i%len(products)][3],products[i%len(products)][4],
                       random.randint(1,5),products[i%len(products)][6],
                       random.randint(1,5)*products[i%len(products)][6],
                       datetime.now()-timedelta(days=i*2)) for i in range(10)]
        conn.executemany('INSERT INTO sales (store_id,transaction_id,barcode,product_name,quantity,price_per_unit,total_price,sale_date) VALUES (?,?,?,?,?,?,?,?)', sales_data)
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

# ================= OTP API =================
@app.route('/api/request-otp', methods=['POST'])
def request_otp():
    email = request.json.get('email')
    if not email or not email.endswith('@gmail.com'):
        return jsonify({'error': 'กรุณาใช้บัญชี Google Email'}), 400
    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now() + timedelta(minutes=2)
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO otps (email,code,expires_at) VALUES (?,?,?)', (email,otp_code,expires_at))
        conn.commit()
    try:
        msg = MIMEText(f'รหัส OTP ของคุณคือ: {otp_code} (หมดอายุใน 2 นาที)')
        msg['Subject'] = 'รหัสเข้าใช้งานระบบสต็อกร้านค้า'
        msg['From'] = 'konpob777@gmail.com'; msg['To'] = email
        server = smtplib.SMTP('smtp.gmail.com', 587); server.starttls()
        server.login('konpob777@gmail.com', 'bsuabflgvlejlmpb')
        server.send_message(msg); server.quit()
        return jsonify({'message': 'ส่ง OTP ไปยังอีเมลแล้ว'})
    except Exception as e:
        print(f'Mail Error: {e}')
        return jsonify({'error': 'ไม่สามารถส่งอีเมลได้'}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email'); code = request.json.get('code')
    with get_db() as conn:
        rec = conn.execute('SELECT * FROM otps WHERE email=?', (email,)).fetchone()
        if not rec or rec['code'] != code: return jsonify({'error': 'รหัส OTP ไม่ถูกต้อง'}), 400
        exp = datetime.strptime(rec['expires_at'].split('.')[0], '%Y-%m-%d %H:%M:%S')
        if exp < datetime.now(): return jsonify({'error': 'รหัส OTP หมดอายุแล้ว'}), 400
        store = conn.execute('SELECT id FROM stores WHERE email=?', (email,)).fetchone()
        store_id = store['id'] if store else conn.execute('INSERT INTO stores (email) VALUES (?)', (email,)).lastrowid
        session['store_id'] = store_id; session['email'] = email
        conn.execute('DELETE FROM otps WHERE email=?', (email,)); conn.commit()
    seed_mock_data(store_id)
    return jsonify({'message': 'เข้าสู่ระบบสำเร็จ'})

# ================= PRODUCTS API =================
@app.route('/api/products', methods=['GET','POST'])
def handle_products():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            rows = conn.execute('''SELECT p.*,c.name as category_name,s.name as supplier_name
                FROM products p LEFT JOIN categories c ON p.category_id=c.id
                LEFT JOIN suppliers s ON p.supplier_id=s.id
                WHERE p.store_id=? ORDER BY p.name''', (store_id,)).fetchall()
            return jsonify([dict(r) for r in rows])
        d = request.json
        ex = conn.execute('SELECT * FROM products WHERE store_id=? AND barcode=?',(store_id,d.get('barcode'))).fetchone()
        if ex:
            conn.execute('UPDATE products SET name=?,quantity=quantity+?,price=?,category_id=?,supplier_id=? WHERE id=?',
                         (d['name'],int(d.get('quantity',0)),float(d.get('price',0)),d.get('category_id') or None,d.get('supplier_id') or None,ex['id']))
        else:
            conn.execute('INSERT INTO products (store_id,category_id,supplier_id,barcode,name,quantity,price) VALUES (?,?,?,?,?,?,?)',
                         (store_id,d.get('category_id') or None,d.get('supplier_id') or None,d.get('barcode'),d['name'],int(d.get('quantity',0)),float(d.get('price',0))))
        conn.commit(); return jsonify({'message': 'บันทึกสำเร็จ'})

@app.route('/api/products/<int:pid>', methods=['PUT','DELETE'])
def handle_product_detail(pid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'DELETE':
            conn.execute('DELETE FROM products WHERE id=? AND store_id=?',(pid,store_id)); conn.commit()
            return jsonify({'message': 'ลบสำเร็จ'})
        d = request.json
        conn.execute('UPDATE products SET name=?,price=?,quantity=?,category_id=?,supplier_id=? WHERE id=? AND store_id=?',
                     (d['name'],d['price'],d['quantity'],d.get('category_id') or None,d.get('supplier_id') or None,pid,store_id))
        conn.commit(); return jsonify({'message': 'อัปเดตสำเร็จ'})

@app.route('/api/checkout', methods=['POST'])
def checkout():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    d = request.json
    with get_db() as conn:
        for item in d['items']:
            conn.execute('UPDATE products SET quantity=quantity-? WHERE store_id=? AND barcode=?',(item['qty'],store_id,item['barcode']))
            conn.execute('INSERT INTO sales (store_id,transaction_id,barcode,product_name,quantity,price_per_unit,total_price,sale_date) VALUES (?,?,?,?,?,?,?,?)',
                         (store_id,d['transaction_id'],item['barcode'],item['name'],item['qty'],item['price'],item['qty']*item['price'],datetime.now()))
        conn.commit()
    return jsonify({'message': 'ขายสำเร็จ'})

@app.route('/api/dashboard-summary')
def dashboard_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    today = datetime.now().strftime('%Y-%m-%d')
    with get_db() as conn:
        return jsonify({
            'today_sales':    conn.execute("SELECT COALESCE(SUM(total_price),0) as v FROM sales WHERE store_id=? AND strftime('%Y-%m-%d',sale_date)=?",(store_id,today)).fetchone()['v'],
            'today_txn':      conn.execute("SELECT COUNT(DISTINCT transaction_id) as c FROM sales WHERE store_id=? AND strftime('%Y-%m-%d',sale_date)=?",(store_id,today)).fetchone()['c'],
            'low_stock':      conn.execute('SELECT COUNT(*) as c FROM products WHERE store_id=? AND quantity<=5',(store_id,)).fetchone()['c'],
            'total_products': conn.execute('SELECT COUNT(*) as c FROM products WHERE store_id=?',(store_id,)).fetchone()['c'],
            'top_products':   [dict(r) for r in conn.execute('SELECT product_name,SUM(quantity) as sold FROM sales WHERE store_id=? GROUP BY product_name ORDER BY sold DESC LIMIT 5',(store_id,)).fetchall()],
            'low_items':      [dict(r) for r in conn.execute('SELECT name,quantity FROM products WHERE store_id=? AND quantity<=5 ORDER BY quantity ASC LIMIT 5',(store_id,)).fetchall()],
        })

@app.route('/api/sales-summary')
def get_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    mode = request.args.get('mode','daily')
    fmt = '%Y-%m-%d' if mode=='daily' else ('%Y-%m' if mode=='monthly' else '%Y')
    with get_db() as conn:
        res = conn.execute(f"SELECT strftime('{fmt}',sale_date) as label,SUM(total_price) as total FROM sales WHERE store_id=? GROUP BY label ORDER BY label DESC",(store_id,)).fetchall()
        return jsonify([dict(r) for r in res])

@app.route('/api/sales-details')
def get_sales_details():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    date_label = request.args.get('date')
    with get_db() as conn:
        rows = conn.execute("SELECT transaction_id,barcode,product_name,quantity,price_per_unit,total_price,strftime('%H:%M:%S',sale_date) as time_label FROM sales WHERE store_id=? AND strftime('%Y-%m-%d',sale_date)=? ORDER BY sale_date DESC",(store_id,date_label)).fetchall()
        receipts = {}
        for r in rows:
            tid = r['transaction_id']
            if tid not in receipts: receipts[tid] = {'time':r['time_label'],'items':[],'grand_total':0}
            receipts[tid]['items'].append(dict(r)); receipts[tid]['grand_total'] += r['total_price']
        return jsonify(receipts)

@app.route('/api/financial-summary')
def financial_summary():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    with get_db() as conn:
        income  = conn.execute("SELECT COALESCE(SUM(total_price),0) as v FROM sales WHERE store_id=? AND strftime('%Y-%m',sale_date)=?",(store_id,month)).fetchone()['v']
        expense = conn.execute("SELECT COALESCE(SUM(amount),0) as v FROM expenses WHERE store_id=? AND strftime('%Y-%m',expense_date)=?",(store_id,month)).fetchone()['v']
        cost    = conn.execute("SELECT COALESCE(SUM(poi.quantity*poi.cost_price),0) as v FROM purchase_order_items poi JOIN purchase_orders po ON poi.order_id=po.id WHERE po.store_id=? AND strftime('%Y-%m',po.order_date)=?",(store_id,month)).fetchone()['v']
        return jsonify({'month':month,'total_income':income,'total_expense':expense,'total_cost':cost,'net_profit':income-expense-cost})

# ================= CATEGORIES API =================
@app.route('/api/categories', methods=['GET','POST'])
def handle_categories():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            return jsonify([dict(r) for r in conn.execute('SELECT * FROM categories WHERE store_id=? ORDER BY name',(store_id,)).fetchall()])
        d = request.json
        conn.execute('INSERT INTO categories (store_id,name,description) VALUES (?,?,?)',(store_id,d['name'],d.get('description','')))
        conn.commit(); return jsonify({'message': 'เพิ่มสำเร็จ'})

@app.route('/api/categories/<int:cid>', methods=['DELETE'])
def delete_category(cid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        conn.execute('DELETE FROM categories WHERE id=? AND store_id=?',(cid,store_id)); conn.commit()
    return jsonify({'message': 'ลบสำเร็จ'})

# ================= SUPPLIERS API =================
@app.route('/api/suppliers', methods=['GET','POST'])
def handle_suppliers():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            return jsonify([dict(r) for r in conn.execute('SELECT * FROM suppliers WHERE store_id=? ORDER BY name',(store_id,)).fetchall()])
        d = request.json
        conn.execute('INSERT INTO suppliers (store_id,name,contact_phone,contact_email) VALUES (?,?,?,?)',(store_id,d['name'],d.get('contact_phone',''),d.get('contact_email','')))
        conn.commit(); return jsonify({'message': 'เพิ่มสำเร็จ'})

@app.route('/api/suppliers/<int:sid>', methods=['DELETE'])
def delete_supplier(sid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        conn.execute('DELETE FROM suppliers WHERE id=? AND store_id=?',(sid,store_id)); conn.commit()
    return jsonify({'message': 'ลบสำเร็จ'})

# ================= PURCHASE ORDERS API =================
@app.route('/api/purchase-orders', methods=['GET','POST'])
def handle_purchase_orders():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            rows = conn.execute('SELECT po.*,s.name as supplier_name FROM purchase_orders po JOIN suppliers s ON po.supplier_id=s.id WHERE po.store_id=? ORDER BY po.order_date DESC',(store_id,)).fetchall()
            return jsonify([dict(r) for r in rows])
        d = request.json
        total = sum(i['quantity']*i['cost_price'] for i in d['items'])
        po_id = conn.execute('INSERT INTO purchase_orders (store_id,supplier_id,order_date,total_amount,status) VALUES (?,?,?,?,?)',(store_id,d['supplier_id'],datetime.now(),total,'received')).lastrowid
        for item in d['items']:
            conn.execute('INSERT INTO purchase_order_items (order_id,product_id,quantity,cost_price) VALUES (?,?,?,?)',(po_id,item['product_id'],item['quantity'],item['cost_price']))
            conn.execute('UPDATE products SET quantity=quantity+? WHERE id=? AND store_id=?',(item['quantity'],item['product_id'],store_id))
        conn.commit(); return jsonify({'message': 'สร้างใบสั่งซื้อสำเร็จ'})

# ================= EXPENSES API =================
@app.route('/api/expenses', methods=['GET','POST'])
def handle_expenses():
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        if request.method == 'GET':
            return jsonify([dict(r) for r in conn.execute('SELECT * FROM expenses WHERE store_id=? ORDER BY expense_date DESC',(store_id,)).fetchall()])
        d = request.json
        conn.execute('INSERT INTO expenses (store_id,amount,description,expense_date) VALUES (?,?,?,?)',(store_id,d['amount'],d['description'],datetime.now()))
        conn.commit(); return jsonify({'message': 'บันทึกสำเร็จ'})

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
def delete_expense(eid):
    store_id = session.get('store_id')
    if not store_id: return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        conn.execute('DELETE FROM expenses WHERE id=? AND store_id=?',(eid,store_id)); conn.commit()
    return jsonify({'message': 'ลบสำเร็จ'})

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

        # ✅ เช็คเฉพาะอีเมลที่อยู่ใน .env เท่านั้น
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
        return render_template('admin_login.html',
            error='เกิดข้อผิดพลาดในการเข้าสู่ระบบ กรุณาลองใหม่')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_email', None)
    session.pop('admin_name', None)
    session.pop('admin_picture', None)
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
            'total_stores':       conn.execute('SELECT COUNT(*) as c FROM stores').fetchone()['c'],
            'total_sales':        conn.execute('SELECT COALESCE(SUM(total_price),0) as v FROM sales').fetchone()['v'],
            'total_products':     conn.execute('SELECT COUNT(*) as c FROM products').fetchone()['c'],
            'total_transactions': conn.execute('SELECT COUNT(DISTINCT transaction_id) as c FROM sales').fetchone()['c'],
            'today_sales':        conn.execute("SELECT COALESCE(SUM(total_price),0) as v FROM sales WHERE strftime('%Y-%m-%d',sale_date)=?",(today,)).fetchone()['v'],
            'sales_by_day':       [dict(r) for r in conn.execute("SELECT strftime('%Y-%m-%d',sale_date) as label,SUM(total_price) as total FROM sales GROUP BY label ORDER BY label DESC LIMIT 14").fetchall()],
            'top_stores':         [dict(r) for r in conn.execute('SELECT st.email,COALESCE(SUM(s.total_price),0) as revenue,COUNT(DISTINCT s.transaction_id) as txn FROM stores st LEFT JOIN sales s ON st.id=s.store_id GROUP BY st.id ORDER BY revenue DESC LIMIT 10').fetchall()],
        })

@app.route('/admin/api/stores')
@admin_required
def admin_stores():
    with get_db() as conn:
        rows = conn.execute('''SELECT st.id, st.email, st.created_at,
            COUNT(DISTINCT p.id) as product_count,
            COUNT(DISTINCT s.transaction_id) as txn_count,
            COALESCE(SUM(s.total_price),0) as revenue
            FROM stores st
            LEFT JOIN products p ON st.id=p.store_id
            LEFT JOIN sales s ON st.id=s.store_id
            GROUP BY st.id ORDER BY st.created_at DESC''').fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/admin/api/stores/<int:store_id>', methods=['DELETE'])
@admin_required
def admin_delete_store(store_id):
    with get_db() as conn:
        conn.execute('DELETE FROM sales WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM expenses WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM purchase_order_items WHERE order_id IN (SELECT id FROM purchase_orders WHERE store_id=?)',(store_id,))
        conn.execute('DELETE FROM purchase_orders WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM products WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM categories WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM suppliers WHERE store_id=?',(store_id,))
        conn.execute('DELETE FROM stores WHERE id=?',(store_id,))
        conn.commit()
    return jsonify({'message': 'ลบร้านค้าสำเร็จ'})

@app.route('/admin/api/sql', methods=['POST'])
@admin_required
def admin_sql():
    query = request.json.get('query','').strip()
    if not query: return jsonify({'error': 'กรุณาระบุ SQL Query'}), 400
    try:
        with get_db() as conn:
            cursor = conn.execute(query)
            if query.upper().lstrip().startswith(('SELECT','PRAGMA')):
                rows = cursor.fetchall()
                if not rows: return jsonify({'columns':[],'rows':[],'message':'ไม่มีข้อมูล','affected':0})
                return jsonify({'columns':list(rows[0].keys()),'rows':[list(r) for r in rows],'affected':len(rows),'message':''})
            conn.commit()
            return jsonify({'columns':[],'rows':[],'message':f'สำเร็จ: {cursor.rowcount} แถวถูกเปลี่ยนแปลง','affected':cursor.rowcount})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/admin/api/tables')
@admin_required
def admin_tables():
    with get_db() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return jsonify({t['name']: conn.execute(f"SELECT COUNT(*) as c FROM {t['name']}").fetchone()['c'] for t in tables})

if __name__ == '__main__':
    app.run(debug=True)