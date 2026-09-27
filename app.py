import os
import re
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, jsonify, request, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')

# CORS: o frontend pode estar no Render ou, durante testes, em outro domínio/origem.
# Quando há cookies/sessão, o servidor precisa devolver a origem específica e
# Access-Control-Allow-Credentials: true; '*' não funciona com credenciais.
ALLOWED_ORIGINS = {
    'https://oficial-u677.onrender.com',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
    'null',  # arquivos HTML abertos diretamente como file://
}

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Vary'] = 'Origin'
    return response


app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError('Configure SECRET_KEY nas variáveis de ambiente do Render.')

raw_db_url = os.environ.get('DATABASE_URL', '')
if raw_db_url.startswith('postgres://'):
    raw_db_url = raw_db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif raw_db_url.startswith('postgresql://'):
    raw_db_url = raw_db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = raw_db_url or 'sqlite:///' + os.path.join(BASE_DIR, 'analytics.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', 'true').lower() == 'true'

# Para o Render, defina ADMIN_PASSWORD com uma senha forte.
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

if not ADMIN_PASSWORD_HASH and not ADMIN_PASSWORD:
    raise RuntimeError('Configure ADMIN_PASSWORD ou ADMIN_PASSWORD_HASH nas variáveis de ambiente do Render.')

if ADMIN_PASSWORD_HASH:
    _admin_hash = ADMIN_PASSWORD_HASH
else:
    _admin_hash = generate_password_hash(ADMIN_PASSWORD)

db = SQLAlchemy(app)

ACTIVE_SECONDS = 90


class VisitorSession(db.Model):
    __tablename__ = 'visitor_sessions'
    id = db.Column(db.String(36), primary_key=True)
    first_seen = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    last_seen = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    source = db.Column(db.String(40), nullable=False, default='Direto')
    device = db.Column(db.String(30), nullable=False, default='Desktop')
    browser = db.Column(db.String(40), nullable=False, default='Outro')
    landing_page = db.Column(db.String(120), nullable=True)


class PageView(db.Model):
    __tablename__ = 'page_views'
    id = db.Column(db.Integer, primary_key=True)
    visitor_id = db.Column(db.String(36), db.ForeignKey('visitor_sessions.id'), nullable=False, index=True)
    page = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)


def utcnow():
    return datetime.now(timezone.utc)


def start_of_day(days_ago=0):
    now = utcnow() - timedelta(days=days_ago)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def source_from_request():
    ref = request.headers.get('Referer', '').strip()
    if not ref:
        return 'Direto'
    try:
        host = (urlparse(ref).hostname or '').lower()
    except Exception:
        return 'Outro'
    if 'google.' in host or 'bing.' in host or 'duckduckgo.' in host:
        return 'Busca'
    if any(x in host for x in ('instagram.com', 'facebook.com', 't.co', 'linkedin.com', 'youtube.com', 'whatsapp.com')):
        return 'Redes sociais'
    return 'Referência'


def device_from_ua(ua):
    ua = (ua or '').lower()
    if 'ipad' in ua or 'tablet' in ua:
        return 'Tablet'
    if any(x in ua for x in ('mobile', 'iphone', 'android')):
        return 'Celular'
    return 'Desktop'


def browser_from_ua(ua):
    ua = (ua or '').lower()
    if 'edg/' in ua:
        return 'Edge'
    if 'chrome/' in ua and 'chromium' not in ua:
        return 'Chrome'
    if 'firefox/' in ua:
        return 'Firefox'
    if 'safari/' in ua and 'chrome/' not in ua:
        return 'Safari'
    return 'Outro'


def get_or_create_visitor():
    visitor_id = request.cookies.get('geo_visit_id')
    visitor = VisitorSession.query.get(visitor_id) if visitor_id else None
    now = utcnow()

    if not visitor:
        visitor_id = str(uuid.uuid4())
        visitor = VisitorSession(
            id=visitor_id,
            first_seen=now,
            last_seen=now,
            source=source_from_request(),
            device=device_from_ua(request.headers.get('User-Agent')),
            browser=browser_from_ua(request.headers.get('User-Agent')),
        )
        db.session.add(visitor)
    else:
        visitor.last_seen = now

    return visitor, visitor_id


def auth_ok():
    return bool(session.get('admin_authenticated'))


def require_admin():
    if not auth_ok():
        return jsonify({'ok': False, 'error': 'Não autenticado.'}), 401
    return None


@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    full = os.path.join(BASE_DIR, path)
    if os.path.isfile(full):
        return send_from_directory(BASE_DIR, path)
    return send_from_directory(BASE_DIR, 'index.html')




@app.get('/healthz')
def healthz():
    return jsonify({'status': 'ok'})

@app.post('/api/track')
def track():
    # Não contabiliza navegação feita pelo administrador.
    if auth_ok():
        return jsonify({'ok': True, 'ignored': True})

    data = request.get_json(silent=True) or {}
    page = str(data.get('page') or 'home')[:80]
    visitor, visitor_id = get_or_create_visitor()
    visitor.last_seen = utcnow()
    if not visitor.landing_page:
        visitor.landing_page = page
    db.session.add(PageView(visitor_id=visitor_id, page=page, created_at=utcnow()))
    db.session.commit()

    response = jsonify({'ok': True})
    response.set_cookie('geo_visit_id', visitor_id, max_age=60 * 60 * 24 * 365, httponly=True, samesite='Lax', secure=app.config['SESSION_COOKIE_SECURE'])
    return response


@app.post('/api/heartbeat')
def heartbeat():
    if auth_ok():
        return jsonify({'ok': True, 'ignored': True})
    visitor_id = request.cookies.get('geo_visit_id')
    if visitor_id:
        visitor = VisitorSession.query.get(visitor_id)
        if visitor:
            visitor.last_seen = utcnow()
            db.session.commit()
    return jsonify({'ok': True})


@app.post('/api/login')
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '')
    password = str(data.get('password') or '')

    valid = username == ADMIN_USER and check_password_hash(_admin_hash, password)
    if not valid:
        return jsonify({'ok': False, 'error': 'Usuário ou senha inválidos.'}), 401

    session.clear()
    session['admin_authenticated'] = True
    session['admin_user'] = ADMIN_USER
    return jsonify({'ok': True, 'user': ADMIN_USER})


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.get('/api/auth/me')
def auth_me():
    return jsonify({'authenticated': auth_ok(), 'user': session.get('admin_user')})


@app.get('/api/admin/stats')
def admin_stats():
    denied = require_admin()
    if denied:
        return denied

    now = utcnow()
    today = start_of_day()
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    active_cutoff = now - timedelta(seconds=ACTIVE_SECONDS)

    online = db.session.query(func.count(VisitorSession.id)).filter(VisitorSession.last_seen >= active_cutoff).scalar() or 0
    visitors_today = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(PageView.created_at >= today).scalar() or 0
    visitors_week = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(PageView.created_at >= week).scalar() or 0
    visitors_month = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(PageView.created_at >= month).scalar() or 0

    views_today = db.session.query(func.count(PageView.id)).filter(PageView.created_at >= today).scalar() or 0
    views_week = db.session.query(func.count(PageView.id)).filter(PageView.created_at >= week).scalar() or 0
    views_month = db.session.query(func.count(PageView.id)).filter(PageView.created_at >= month).scalar() or 0

    page_rows = db.session.query(PageView.page, func.count(PageView.id)).filter(PageView.created_at >= month).group_by(PageView.page).order_by(func.count(PageView.id).desc()).all()
    device_rows = db.session.query(VisitorSession.device, func.count(VisitorSession.id)).filter(VisitorSession.first_seen >= month).group_by(VisitorSession.device).order_by(func.count(VisitorSession.id).desc()).all()
    source_rows = db.session.query(VisitorSession.source, func.count(VisitorSession.id)).filter(VisitorSession.first_seen >= month).group_by(VisitorSession.source).order_by(func.count(VisitorSession.id).desc()).all()

    trend = []
    for i in range(29, -1, -1):
        day_start = start_of_day(i)
        day_end = day_start + timedelta(days=1)
        count = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(
            PageView.created_at >= day_start,
            PageView.created_at < day_end
        ).scalar() or 0
        trend.append({'date': day_start.date().isoformat(), 'visitors': count})

    return jsonify({
        'ok': True,
        'active_seconds': ACTIVE_SECONDS,
        'kpis': {
            'online': online,
            'visitors_today': visitors_today,
            'visitors_week': visitors_week,
            'visitors_month': visitors_month,
            'views_today': views_today,
            'views_week': views_week,
            'views_month': views_month,
        },
        'pages': [{'page': p, 'views': int(c)} for p, c in page_rows],
        'devices': [{'name': p, 'value': int(c)} for p, c in device_rows],
        'sources': [{'name': p, 'value': int(c)} for p, c in source_rows],
        'trend': trend,
        'updated_at': now.isoformat(),
    })


with app.app_context():
    db.create_all()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
