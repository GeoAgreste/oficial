import os
import re
import uuid
import hashlib
import logging
import json
import ipaddress
from urllib.request import Request as UrlRequest, urlopen
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, jsonify, request, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

# Configuração de logs para depuração
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
# Permite enviar até cinco imagens de 20 MB e um vídeo de até 100 MB por requisições separadas.
app.config['MAX_CONTENT_LENGTH'] = 110 * 1024 * 1024

# CORS: o frontend pode estar no Render ou, durante testes, em outro domínio/origem.
ALLOWED_ORIGINS = {
    'https://oficial-u677.onrender.com',
    'https://geoagreste.github.io',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'null',  # arquivos HTML abertos diretamente como file://
}

@app.before_request
def handle_preflight():
    """Garante resposta adequada para requisições OPTIONS de CORS preflight."""
    if request.method == 'OPTIONS':
        response = app.make_default_options_response()
        return add_cors_headers(response)

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin and (origin in ALLOWED_ORIGINS or (origin.endswith('.onrender.com') or origin.endswith('.github.io')) or origin.endswith('.github.io')):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
        response.headers['Vary'] = 'Origin'
    return response


# Secret key fallback seguro para evitar crash se não definida no ambiente local
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'geoagreste-secret-key-default-2026')

raw_db_url = os.environ.get('DATABASE_URL', '')
if raw_db_url.startswith('postgres://'):
    raw_db_url = raw_db_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif raw_db_url.startswith('postgresql://'):
    raw_db_url = raw_db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = raw_db_url or ('sqlite:///' + os.path.join(BASE_DIR, 'analytics.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'

# Para o Render, defina ADMIN_USER e ADMIN_PASSWORD nas variáveis de ambiente.
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

if ADMIN_PASSWORD_HASH:
    _admin_hash = ADMIN_PASSWORD_HASH
else:
    _admin_hash = generate_password_hash(ADMIN_PASSWORD)

db = SQLAlchemy(app)

ACTIVE_SECONDS = 90
LOCATION_HISTORY_HOURS = 24


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


class VisitorLocation(db.Model):
    __tablename__ = 'visitor_locations'
    visitor_id = db.Column(db.String(36), db.ForeignKey('visitor_sessions.id'), primary_key=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)


class Course(db.Model):
    __tablename__ = 'courses'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    info = db.Column(db.Text, nullable=False, default='')
    hours = db.Column(db.String(80), nullable=False, default='')
    instructor = db.Column(db.String(160), nullable=False, default='')
    content = db.Column(db.Text, nullable=False, default='')
    price = db.Column(db.String(80), nullable=False, default='')
    link = db.Column(db.String(500), nullable=False, default='')


class PortfolioItem(db.Model):
    __tablename__ = 'portfolio_items'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    image_url = db.Column(db.String(1000), nullable=False)
    cloudinary_public_id = db.Column(db.String(300), nullable=True)
    summary = db.Column(db.Text, nullable=False, default='')
    location = db.Column(db.String(300), nullable=False, default='')
    objective = db.Column(db.Text, nullable=False, default='')
    images_json = db.Column(db.Text, nullable=False, default='[]')
    video_url = db.Column(db.String(1500), nullable=False, default='')
    video_public_id = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

DEFAULT_PORTFOLIO = [
    {'id': 1, 'title': 'Georreferenciamento Fazenda Boa Esperança', 'image_url': 'https://images.unsplash.com/photo-1623869680373-c6b22b64d04c?auto=format&fit=crop&q=80&w=800'},
    {'id': 2, 'title': 'Projeto REURB Residencial Vista Verde', 'image_url': 'https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&q=80&w=800'},
    {'id': 3, 'title': 'Aerolevantamento e Inspeção 3D Solar Tech', 'image_url': 'https://images.unsplash.com/photo-1508344928928-7137b29339d0?auto=format&fit=crop&q=80&w=800'},
]


DEFAULT_COURSES = [
    {
        'id': 1,
        'title': 'Piloto Profissional de Drone',
        'info': 'Capacitação completa para operação comercial de drones, regulamentação ANAC/DECEA e segurança de voo.',
        'hours': '40 horas',
        'instructor': 'Equipe Especializada GeoAgreste',
        'content': '• Legislação vigente (ANAC, DECEA, Ministério da Defesa)\n• Componentes e sistemas de RPA\n• Planejamento de Voo e Meteorologia\n• Prática de voo com simuladores e em campo\n• Coleta de dados básicos',
        'price': 'R$ 1.250,00',
        'link': 'https://geoagresteacademy.netlify.app/'
    },
    {
        'id': 2,
        'title': 'CCIR na Prática',
        'info': 'Aprenda a emitir e atualizar o Certificado de Cadastro de Imóvel Rural, essencial para transações e financiamentos.',
        'hours': '16 horas',
        'instructor': 'Eng. Agrimensor Victor Gomes',
        'content': '• Introdução ao SNCR (Sistema Nacional de Cadastro Rural)\n• Documentação necessária e análise dominial\n• Preenchimento prático no sistema do INCRA\n• Tratamento de inconsistências e sobreposições\n• Atualização e desmembramento no CCIR',
        'price': 'R$ 680,00',
        'link': ''
    },
    {
        'id': 3,
        'title': 'Dominando o CAR & QGIS',
        'info': 'Treinamento intensivo sobre o Cadastro Ambiental Rural, legislação vigente e manuseio do software QGIS.',
        'hours': '24 horas',
        'instructor': 'Eng. Cartógrafo Filipe Soares',
        'content': '• Legislação (Novo Código Florestal)\n• Mapeamento de APP, Reserva Legal e Área Consolidada\n• Uso do QGIS para vetorização de dados\n• Inserção de dados no SICAR\n• Análise e Retificação de Cadastros',
        'price': 'R$ 850,00',
        'link': ''
    }
]


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
    if not host:
        return 'Direto'
    if any(x in host for x in ('google.', 'bing.', 'duckduckgo.', 'yahoo.', 'ecosia.', 'baidu.')):
        return 'Busca'
    if any(x in host for x in ('instagram.com', 'facebook.com', 'fb.me', 't.co', 'twitter.com', 'x.com', 'linkedin.com', 'youtube.com', 'whatsapp.com', 'tiktok.com')):
        return 'Redes sociais'
    return 'Referência'


def device_from_ua(ua):
    ua = (ua or '').lower()
    if any(x in ua for x in ('ipad', 'tablet', 'kindle', 'playbook', 'nexus 7', 'nexus 10')):
        return 'Tablet'
    if any(x in ua for x in ('mobile', 'iphone', 'ipod', 'android', 'blackberry', 'webos', 'windows phone')):
        return 'Celular'
    return 'Desktop'


def browser_from_ua(ua):
    ua = (ua or '').lower()
    if 'edg/' in ua or 'edge/' in ua:
        return 'Edge'
    if 'opr/' in ua or 'opera' in ua:
        return 'Opera'
    if 'chrome/' in ua and 'chromium' not in ua:
        return 'Chrome'
    if 'firefox/' in ua:
        return 'Firefox'
    if 'safari/' in ua and 'chrome/' not in ua:
        return 'Safari'
    if 'brave' in ua:
        return 'Brave'
    return 'Outro'


def get_client_ip():
    """Obtém o IP público do visitante sem depender de geolocalização do navegador."""
    candidates = [
        request.headers.get('CF-Connecting-IP'),
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip(),
        request.headers.get('X-Real-IP'),
        request.remote_addr,
    ]
    for raw in candidates:
        ip = (raw or '').strip()
        if not ip:
            continue
        try:
            obj = ipaddress.ip_address(ip)
            if not (obj.is_private or obj.is_loopback or obj.is_reserved or obj.is_link_local):
                return ip
        except ValueError:
            continue
    return None


def update_ip_location(visitor_id, force=False):
    """Obtém uma localização aproximada pelo IP, sem pedir permissão ao visitante.

    A geolocalização por IP é deliberadamente tratada como aproximada: os
    dados normalmente representam uma área/cidade, não a residência exata.
    O IP bruto não é salvo no banco por esta função.
    """
    try:
        existing = db.session.get(VisitorLocation, visitor_id)
        if existing and not force:
            return False

        ip = get_client_ip()
        if not ip:
            return False

        url = f'https://ipapi.co/{ip}/json/'
        req = UrlRequest(url, headers={'User-Agent': 'GeoAgreste-Analytics/1.0'})
        with urlopen(req, timeout=3.0) as response:
            payload = json.loads(response.read().decode('utf-8'))

        latitude = payload.get('latitude')
        longitude = payload.get('longitude')
        if latitude is None or longitude is None:
            return False

        latitude = float(latitude)
        longitude = float(longitude)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return False

        now = utcnow()
        if existing:
            # Só substitui uma posição GPS por IP quando não houver precisão GPS.
            if existing.accuracy is not None and not force:
                return False
            existing.latitude = latitude
            existing.longitude = longitude
            existing.accuracy = None
            existing.updated_at = now
        else:
            db.session.add(VisitorLocation(
                visitor_id=visitor_id,
                latitude=latitude,
                longitude=longitude,
                accuracy=None,
                updated_at=now,
            ))
        db.session.flush()
        return True
    except Exception as e:
        logger.warning(f'Geolocalização por IP indisponível: {e}')
        return False


def get_or_create_visitor(client_visitor_id=None):
    """Identifica o visitante sem depender exclusivamente de cookies de terceiros."""
    visitor_id = str(client_visitor_id or request.cookies.get('geo_visit_id') or '').strip()
    visitor = None

    if visitor_id:
        try:
            uuid.UUID(visitor_id)
            visitor = db.session.get(VisitorSession, visitor_id)
        except (ValueError, AttributeError):
            visitor = None
        except Exception as e:
            logger.warning(f"Erro ao consultar sessão de visitante {visitor_id}: {e}")
            visitor = None

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
        # Garante que o registro pai exista no banco antes de qualquer
        # PageView que use visitor_id como chave estrangeira.
        db.session.flush()
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


@app.route('/api/track', methods=['POST', 'OPTIONS'])
def track():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    # Não contabiliza navegação feita pelo administrador.
    if auth_ok():
        return jsonify({'ok': True, 'ignored': True})

    try:
        data = request.get_json(silent=True) or {}
        page = str(data.get('page') or 'home')[:80]
        client_visitor_id = data.get('visitor_id')

        visitor, visitor_id = get_or_create_visitor(client_visitor_id)
        visitor.last_seen = utcnow()
        if not visitor.landing_page:
            visitor.landing_page = page

        # Posiciona automaticamente pelo IP, sem pedir permissão ao visitante.
        update_ip_location(visitor_id)

        page_view = PageView(
            visitor_id=visitor_id,
            page=page,
            created_at=utcnow()
        )
        db.session.add(page_view)
        db.session.commit()

        response = jsonify({'ok': True, 'visitor_id': visitor_id})
        response.set_cookie(
            'geo_visit_id',
            visitor_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite='Lax',
            secure=app.config['SESSION_COOKIE_SECURE']
        )
        return response
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/track: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/heartbeat', methods=['POST', 'OPTIONS'])
def heartbeat():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    if auth_ok():
        return jsonify({'ok': True, 'ignored': True})

    try:
        data = request.get_json(silent=True) or {}
        visitor_id = str(data.get('visitor_id') or request.cookies.get('geo_visit_id') or '').strip()

        visitor = None
        if visitor_id:
            try:
                uuid.UUID(visitor_id)
                visitor = db.session.get(VisitorSession, visitor_id)
            except (ValueError, AttributeError):
                visitor = None

        # O heartbeat pode chegar antes do /api/track. Nesse caso, cria
        # imediatamente a sessão para que o visitante já seja considerado online.
        if not visitor:
            visitor_id = str(uuid.uuid4())
            now = utcnow()
            visitor = VisitorSession(
                id=visitor_id,
                first_seen=now,
                last_seen=now,
                source=source_from_request(),
                device=device_from_ua(request.headers.get('User-Agent')),
                browser=browser_from_ua(request.headers.get('User-Agent')),
            )
            db.session.add(visitor)
            db.session.flush()
        else:
            visitor.last_seen = utcnow()

        # Se ainda não houver posição, tenta a geolocalização aproximada por IP.
        update_ip_location(visitor_id)

        db.session.commit()
        response = jsonify({'ok': True, 'tracked': True, 'visitor_id': visitor_id})
        response.set_cookie(
            'geo_visit_id',
            visitor_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite='Lax',
            secure=app.config['SESSION_COOKIE_SECURE']
        )
        return response
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/heartbeat: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


def course_to_dict(course):
    return {
        'id': course.id,
        'title': course.title,
        'info': course.info,
        'hours': course.hours,
        'instructor': course.instructor,
        'content': course.content,
        'price': course.price,
        'link': course.link,
    }


def seed_default_courses():
    if db.session.query(Course).count() == 0:
        for item in DEFAULT_COURSES:
            db.session.add(Course(**item))
        db.session.commit()
        logger.info('Cursos padrão cadastrados no banco de dados.')


@app.get('/api/courses')
def list_courses():
    try:
        seed_default_courses()
        seed_default_portfolio()
        courses = db.session.query(Course).order_by(Course.id.asc()).all()
        return jsonify({'ok': True, 'courses': [course_to_dict(c) for c in courses]})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/courses GET: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/api/courses')
def create_course():
    denied = require_admin()
    if denied:
        return denied
    try:
        data = request.get_json(silent=True) or {}
        title = str(data.get('title') or '').strip()
        if not title:
            return jsonify({'ok': False, 'error': 'O título do curso é obrigatório.'}), 400

        # Não dependemos da sequência automática do PostgreSQL aqui.
        # Os cursos padrão são inseridos com IDs explícitos (1, 2, 3),
        # portanto uma sequência antiga/desalinhada poderia tentar reutilizar
        # o ID 1 e gerar UniqueViolation. O próximo ID é calculado a partir
        # do maior ID realmente existente no banco.
        max_id = db.session.query(func.max(Course.id)).scalar() or 0
        next_id = int(max_id) + 1

        course = Course(
            id=next_id,
            title=title[:200],
            info=str(data.get('info') or '').strip(),
            hours=str(data.get('hours') or '').strip(),
            instructor=str(data.get('instructor') or '').strip(),
            content=str(data.get('content') or '').strip(),
            price=str(data.get('price') or '').strip(),
            link=str(data.get('link') or '').strip()[:500],
        )
        db.session.add(course)
        db.session.commit()
        return jsonify({'ok': True, 'course': course_to_dict(course)}), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/courses POST: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.put('/api/courses/<int:course_id>')
def update_course(course_id):
    denied = require_admin()
    if denied:
        return denied
    try:
        course = db.session.get(Course, course_id)
        if not course:
            return jsonify({'ok': False, 'error': 'Curso não encontrado.'}), 404

        data = request.get_json(silent=True) or {}
        title = str(data.get('title') or '').strip()
        if not title:
            return jsonify({'ok': False, 'error': 'O título do curso é obrigatório.'}), 400

        course.title = title[:200]
        course.info = str(data.get('info') or '').strip()
        course.hours = str(data.get('hours') or '').strip()
        course.instructor = str(data.get('instructor') or '').strip()
        course.content = str(data.get('content') or '').strip()
        course.price = str(data.get('price') or '').strip()
        course.link = str(data.get('link') or '').strip()[:500]
        db.session.commit()
        return jsonify({'ok': True, 'course': course_to_dict(course)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/courses PUT: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.delete('/api/courses/<int:course_id>')
def delete_course(course_id):
    denied = require_admin()
    if denied:
        return denied
    try:
        course = db.session.get(Course, course_id)
        if not course:
            return jsonify({'ok': False, 'error': 'Curso não encontrado.'}), 404
        db.session.delete(course)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/courses DELETE: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    try:
        data = request.get_json(silent=True) or {}
        username = str(data.get('username') or '').strip()
        password = str(data.get('password') or '').strip()

        valid = (username == ADMIN_USER) and check_password_hash(_admin_hash, password)
        if not valid:
            return jsonify({'ok': False, 'error': 'Usuário ou senha inválidos.'}), 401

        session.clear()
        session['admin_authenticated'] = True
        session['admin_user'] = ADMIN_USER
        return jsonify({'ok': True, 'user': ADMIN_USER})
    except Exception as e:
        logger.error(f"Erro em /api/login: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/location', methods=['POST', 'OPTIONS'])
def update_location():
    '''Recebe a localização do navegador somente após a permissão do usuário.'''
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

    if auth_ok():
        return jsonify({'ok': True, 'ignored': True})

    try:
        data = request.get_json(silent=True) or {}
        visitor_id = str(data.get('visitor_id') or request.cookies.get('geo_visit_id') or '').strip()
        try:
            uuid.UUID(visitor_id)
        except (ValueError, AttributeError):
            visitor_id = ''

        visitor, visitor_id = get_or_create_visitor(visitor_id)
        db.session.flush()

        latitude = float(data.get('latitude'))
        longitude = float(data.get('longitude'))
        accuracy_raw = data.get('accuracy')
        accuracy = float(accuracy_raw) if accuracy_raw not in (None, '') else None

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return jsonify({'ok': False, 'error': 'Coordenadas inválidas.'}), 400
        if accuracy is not None and (accuracy < 0 or accuracy > 100000):
            accuracy = None

        now = utcnow()
        location = db.session.get(VisitorLocation, visitor_id)
        if location:
            location.latitude = latitude
            location.longitude = longitude
            location.accuracy = accuracy
            location.updated_at = now
        else:
            db.session.add(VisitorLocation(
                visitor_id=visitor_id,
                latitude=latitude,
                longitude=longitude,
                accuracy=accuracy,
                updated_at=now,
            ))

        visitor.last_seen = now
        db.session.commit()

        response = jsonify({'ok': True, 'visitor_id': visitor_id, 'updated_at': now.isoformat()})
        response.set_cookie(
            'geo_visit_id', visitor_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite='Lax',
            secure=app.config['SESSION_COOKIE_SECURE']
        )
        return response
    except (TypeError, ValueError):
        db.session.rollback()
        return jsonify({'ok': False, 'error': 'Latitude/longitude inválidas.'}), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/location: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/api/admin/locations')
def admin_locations():
    '''Retorna as últimas localizações conhecidas para o mapa administrativo.'''
    denied = require_admin()
    if denied:
        return denied

    try:
        now = utcnow()
        active_cutoff = now - timedelta(seconds=ACTIVE_SECONDS)
        history_cutoff = now - timedelta(hours=LOCATION_HISTORY_HOURS)

        rows = (db.session.query(VisitorSession, VisitorLocation)
                .join(VisitorLocation, VisitorLocation.visitor_id == VisitorSession.id)
                .filter(VisitorLocation.updated_at >= history_cutoff)
                .order_by(VisitorSession.last_seen.desc())
                .limit(5000).all())

        visitors = []
        for visitor, location in rows:
            active = visitor.last_seen >= active_cutoff
            visitors.append({
                'visitor_id': visitor.id,
                'latitude': location.latitude,
                'longitude': location.longitude,
                'accuracy': location.accuracy,
                'location_source': 'gps' if location.accuracy is not None else 'ip',
                'first_seen': visitor.first_seen.isoformat(),
                'last_seen': visitor.last_seen.isoformat(),
                'location_updated_at': location.updated_at.isoformat(),
                'device': visitor.device,
                'browser': visitor.browser,
                'page': visitor.landing_page or 'home',
                'status': 'active' if active else 'finished',
            })

        return jsonify({
            'ok': True,
            'active_seconds': ACTIVE_SECONDS,
            'history_hours': LOCATION_HISTORY_HOURS,
            'visitors': visitors,
            'updated_at': now.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/admin/locations: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    if request.method == 'OPTIONS':
        return jsonify({'ok': True})

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

    try:
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
        browser_rows = db.session.query(VisitorSession.browser, func.count(VisitorSession.id)).filter(VisitorSession.first_seen >= month).group_by(VisitorSession.browser).order_by(func.count(VisitorSession.id).desc()).all()

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
            'browsers': [{'name': p, 'value': int(c)} for p, c in browser_rows],
            'trend': trend,
            'updated_at': now.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro em /api/admin/stats: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500



def _portfolio_images(item):
    try:
        images = json.loads(item.images_json or '[]')
        if not isinstance(images, list):
            images = []
    except (TypeError, json.JSONDecodeError):
        images = []
    if not images and item.image_url:
        images = [{'url': item.image_url, 'public_id': item.cloudinary_public_id or ''}]
    cleaned = []
    for image in images[:5]:
        if isinstance(image, str):
            image = {'url': image, 'public_id': ''}
        if isinstance(image, dict) and str(image.get('url') or '').startswith(('https://', 'http://')):
            cleaned.append({'url': str(image['url']), 'public_id': str(image.get('public_id') or '')})
    return cleaned


def portfolio_to_dict(item):
    images = _portfolio_images(item)
    return {
        'id': item.id, 'title': item.title, 'img': (images[0]['url'] if images else item.image_url),
        'image_url': (images[0]['url'] if images else item.image_url),
        'cloudinary_public_id': (images[0].get('public_id') if images else item.cloudinary_public_id),
        'images': images, 'summary': item.summary or '', 'location': item.location or '',
        'objective': item.objective or '', 'video_url': item.video_url or '',
        'video_public_id': item.video_public_id or ''
    }


def seed_default_portfolio():
    if db.session.query(PortfolioItem).count() == 0:
        for data in DEFAULT_PORTFOLIO:
            image_url = data.get('image_url') or data.get('img') or ''
            item = PortfolioItem(id=data.get('id'), title=data['title'], image_url=image_url,
                                 cloudinary_public_id=data.get('cloudinary_public_id'),
                                 images_json=json.dumps([{'url': image_url, 'public_id': ''}], ensure_ascii=False))
            db.session.add(item)
        db.session.commit()
        logger.info('Portfólio padrão cadastrado no banco de dados.')


def _cloudinary_configured():
    return all(os.environ.get(k) for k in ('CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_API_KEY', 'CLOUDINARY_API_SECRET'))


def _configure_cloudinary():
    """Configura o SDK e importa explicitamente o submódulo de upload.

    Importar apenas `cloudinary` não garante que `cloudinary.uploader` esteja
    carregado. Importamos o submódulo para evitar o AttributeError observado
    nos logs do Render.
    """
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=os.environ['CLOUDINARY_CLOUD_NAME'],
        api_key=os.environ['CLOUDINARY_API_KEY'],
        api_secret=os.environ['CLOUDINARY_API_SECRET'],
        secure=True,
    )
    return cloudinary.uploader


@app.get('/api/portfolio')
def list_portfolio():
    try:
        seed_default_portfolio()
        items = db.session.query(PortfolioItem).order_by(PortfolioItem.id.asc()).all()
        return jsonify({'ok': True, 'portfolio': [portfolio_to_dict(item) for item in items]})
    except Exception:
        db.session.rollback()
        logger.exception('Erro em GET /api/portfolio')
        return jsonify({'ok': False, 'error': 'Não foi possível carregar o portfólio.'}), 500


@app.post('/api/portfolio/upload')
def upload_portfolio_image():
    denied = require_admin()
    if denied:
        return denied
    image = request.files.get('image')
    if not image or not image.filename:
        return jsonify({'ok': False, 'error': 'Selecione uma imagem.'}), 400
    if image.mimetype not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
        return jsonify({'ok': False, 'error': 'Formato não permitido. Use JPG, PNG, WEBP ou GIF.'}), 400
    image.stream.seek(0, 2)
    image_size = image.stream.tell()
    image.stream.seek(0)
    if image_size > 20 * 1024 * 1024:
        return jsonify({'ok': False, 'error': 'Cada imagem pode ter no máximo 20 MB.'}), 413
    if not _cloudinary_configured():
        return jsonify({'ok': False, 'error': 'Configure as três variáveis CLOUDINARY no Render.'}), 503
    try:
        uploader = _configure_cloudinary()
        result = uploader.upload(image, folder='geoagreste/portfolio', resource_type='image',
                                             allowed_formats=['jpg', 'jpeg', 'png', 'webp', 'gif'])
        return jsonify({'ok': True, 'url': result['secure_url'], 'public_id': result['public_id']})
    except Exception as exc:
        logger.exception('Falha no upload de imagem do portfólio')
        # Não expõe credenciais; converte erros comuns em mensagens acionáveis.
        cloud_error = str(exc).lower()
        if any(term in cloud_error for term in ('file size too large', 'file size limit', 'maximum file size', 'max file size', 'too large')):
            message = ('O Cloudinary recusou o arquivo por exceder o limite de tamanho permitido pela conta. '
                       'Tente uma imagem menor ou confirme o limite do seu plano.')
            code = 'CLOUDINARY_FILE_SIZE_LIMIT'
            status = 413
        elif any(term in cloud_error for term in ('invalid api key', 'invalid signature', 'unauthorized', 'authentication')):
            message = ('O Cloudinary recusou a autenticação. Confira CLOUDINARY_CLOUD_NAME, '
                       'CLOUDINARY_API_KEY e CLOUDINARY_API_SECRET nas variáveis do Render.')
            code = 'CLOUDINARY_AUTH_ERROR'
            status = 502
        elif any(term in cloud_error for term in ('quota', 'credits', 'usage limit', 'rate limit', 'resource limit')):
            message = 'A conta Cloudinary atingiu um limite de uso. Confira o painel de uso e os limites da conta.'
            code = 'CLOUDINARY_USAGE_LIMIT'
            status = 502
        else:
            message = ('O Cloudinary não concluiu o envio. O erro técnico foi registrado nos logs do Render; '
                       'abra Logs no serviço e procure por “Falha no upload de imagem do portfólio”.')
            code = 'CLOUDINARY_UPLOAD_ERROR'
            status = 502
        return jsonify({'ok': False, 'error': message, 'code': code}), status


@app.post('/api/portfolio/upload-video')
def upload_portfolio_video():
    denied = require_admin()
    if denied:
        return denied
    video = request.files.get('video')
    if not video or not video.filename:
        return jsonify({'ok': False, 'error': 'Selecione um vídeo.'}), 400
    if video.mimetype not in {'video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v', 'video/ogg'}:
        return jsonify({'ok': False, 'error': 'Formato não permitido. Use MP4, MOV, WEBM ou OGG.'}), 400
    video.stream.seek(0, 2)
    video_size = video.stream.tell()
    video.stream.seek(0)
    if video_size > 100 * 1024 * 1024:
        return jsonify({'ok': False, 'error': 'O vídeo pode ter no máximo 100 MB.'}), 413
    if not _cloudinary_configured():
        return jsonify({'ok': False, 'error': 'Configure as três variáveis CLOUDINARY no Render.'}), 503
    try:
        uploader = _configure_cloudinary()
        result = uploader.upload(video, folder='geoagreste/portfolio/videos',
                                             resource_type='video',
                                             allowed_formats=['mp4', 'mov', 'webm', 'ogg', 'm4v'])
        return jsonify({'ok': True, 'url': result['secure_url'], 'public_id': result['public_id']})
    except Exception:
        logger.exception('Falha no upload de vídeo do portfólio')
        return jsonify({'ok': False, 'error': 'Falha no Cloudinary ao enviar o vídeo. Confira os limites da conta.'}), 502


def _apply_portfolio_payload(item, data, creating=False):
    title = str(data.get('title') or '').strip()
    images = data.get('images')
    if images is None:
        old_url = str(data.get('img') or data.get('image_url') or '').strip()
        images = [{'url': old_url, 'public_id': str(data.get('cloudinary_public_id') or '')}] if old_url else _portfolio_images(item) if not creating else []
    if not isinstance(images, list):
        raise ValueError('A galeria de imagens está inválida.')
    images = images[:5]
    cleaned = []
    for image in images:
        if isinstance(image, str): image = {'url': image, 'public_id': ''}
        if not isinstance(image, dict): continue
        url = str(image.get('url') or '').strip()
        if not url.startswith(('https://', 'http://')): raise ValueError('Uma das URLs de imagem é inválida.')
        cleaned.append({'url': url[:1500], 'public_id': str(image.get('public_id') or '')[:300]})
    if not title: raise ValueError('Informe o título do projeto.')
    if not cleaned: raise ValueError('Adicione pelo menos uma imagem ao projeto.')
    item.title = title[:240]
    item.summary = str(data.get('summary') or '').strip()[:12000]
    item.location = str(data.get('location') or '').strip()[:300]
    item.objective = str(data.get('objective') or '').strip()[:12000]
    item.images_json = json.dumps(cleaned, ensure_ascii=False)
    item.image_url = cleaned[0]['url']
    item.cloudinary_public_id = cleaned[0].get('public_id') or None
    item.video_url = str(data.get('video_url') or '').strip()[:1500]
    item.video_public_id = str(data.get('video_public_id') or '').strip()[:300] or None
    if item.video_url and not item.video_url.startswith(('https://', 'http://')):
        raise ValueError('Informe uma URL de vídeo válida.')


@app.post('/api/portfolio')
def create_portfolio_item():
    denied = require_admin()
    if denied: return denied
    try:
        data = request.get_json(silent=True) or {}
        next_id = (db.session.query(func.max(PortfolioItem.id)).scalar() or 0) + 1
        item = PortfolioItem(id=next_id, title='Novo projeto', image_url='')
        _apply_portfolio_payload(item, data, creating=True)
        db.session.add(item); db.session.commit()
        return jsonify({'ok': True, 'item': portfolio_to_dict(item)}), 201
    except ValueError as e:
        db.session.rollback(); return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception:
        db.session.rollback(); logger.exception('Erro em POST /api/portfolio')
        return jsonify({'ok': False, 'error': 'Não foi possível salvar o projeto no banco de dados.'}), 500


@app.put('/api/portfolio/<int:item_id>')
def update_portfolio_item(item_id):
    denied = require_admin()
    if denied: return denied
    try:
        item = db.session.get(PortfolioItem, item_id)
        if not item: return jsonify({'ok': False, 'error': 'Projeto não encontrado.'}), 404
        data = request.get_json(silent=True) or {}
        _apply_portfolio_payload(item, data)
        db.session.commit()
        return jsonify({'ok': True, 'item': portfolio_to_dict(item)})
    except ValueError as e:
        db.session.rollback(); return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception:
        db.session.rollback(); logger.exception('Erro em PUT /api/portfolio')
        return jsonify({'ok': False, 'error': 'Não foi possível atualizar o projeto.'}), 500


@app.delete('/api/portfolio/<int:item_id>')
def delete_portfolio_item(item_id):
    denied = require_admin()
    if denied: return denied
    try:
        item = db.session.get(PortfolioItem, item_id)
        if not item: return jsonify({'ok': False, 'error': 'Projeto não encontrado.'}), 404
        db.session.delete(item); db.session.commit()
        return jsonify({'ok': True})
    except Exception:
        db.session.rollback(); logger.exception('Erro em DELETE /api/portfolio')
        return jsonify({'ok': False, 'error': 'Não foi possível excluir o projeto.'}), 500

def init_db():
    with app.app_context():
        try:
            db.create_all()
            # Migração aditiva para bancos existentes, sem apagar projetos já cadastrados.
            columns = {col['name'] for col in inspect(db.engine).get_columns('portfolio_items')}
            additions = {
                'summary': 'TEXT', 'location': 'VARCHAR(300)', 'objective': 'TEXT',
                'images_json': "TEXT DEFAULT '[]'", 'video_url': "VARCHAR(1500) DEFAULT ''",
                'video_public_id': 'VARCHAR(300)'
            }
            for col_name, col_type in additions.items():
                if col_name not in columns:
                    with db.engine.begin() as conn:
                        conn.execute(text(f'ALTER TABLE portfolio_items ADD COLUMN {col_name} {col_type}'))
            logger.info("Tabelas e campos do banco de dados verificados/criados com sucesso.")
        except Exception as e:
            logger.error(f"Erro ao inicializar o banco de dados: {e}")

init_db()

with app.app_context():
    try:
        seed_default_courses()
    except Exception as e:
        logger.error(f"Erro ao semear cursos padrão: {e}")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)